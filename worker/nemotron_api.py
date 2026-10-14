"""OpenAI-compatible audio endpoint for the shared Nemotron RNNT implementation."""

import asyncio
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import UUID

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from k8s.model_security import ModelAccessMiddleware
from worker.transkribering.nemotron import hent_nemotron_modell
from worker.transkribering.nemotron_diarization import NemotronDiarizer, SpeechSessions
from worker.transkribering.request_queue import QueueFull, SpeechRequestQueue

MODEL_NAME = os.getenv("SPEECH_MODEL_NAME", "nemotron-3.5-asr-streaming-0.6b")
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
    backend = os.getenv("SPEECH_ASR_BACKEND", "nemotron")
    if backend == "whisper":
        from worker.transkribering.whisper_api_model import WhisperTranskriberer
        from worker.transkribering.whisper_batcher import BatchedWhisper
        model = await asyncio.to_thread(WhisperTranskriberer)
        app.state.model = BatchedWhisper(model, batch_size=int(os.getenv("SPEECH_BATCH_SIZE", "2")),
                                        wait_seconds=float(os.getenv("SPEECH_BATCH_WAIT_SECONDS", "0.1")))
    elif backend == "faster-whisper":
        from worker.transkribering.ctranslate2_api_model import CTranslate2Transkriberer
        app.state.model = await asyncio.to_thread(CTranslate2Transkriberer)
    else:
        app.state.model = await asyncio.to_thread(hent_nemotron_modell)
    diar_path = os.getenv("NEMOTRON_DIAR_MODELL", "")
    app.state.diarizer = await asyncio.to_thread(NemotronDiarizer, diar_path, app.state.model.enhet) if diar_path else None
    app.state.sessions = SpeechSessions()
    app.state.requests = SpeechRequestQueue(capacity=32,
                                           concurrency=32 if hasattr(app.state.model, "batch_size") else
                                           getattr(app.state.model, "inference_concurrency", 1))

    async def cleanup():
        while True:
            await asyncio.sleep(30)
            await asyncio.to_thread(app.state.sessions.purge)

    task = asyncio.create_task(cleanup())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        app.state.sessions.records.clear()
        if hasattr(app.state.model, "close"):
            await asyncio.to_thread(app.state.model.close)


app = FastAPI(title="Speech and diarization model API", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(ModelAccessMiddleware)


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "diarization": app.state.diarizer is not None,
            "batch_size": getattr(app.state.model, "batch_size", 1),
            "runtime": os.getenv("SPEECH_ASR_BACKEND", "nemotron"),
            "inference_concurrency": getattr(app.state.model, "inference_concurrency", 1),
            "batch_counts": getattr(app.state.model, "batch_counts", {}),
            "outstanding_requests": app.state.requests.pending}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "nvidia"}]}


@app.post("/v1/audio/transcriptions")
async def transcribe(request: Request, file: UploadFile = File(...), model: str = Form(...),
                     language: str = Form("no"), response_format: str = Form("json"),
                     session_id: str | None = Form(None), chunk_start: float = Form(0, ge=0, le=7200)):
    if model != MODEL_NAME:
        raise HTTPException(404, "Unknown speech model")
    if language not in {"no", "nb", "nb-NO"}:
        raise HTTPException(400, "This deployment is configured for Norwegian Bokmal")
    if response_format not in {"json", "verbose_json", "text"}:
        raise HTTPException(400, "Unsupported response format")
    if session_id:
        try:
            session_id = str(UUID(session_id))
        except ValueError:
            raise HTTPException(400, "Invalid recording session") from None
    async def process():
        if await request.is_disconnected():
            raise HTTPException(499, "Client disconnected while waiting for speech inference")
        return await transcribe_audio(file, response_format, session_id, chunk_start)

    try:
        return await app.state.requests.run(process)
    except QueueFull:
        raise HTTPException(429, "Speech queue is full; retry later", headers={"Retry-After": "10"}) from None
    finally:
        await file.close()


async def transcribe_audio(file, response_format, session_id, chunk_start):
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
        if session_id and (len(pcm) > 32 * 16000 or chunk_start + len(pcm) / 16000 > MAX_AUDIO_SECONDS):
            raise HTTPException(413, "Recording chunk or session exceeds its limit")
        if app.state.diarizer is not None:
            try:
                result = await asyncio.to_thread(app.state.sessions.transcribe, pcm, app.state.model,
                                                app.state.diarizer, session_id, chunk_start)
            except OverflowError:
                raise HTTPException(429, "Too many active recordings") from None
            except ValueError:
                raise HTTPException(409, "Recording chunk is out of order") from None
        else:
            result = await asyncio.to_thread(app.state.model.transkriber, pcm)
    if response_format == "text":
        return PlainTextResponse(result["text"])
    if response_format == "json":
        return {"text": result["text"]}
    if "segments" in result:
        return result
    segments = [
        {"id": i, "text": chunk["text"], "start": chunk["timestamp"][0], "end": chunk["timestamp"][1]}
        for i, chunk in enumerate(result["chunks"])
    ]
    return {"text": result["text"], "language": "norwegian", "duration": len(pcm) / 16000,
            "segments": segments}
