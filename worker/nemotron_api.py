"""OpenAI-compatible audio endpoint for the shared Nemotron RNNT implementation."""

import asyncio
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from k8s.model_security import ModelAccessMiddleware
from worker.transkribering.nemotron import hent_nemotron_modell

MODEL_NAME = "nemotron-3.5-asr-streaming-0.6b"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_AUDIO_SECONDS = 7200


def decode_audio(path: Path) -> np.ndarray:
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", str(path), "-vn", "-ac", "1",
             "-ar", "16000", "-t", str(MAX_AUDIO_SECONDS + 1), "-f", "f32le", "pipe:1"],
            capture_output=True, check=True, timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(400, "Audio could not be decoded") from exc
    pcm = np.frombuffer(result.stdout, dtype="<f4")
    if len(pcm) > MAX_AUDIO_SECONDS * 16000:
        raise HTTPException(413, "Audio exceeds the two-hour limit")
    if not len(pcm) or not np.isfinite(pcm).all():
        raise HTTPException(400, "Audio is empty or invalid")
    return pcm


@asynccontextmanager
async def lifespan(app):
    app.state.model = await asyncio.to_thread(hent_nemotron_modell)
    app.state.busy = False
    yield


app = FastAPI(title="Nemotron speech model", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(ModelAccessMiddleware)


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "nvidia"}]}


@app.post("/v1/audio/transcriptions")
async def transcribe(file: UploadFile = File(...), model: str = Form(...),
                     language: str = Form("no"), response_format: str = Form("json")):
    if model != MODEL_NAME:
        raise HTTPException(404, "Unknown speech model")
    if language not in {"no", "nb", "nb-NO"}:
        raise HTTPException(400, "This deployment is configured for Norwegian Bokmal")
    if response_format not in {"json", "verbose_json", "text"}:
        raise HTTPException(400, "Unsupported response format")
    # Reject excess work instead of building an unbounded queue on a single GPU.
    if app.state.busy:
        raise HTTPException(429, "Speech model is busy; retry later")
    app.state.busy = True
    try:
        with tempfile.TemporaryDirectory(prefix="nemotron-") as directory:
            path = Path(directory) / "audio"
            size = 0
            with path.open("wb") as target:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "Audio upload is too large")
                    target.write(chunk)
            pcm = await asyncio.to_thread(decode_audio, path)
            result = await asyncio.to_thread(app.state.model.transkriber, pcm)
        if response_format == "text":
            return PlainTextResponse(result["text"])
        if response_format == "json":
            return {"text": result["text"]}
        segments = [
            {"id": i, "text": chunk["text"], "start": chunk["timestamp"][0], "end": chunk["timestamp"][1]}
            for i, chunk in enumerate(result["chunks"])
        ]
        return {"text": result["text"], "language": "norwegian", "duration": len(pcm) / 16000,
                "segments": segments}
    finally:
        app.state.busy = False
        await file.close()
