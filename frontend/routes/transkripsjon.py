import shutil
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from shared.core.runtime import job_store, jobbkø
from shared.core.settings import MODELL_ID, TRANSKRIPSJON_BACKEND, TRANSKRIPSJON_MODELL
from shared.diagnostics import event, failure, recording_id
from shared.services.transkripsjon_client import transkriber_remote
from shared.services.uploaded_audio import AudioFileError
from worker.transkribering.batch import estimert_total_s

router = APIRouter()


def _upload_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, AudioFileError):
        return "audio_invalid", str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        errors = {
            400: ("audio_rejected", "Modell-API-et avviste lydfilen. Kontroller at filen kan spilles av."),
            401: ("model_auth", "Modell-API-et mangler gyldig autentisering."),
            403: ("model_auth", "Modell-API-et mangler gyldig autentisering eller tilgang."),
            404: ("model_missing", "Transkripsjonsmodellen er ikke tilgjengelig."),
            413: ("audio_too_large", "Lydfilen eller en lyddel er for stor for modell-API-et."),
            429: ("model_busy", "Transkripsjonsmodellen har full kø. Prøv igjen om litt."),
            502: ("model_unavailable", "Transkripsjonsmodellen er ikke tilgjengelig nå. Prøv igjen senere."),
            503: ("model_unavailable", "Transkripsjonsmodellen er ikke tilgjengelig nå. Prøv igjen senere."),
            504: ("model_timeout", "Transkripsjonsmodellen brukte for lang tid. Prøv igjen."),
        }
        if status in errors:
            return errors[status]
    if isinstance(exc, httpx.TimeoutException):
        return "model_timeout", "Transkripsjonsmodellen brukte for lang tid. Prøv igjen."
    if isinstance(exc, httpx.TransportError):
        return "model_connection", "Kan ikke nå transkripsjonsmodellen. Prøv igjen senere."
    return "transcription_failed", "Transkriberingen feilet. Prøv igjen."


@router.post("/transkriber")
async def start_transkribering(
    background_tasks: BackgroundTasks,
    lydfil: UploadFile = File(...),
    n_talere: int = Form(0),
):
    """Mottar lydfil, sender til arbeiderprosess, returnerer jobb-ID.

    n_talere: 0 = auto-deteksjon, 2/3/4 = eksakt antall talere.
    """
    suffix = Path(lydfil.filename or "opptak.webm").suffix or ".webm"
    paths = job_store.create_paths(suffix)

    with paths.audio_path.open("wb") as f:
        paths.audio_path.chmod(0o600)
        shutil.copyfileobj(lydfil.file, f)

    job_store.write_queued(paths.result_path)
    event("upload.received", job_id=paths.job_id, bytes=paths.audio_path.stat().st_size)
    if TRANSKRIPSJON_BACKEND == "remote":
        background_tasks.add_task(
            _kjor_remote_transkribering,
            paths.audio_path,
            paths.result_path,
            n_talere,
        )
    else:
        jobbkø.put((paths.job_id, str(paths.audio_path), str(paths.result_path), n_talere))

    return {"jobb_id": paths.job_id}


async def _kjor_remote_transkribering(
    audio_path: Path,
    result_path: Path,
    n_talere: int,
) -> None:
    job_store.write_transcribing(
        result_path,
        model_id=TRANSKRIPSJON_MODELL,
        device="remote",
    )
    started = time.monotonic()
    token = recording_id.set(result_path.stem)
    try:
        event("upload.transcription_started", job_id=result_path.stem, bytes=audio_path.stat().st_size)
        resultat = await transkriber_remote(
            audio_path, n_talere=n_talere,
            status_callback=lambda values: job_store.update_path(result_path, values),
        )
        job_store.write_done(
            result_path,
            text=resultat.tekst,
            segments=[s.model_dump() for s in resultat.segmenter],
            warnings=resultat.advarsler,
        )
        event("upload.transcription_completed", job_id=result_path.stem,
              elapsed_s=round(time.monotonic() - started, 3), segments=len(resultat.segmenter))
    except Exception as exc:
        code, message = _upload_error(exc)
        failure("upload.transcription_failed", exc, job_id=result_path.stem, error_code=code,
                status=exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
                elapsed_s=round(time.monotonic() - started, 3))
        job_store.write_failed(result_path, message, error_code=code)
    finally:
        audio_path.unlink(missing_ok=True)
        recording_id.reset(token)


@router.get("/status/{jobb_id}")
async def sjekk_status(jobb_id: str):
    """Returnerer status, fremdrift og elapsed tid for en transkriberingsjobb."""
    if not job_store.exists(jobb_id):
        raise HTTPException(status_code=404, detail="Ukjent jobb-ID")
    data = job_store.read(jobb_id)

    svar: dict = {"jobb_id": jobb_id, "status": data["status"]}
    if data["status"] == "feil":
        svar["feilmelding"] = data["feilmelding"] if data.get("feilkode") else "Transkriberingen feilet. Prøv igjen."

    if data["status"] == "transkriberer":
        start_tid = data.get("start_tid")
        lyd_s = data.get("lyd_varighet_s")
        modell_id = data.get("modell_id", MODELL_ID)
        enhet = data.get("enhet", "cpu")
        fase = data.get("fase", "transkriberer")

        if start_tid:
            elapsed = time.time() - start_tid
            svar["elapsed_s"] = round(elapsed, 1)
            svar["fase"] = fase

            if lyd_s:
                if enhet == "remote" and "behandlet_lyd_s" in data:
                    processed = data["behandlet_lyd_s"]
                    svar["lyd_varighet_s"] = round(lyd_s, 1)
                    svar["fremdrift"] = round(0.95 * min(processed / lyd_s, 1), 3)
                    if processed:
                        svar["estimert_total_s"] = round(elapsed * lyd_s / processed, 1)
                    return svar
                estimert = estimert_total_s(modell_id, lyd_s, enhet)
                svar["estimert_total_s"] = round(estimert, 1)
                svar["lyd_varighet_s"] = round(lyd_s, 1)
                if fase == "diariserer":
                    fremdrift = 0.85 + 0.10 * min(elapsed / estimert, 1.0)
                else:
                    fremdrift = min(elapsed / estimert * 0.85, 0.84)
                svar["fremdrift"] = round(fremdrift, 3)

    return svar


@router.get("/resultat/{jobb_id}")
async def hent_resultat(jobb_id: str):
    """Returnerer ferdig transkripsjon."""
    if not job_store.exists(jobb_id):
        raise HTTPException(status_code=404, detail="Ukjent jobb-ID")
    data = job_store.read(jobb_id)
    if data["status"] == "feil":
        raise HTTPException(status_code=500, detail=data.get("feilmelding", "Ukjent feil"))
    if data["status"] != "ferdig":
        raise HTTPException(status_code=409, detail=f"Jobb ikke ferdig (status: {data['status']})")
    svar = {"jobb_id": jobb_id, "tekst": data["tekst"], "segmenter": data["segmenter"]}
    if data.get("advarsler"):
        svar["advarsler"] = data["advarsler"]
    return svar
