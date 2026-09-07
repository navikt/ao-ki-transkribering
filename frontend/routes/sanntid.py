import asyncio
import json

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.core.settings import TRANSKRIPSJON_BACKEND
from shared.services.transkripsjon_client import transkriber_remote_pcm
from worker.transkribering.sanntid import (
    VadBuffer,
    hent_fw_modell,
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
    """Lokal faster-whisper + diarisering."""
    try:
        hent_fw_modell()
    except (FileNotFoundError, ImportError) as e:
        await websocket.send_json({"type": "feil", "melding": str(e)})
        await websocket.close()
        return

    buf = VadBuffer()
    transkriber_kø: asyncio.Queue = asyncio.Queue(maxsize=4)
    prototyper_state: list[np.ndarray | None] = [None]

    async def transkriber_worker():
        while True:
            pcm = await transkriber_kø.get()
            if pcm is None:
                break
            resultat, ny_proto = await asyncio.to_thread(
                transkriber_pcm, pcm, prototyper_state[0]
            )
            prototyper_state[0] = ny_proto
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

    async def send_til_whisper(pcm: np.ndarray):
        await transkriber_kø.put(pcm)

    try:
        while True:
            melding = await websocket.receive()
            if "text" in melding:
                data = json.loads(melding["text"])
                if data.get("type") == "stopp":
                    rest = buf.flush_alt()
                    if rest is not None:
                        await send_til_whisper(rest)
                    break
            elif "bytes" in melding:
                raw = melding["bytes"]
                if not raw:
                    continue
                n_samples = len(raw) // 4
                if n_samples == 0:
                    continue
                samples = np.frombuffer(raw, dtype="<f4").copy()
                pcm_klar = buf.legg_til(samples)
                if pcm_klar is not None:
                    await send_til_whisper(pcm_klar)
    except WebSocketDisconnect:
        pass
    finally:
        await transkriber_kø.put(None)
        await worker_task


async def _sanntid_remote(websocket: WebSocket) -> None:
    """Proxy-basert sanntid: lokal VAD, ekstern transkribering per chunk."""
    buf = VadBuffer()
    transkriber_kø: asyncio.Queue = asyncio.Queue(maxsize=4)

    async def transkriber_worker():
        while True:
            pcm = await transkriber_kø.get()
            if pcm is None:
                break
            try:
                resultat = await transkriber_remote_pcm(pcm)
            except Exception as exc:
                await websocket.send_json({"type": "feil", "melding": f"Ekstern transkribering feilet: {exc}"})
                transkriber_kø.task_done()
                continue

            if resultat.tekst.strip():
                segmenter = [s.model_dump() for s in resultat.segmenter]
                if not segmenter:
                    segmenter = [{"start": 0.0, "slutt": 0.0, "tekst": resultat.tekst, "taler": "SPEAKER_00"}]
                for seg in segmenter:
                    seg.setdefault("taler", "SPEAKER_00")
                try:
                    await websocket.send_json(
                        {
                            "type": "segment",
                            "tekst": resultat.tekst,
                            "segmenter": segmenter,
                        }
                    )
                except Exception:
                    pass
            transkriber_kø.task_done()

    worker_task = asyncio.create_task(transkriber_worker())

    async def send_til_remote(pcm: np.ndarray):
        await transkriber_kø.put(pcm)

    try:
        while True:
            melding = await websocket.receive()
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
                pcm_klar = buf.legg_til(samples)
                if pcm_klar is not None:
                    await send_til_remote(pcm_klar)
    except WebSocketDisconnect:
        pass
    finally:
        await transkriber_kø.put(None)
        await worker_task
