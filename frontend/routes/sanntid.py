import asyncio
import json
import logging
import time
from contextlib import suppress
from uuid import uuid4

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.core.settings import TRANSKRIPSJON_BACKEND
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


async def _heartbeat(websocket):
    while True:
        await asyncio.sleep(15)
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
    heartbeat = asyncio.create_task(_heartbeat(websocket))
    try:
        if TRANSKRIPSJON_BACKEND == "remote":
            await _sanntid_remote(websocket)
        else:
            await _sanntid_lokal(websocket)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError, RuntimeError, WebSocketDisconnect, OSError):
            await heartbeat


async def _sanntid_lokal(websocket: WebSocket) -> None:
    """Startup-selected local ASR and diarisation."""
    try:
        await asyncio.to_thread(hent_sanntidsmodell)
    except Exception as e:
        await websocket.send_json({"type": "feil", "melding": str(e)})
        await websocket.close()
        return

    buf = VadBuffer(preserve_audio=True)
    transkriber_kø: asyncio.Queue = asyncio.Queue(maxsize=4)
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

    worker_task = asyncio.create_task(transkriber_worker())

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
    transkriber_kø: asyncio.Queue = asyncio.Queue(maxsize=4)
    samler = SegmentSamler()
    session_id = str(uuid4())

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

    worker_task = asyncio.create_task(transkriber_worker())

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
