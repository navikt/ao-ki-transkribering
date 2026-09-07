from fastapi import APIRouter, Response

from shared.core.runtime import arbeider_klar, job_store, lokal_arbeider_aktiv
from shared.core.settings import AI_PROXY_URL, LLM_MODELL, MODELL_ID, TRANSKRIPSJON_BACKEND, TRANSKRIPSJON_SERVICE_URL
from worker.transkribering.konstanter import STILLHET_TERSKEL_S, MAKS_BUFFER_S, ENERGI_TERSKEL
from worker.transkribering.diarisering import _ECAPA_KILDE, _VINDU_S, _RATE
from worker.transkribering.sanntid import _CT2_MODELL_STI

router = APIRouter()


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
            "modell": LLM_MODELL,
            "url": AI_PROXY_URL,
        },
    }
