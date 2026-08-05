from fastapi import APIRouter, Response

from runtime import arbeider_klar, job_store, lokal_arbeider_aktiv

router = APIRouter()


@router.get("/isAlive", include_in_schema=False)
def is_alive():
    return {"status": "ok"}


@router.get("/isReady", include_in_schema=False)
def is_ready():
    """API is ready when job storage is reachable and any local worker is ready."""
    job_store.work_dir.mkdir(parents=True, exist_ok=True)
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
    if not lokal_arbeider_aktiv:
        return {"status": "ekstern arbeider"}
    if not arbeider_klar.is_set():
        return Response(
            content='{"status":"laster modell"}',
            status_code=503,
            media_type="application/json",
        )
    return {"status": "ok"}
