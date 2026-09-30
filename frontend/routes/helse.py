import httpx
from fastapi import APIRouter, Response

from shared.core.runtime import arbeider_klar, job_store, lokal_arbeider_aktiv
from shared.core.settings import (
    AI_PROXY_URL,
    LLM_BACKEND,
    LLM_MODELL,
    MODELL_ID,
    OLLAMA_URL,
    TRANSKRIPSJON_BACKEND,
    TRANSKRIPSJON_MODELL,
    TRANSKRIPSJON_SERVICE_URL,
)
from shared.services.llm_proxy_client import LlmStatus, hent_status as hent_llm_status
from worker.transkribering.konstanter import STILLHET_TERSKEL_S, MAKS_BUFFER_S, ENERGI_TERSKEL
from worker.transkribering.diarisering import _ECAPA_KILDE, _VINDU_S, _RATE
from worker.transkribering.sanntid import _CT2_MODELL_STI

router = APIRouter()


def _har_modell(status: LlmStatus | None, modell: str) -> bool:
    return bool(status and any(item.id == modell for item in status.modeller))


def _model_api_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.ConnectError):
        return "unavailable", "Kan ikke nå modell-API-et"
    if isinstance(exc, httpx.TimeoutException):
        return "unavailable", "Modell-API-et svarer ikke innen fristen"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "unauthorized", "Modell-API-et mangler gyldig autentisering"
        if status in {429, 503}:
            return "unavailable", "Modellkapasitet er ikke tilgjengelig nå"
        return "unavailable", f"Modell-API-et svarte med HTTP {status}"
    return "unavailable", "Modellstatus kunne ikke sjekkes"


@router.get("/isAlive", include_in_schema=False)
def is_alive():
    return {"status": "ok"}


@router.get("/isReady", include_in_schema=False)
def is_ready():
    """API is ready when job storage is reachable and any local worker is ready."""
    job_store.work_dir.mkdir(parents=True, exist_ok=True)
    if TRANSKRIPSJON_BACKEND == "remote":
        return {
            "status": "ok",
            "transkripsjon_backend": "remote",
            "transkripsjon_service_url": TRANSKRIPSJON_SERVICE_URL,
        }
    if lokal_arbeider_aktiv and not arbeider_klar.is_set():
        return Response(
            content='{"status":"laster modell"}',
            status_code=503,
            media_type="application/json",
        )
    return {"status": "ok"}


@router.get("/worker/isReady", include_in_schema=False)
def worker_is_ready():
    """Local model worker readiness. Useful until the worker moves out of process."""
    if TRANSKRIPSJON_BACKEND == "remote":
        return {
            "status": "ekstern arbeider",
            "transkripsjon_service_url": TRANSKRIPSJON_SERVICE_URL,
        }
    if not lokal_arbeider_aktiv:
        return {"status": "ekstern arbeider"}
    if not arbeider_klar.is_set():
        return Response(
            content='{"status":"laster modell"}',
            status_code=503,
            media_type="application/json",
        )
    return {"status": "ok"}


@router.get("/system/info")
def system_info():
    """Teknisk konfigurasjon for visning i UI."""
    sanntid_modell = MODELL_ID if TRANSKRIPSJON_BACKEND == "remote" else _CT2_MODELL_STI
    return {
        "asr": {
            "batch_modell": MODELL_ID,
            "transkripsjon_modell": TRANSKRIPSJON_MODELL,
            "sanntid_modell": sanntid_modell,
            "backend": TRANSKRIPSJON_BACKEND,
        },
        "diarisering": {
            "modell": _ECAPA_KILDE,
            "vindu_s": _VINDU_S,
            "rate": _RATE,
        },
        "vad": {
            "stillhet_s": STILLHET_TERSKEL_S,
            "maks_buffer_s": MAKS_BUFFER_S,
            "energi_terskel": ENERGI_TERSKEL,
        },
        "llm": {
            "backend": LLM_BACKEND,
            "modell": LLM_MODELL,
            "url": OLLAMA_URL if LLM_BACKEND == "ollama" else AI_PROXY_URL,
        },
    }


@router.get("/system/status")
async def system_status():
    """Samlet status for API, transkripsjon, LLM og diarisering."""
    status = {
        "api": {"status": "ok"},
        "transcription": {
            "status": "ok",
            "backend": TRANSKRIPSJON_BACKEND,
            "model": TRANSKRIPSJON_MODELL,
        },
        "llm": {
            "status": "unknown",
            "backend": LLM_BACKEND,
            "model": LLM_MODELL,
        },
        "diarization": {
            "status": "ok",
            "model": _ECAPA_KILDE,
        },
    }

    try:
        job_store.work_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        status["api"] = {"status": "unavailable", "message": "Jobblageret kan ikke skrives til"}

    if TRANSKRIPSJON_BACKEND == "local":
        if lokal_arbeider_aktiv and not arbeider_klar.is_set():
            status["transcription"]["status"] = "starting"
            status["transcription"]["message"] = "Lokal transkripsjonsmodell starter"
    else:
        status["transcription"]["service_url"] = TRANSKRIPSJON_SERVICE_URL

    llm_status: LlmStatus | None = None
    model_error: Exception | None = None
    try:
        llm_status = await hent_llm_status()
    except Exception as exc:
        model_error = exc

    if model_error:
        model_status, message = _model_api_error(model_error)
        status["llm"]["status"] = model_status
        status["llm"]["message"] = message
        if TRANSKRIPSJON_BACKEND == "remote":
            status["transcription"]["status"] = model_status
            status["transcription"]["message"] = message
    else:
        modeller = [modell.id for modell in llm_status.modeller] if llm_status else []
        status["llm"]["available_models"] = modeller
        status["transcription"]["available_models"] = modeller

        if _har_modell(llm_status, LLM_MODELL):
            status["llm"]["status"] = "ok"
        else:
            status["llm"]["status"] = "missing_model"
            status["llm"]["message"] = f"Konfigurert LLM-modell mangler: {LLM_MODELL}"

        if TRANSKRIPSJON_BACKEND == "remote" and not _har_modell(llm_status, TRANSKRIPSJON_MODELL):
            status["transcription"]["status"] = "missing_model"
            status["transcription"]["message"] = (
                f"Konfigurert transkripsjonsmodell mangler: {TRANSKRIPSJON_MODELL}"
            )

    if TRANSKRIPSJON_BACKEND == "remote":
        status["diarization"]["status"] = "degraded"
        status["diarization"]["message"] = "Ekstern transkripsjon kan ha begrenset talerskille"

    return status
