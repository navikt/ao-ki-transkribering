from fastapi import APIRouter, Response

from runtime import arbeider_klar

router = APIRouter()


@router.get("/isAlive", include_in_schema=False)
def is_alive():
    return {"status": "ok"}


@router.get("/isReady", include_in_schema=False)
def is_ready():
    """Klar når arbeiderprosessen er startet og modellen er lastet."""
    if not arbeider_klar.is_set():
        return Response(
            content='{"status":"laster modell"}',
            status_code=503,
            media_type="application/json",
        )
    return {"status": "ok"}
