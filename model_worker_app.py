import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile

from settings import MODELL_ID
from transkribering.batch import LokalBatchTranskriberer

app = FastAPI(title="NB-Whisper model worker")

_transkriberer: LokalBatchTranskriberer | None = None


def hent_transkriberer() -> LokalBatchTranskriberer:
    global _transkriberer
    if _transkriberer is None:
        _transkriberer = LokalBatchTranskriberer(MODELL_ID)
    return _transkriberer


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
def ready():
    if _transkriberer is None:
        return {"status": "modell ikke lastet"}
    return {
        "status": "ok",
        "modell": _transkriberer.modell_id,
        "enhet": _transkriberer.enhet,
    }


@app.post("/transkriber")
async def transkriber(
    lydfil: UploadFile = File(...),
    n_talere: int = Form(0),
):
    suffix = Path(lydfil.filename or "opptak.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_path = Path(tmp.name)
        shutil.copyfileobj(lydfil.file, tmp)

    try:
        return hent_transkriberer().transkriber(audio_path, n_talere=n_talere)
    finally:
        audio_path.unlink(missing_ok=True)
