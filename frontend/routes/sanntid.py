import asyncio
import json

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
    if TRANSKRIPSJON_BACKEND == "remote":
        await _sanntid_remote(websocket)
        return
    await _sanntid_lokal(websocket)


async def _sanntid_lokal(websocket: WebSocket) -> None:
    """Startup-selected local ASR and diarisation."""
    try:
        await asyncio.to_thread(hent_sanntidsmodell)
    except Exception as e:
        await websocket.send_json({"type": "feil", "melding": str(e)})
        await websocket.close()
        return

    buf = VadBuffer()
    transkriber_kø: asyncio.Queue = asyncio.Queue(maxsize=4)
    prototyper_state: list[np.ndarray | None] = [None]
    samler = SegmentSamler()

    async def transkriber_worker():
        while True:
            chunk = await transkriber_kø.get()
            if chunk is None:
                break
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
    buf = VadBuffer()
    transkriber_kø: asyncio.Queue = asyncio.Queue(maxsize=4)
    samler = SegmentSamler()

    async def transkriber_worker():
        while True:
            chunk = await transkriber_kø.get()
            if chunk is None:
                break
            try:
                resultat = await transkriber_remote_pcm(chunk.pcm)
            except Exception as exc:
                samler.legg_til(None, chunk)
                await websocket.send_json({"type": "feil", "melding": f"Ekstern transkribering feilet: {exc}"})
                transkriber_kø.task_done()
                continue

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
