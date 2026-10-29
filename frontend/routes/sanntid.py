import asyncio
import json
import logging
import time
from contextlib import suppress
from uuid import UUID, uuid4
from typing import Literal

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from shared.core.settings import TRANSKRIPSJON_BACKEND
from shared.diagnostics import event, failure, recording_id
from frontend.recording_diagnostics import RecordingSocket, observe_task
from shared.services.transkripsjon_client import transkriber_remote_pcm
from worker.transkribering.sanntid import (
    VadBuffer,
    SegmentSamler,
    LydChunk,
    hent_sanntidsmodell,
    transkriber_pcm,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class RecordingCloseReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recording_id: UUID
    code: int = Field(ge=1000, le=4999)
    elapsed_s: float = Field(ge=0, le=604800, allow_inf_nan=False)
    buffered_bytes: int = Field(ge=0, le=2**53)
    online: bool
    visibility: Literal["visible", "hidden", "unknown"]
    audio_state: Literal["running", "suspended", "closed", "interrupted", "unknown"]
    was_clean: bool | None = None
    last_message_age_s: float | None = Field(default=None, ge=0, le=604800, allow_inf_nan=False)
    last_heartbeat_age_s: float | None = Field(default=None, ge=0, le=604800, allow_inf_nan=False)
    heartbeat_count: int = Field(default=0, ge=0, le=1000000)


@router.post("/diagnostics/recording-close", status_code=204)
async def recording_close_report(report: RecordingCloseReport):
    # Client-reported metadata is diagnostic evidence, not a trusted cause of failure.
    event("recording.browser_closed", **report.model_dump(mode="json"))


class RecordingCaptureReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recording_id: UUID
    kind: Literal["visibility_changed", "audio_state_changed", "track_changed",
                  "wake_lock_changed", "capture_interrupted", "capture_recovered",
                  "resume_failed", "capture_stopped"]
    elapsed_s: float = Field(ge=0, le=604800, allow_inf_nan=False)
    duration_s: float = Field(ge=0, le=604800, allow_inf_nan=False)
    visibility: Literal["visible", "hidden"]
    audio_state: Literal["running", "suspended", "closed", "interrupted", "unknown"]
    wake_lock: Literal["held", "released", "unavailable", "unsupported"]
    track_state: Literal["live", "muted", "ended"]
    reason: Literal["track_ended", "track_muted", "audio_suspended", "clock_gap", "no_frames"] | None


@router.post("/diagnostics/recording-capture", status_code=204)
async def recording_capture_report(report: RecordingCaptureReport):
    # Browser evidence cannot prove that missing audio was actually spoken.
    event("recording.browser_capture", **report.model_dump(mode="json"))


async def _heartbeat(websocket):
    while True:
        expected = time.monotonic() + 15
        await asyncio.sleep(15)
        lag = time.monotonic() - expected
        if lag >= 1:
            event("recording.event_loop_delayed", delay_s=round(lag, 3))
        await websocket.send_json({"type": "heartbeat"})


@router.websocket("/ws/sanntid")
async def sanntid_ws(websocket: WebSocket):
    """
    WebSocket-endpoint for sanntidstranskribering.

    Protokoll:
      Client -> Server: binaer melding = raw float32 LE PCM, 16 kHz, mono
      Client -> Server: JSON {"type": "stopp"}  (avslutt og flush)
      Server -> Client: JSON {"type": "segment", "tekst": "...", "segmenter": [{..., "taler": "SPEAKER_XX"}]}
    """
    await websocket.accept()
    websocket = RecordingSocket(websocket)
    token = recording_id.set(str(uuid4()))
    event("recording.opened", backend=TRANSKRIPSJON_BACKEND)
    heartbeat = observe_task(asyncio.create_task(_heartbeat(websocket)), "heartbeat")
    try:
        await websocket.send_json({"type": "session", "recording_id": recording_id.get()})
        if TRANSKRIPSJON_BACKEND == "remote":
            await _sanntid_remote(websocket)
        else:
            await _sanntid_lokal(websocket)
    except asyncio.CancelledError:
        event("recording.cancelled", **websocket.snapshot())
        raise
    except Exception as exc:
        failure("recording.handler_failed", exc, **websocket.snapshot())
        raise
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await heartbeat
        event("recording.ended", **websocket.snapshot())
        recording_id.reset(token)


async def _sanntid_lokal(websocket: WebSocket) -> None:
    """Startup-selected local ASR and diarisation."""
    try:
        await asyncio.to_thread(hent_sanntidsmodell)
    except Exception as e:
        await websocket.send_json({"type": "feil", "melding": str(e)})
        await websocket.close()
        return

    buf = VadBuffer(preserve_audio=True)
    transkriber_kø: asyncio.Queue = websocket.queue if isinstance(websocket, RecordingSocket) else asyncio.Queue(maxsize=4)
    prototyper_state: list[np.ndarray | None] = [None]
    samler = SegmentSamler()

    async def transkriber_worker():
        while True:
            chunk = await transkriber_kø.get()
            if chunk is None:
                break
            started = time.monotonic()
            logger.info("Audio chunk start=%.2fs duration=%.2fs rms=%.5f backend=local",
                        chunk.start_s, len(chunk.pcm) / 16000,
                        float(np.sqrt(np.mean(chunk.pcm ** 2))))
            try:
                resultat, ny_proto = await asyncio.to_thread(
                    transkriber_pcm, chunk.pcm, prototyper_state[0]
                )
            except Exception as exc:
                failure("recording.inference_failed", exc, backend="local", chunk_start_s=chunk.start_s)
                samler.legg_til(None, chunk)
                try:
                    await websocket.send_json({"type": "feil", "melding": f"Transkribering feilet: {exc}"})
                except Exception:
                    pass
                transkriber_kø.task_done()
                continue
            prototyper_state[0] = ny_proto
            resultat = samler.legg_til(resultat, chunk)
            logger.info("Audio result start=%.2fs elapsed=%.2fs characters=%d speakers=%d",
                        chunk.start_s, time.monotonic() - started,
                        len(resultat.get("tekst", "")) if resultat else 0,
                        len({s.get("taler") for s in resultat.get("segmenter", [])}) if resultat else 0)
            if resultat and resultat.get("tekst"):
                try:
                    await websocket.send_json({
                        "type": "segment",
                        "tekst": resultat["tekst"],
                        "segmenter": resultat["segmenter"],
                    })
                except Exception:
                    pass
            transkriber_kø.task_done()

    worker_task = observe_task(asyncio.create_task(transkriber_worker()), "local_worker")

    async def send_til_modell(chunk: LydChunk):
        await transkriber_kø.put(chunk)

    try:
        while True:
            melding = await websocket.receive()
            if melding.get("type") == "websocket.disconnect":
                break
            if "text" in melding:
                data = json.loads(melding["text"])
                if data.get("type") == "stopp":
                    if isinstance(websocket, RecordingSocket):
                        websocket.stop_requested = True
                        event("recording.stop_requested", **websocket.snapshot())
                    rest = buf.flush_alt()
                    if rest is not None:
                        await send_til_modell(rest)
                    break
            elif "bytes" in melding:
                raw = melding["bytes"]
                if not raw:
                    continue
                n_samples = len(raw) // 4
                if n_samples == 0:
                    continue
                samples = np.frombuffer(raw, dtype="<f4").copy()
                for chunk in buf.legg_til(samples):
                    await send_til_modell(chunk)
    except WebSocketDisconnect:
        pass
    finally:
        await transkriber_kø.put(None)
        await worker_task
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


async def _sanntid_remote(websocket: WebSocket) -> None:
    """Proxy-basert sanntid: lokal VAD, ekstern transkribering per chunk."""
    # Quiet microphones must not lose whole chunks before ASR can inspect them.
    buf = VadBuffer(preserve_audio=True)
    transkriber_kø: asyncio.Queue = websocket.queue if isinstance(websocket, RecordingSocket) else asyncio.Queue(maxsize=4)
    samler = SegmentSamler()
    session_id = recording_id.get() or str(uuid4())

    async def transkriber_worker():
        while True:
            chunk = await transkriber_kø.get()
            if chunk is None:
                break
            started = time.monotonic()
            logger.info("Audio chunk start=%.2fs duration=%.2fs rms=%.5f backend=remote",
                        chunk.start_s, len(chunk.pcm) / 16000,
                        float(np.sqrt(np.mean(chunk.pcm ** 2))))
            try:
                resultat = await transkriber_remote_pcm(chunk.pcm, session_id=session_id,
                                                       chunk_start=chunk.start_s)
            except Exception as exc:
                failure("recording.inference_failed", exc, backend="remote", chunk_start_s=chunk.start_s)
                samler.legg_til(None, chunk)
                # Log only error classes/status, never audio, transcripts or credentials.
                response = getattr(exc, "response", None)
                logger.warning("Live transcription failed: %s status=%s", type(exc).__name__,
                               getattr(response, "status_code", None))
                with suppress(RuntimeError, WebSocketDisconnect, OSError):
                    await websocket.send_json({"type": "feil", "melding":
                        "Transkripsjonen feilet etter nye forsok. Opptaket er stoppet; teksten hittil er beholdt."})
                transkriber_kø.task_done()
                continue

            logger.info("Audio result start=%.2fs elapsed=%.2fs characters=%d",
                        chunk.start_s, time.monotonic() - started, len(resultat.tekst))
            if resultat.tekst.strip():
                segmenter = [s.model_dump() for s in resultat.segmenter]
                if not segmenter:
                    segmenter = [{"start": 0.0, "slutt": len(chunk.pcm) / 16000,
                                  "tekst": resultat.tekst, "taler": "SPEAKER_00"}]
                for seg in segmenter:
                    seg.setdefault("taler", "SPEAKER_00")
                samlet = samler.legg_til({"tekst": resultat.tekst, "segmenter": segmenter}, chunk)
                if not samlet:
                    transkriber_kø.task_done()
                    continue
                try:
                    await websocket.send_json(
                        {
                            "type": "segment",
                            "tekst": samlet["tekst"],
                            "segmenter": samlet["segmenter"],
                        }
                    )
                except Exception:
                    pass
            else:
                samler.legg_til(None, chunk)
            transkriber_kø.task_done()

    worker_task = observe_task(asyncio.create_task(transkriber_worker()), "remote_worker")

    async def send_til_remote(chunk: LydChunk):
        await transkriber_kø.put(chunk)

    try:
        while True:
            melding = await websocket.receive()
            if melding.get("type") == "websocket.disconnect":
                break
            if "text" in melding:
                data = json.loads(melding["text"])
                if data.get("type") == "stopp":
                    if isinstance(websocket, RecordingSocket):
                        websocket.stop_requested = True
                        event("recording.stop_requested", **websocket.snapshot())
                    rest = buf.flush_alt()
                    if rest is not None:
                        await send_til_remote(rest)
                    break
            elif "bytes" in melding:
                raw = melding["bytes"]
                if not raw:
                    continue
                n_samples = len(raw) // 4
                if n_samples == 0:
                    continue
                samples = np.frombuffer(raw, dtype="<f4").copy()
                for chunk in buf.legg_til(samples):
                    await send_til_remote(chunk)
    except WebSocketDisconnect:
        pass
    finally:
        await transkriber_kø.put(None)
        await worker_task
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
