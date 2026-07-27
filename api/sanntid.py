import asyncio
import json

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from settings import STT_BACKEND
from transkribering.sanntid import (
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

    Backend vel med STT_BACKEND=lokal (standard) eller STT_BACKEND=soniox.
    """
    await websocket.accept()

    if STT_BACKEND == "soniox":
        await _sanntid_soniox(websocket)
    else:
        await _sanntid_lokal(websocket)


async def _sanntid_lokal(websocket: WebSocket) -> None:
    """Lokal faster-whisper + diarisering."""
    try:
        hent_fw_modell()
    except FileNotFoundError as e:
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


async def _sanntid_soniox(websocket: WebSocket) -> None:
    """Soniox cloud STT med innebygd diarisering."""
    from transkribering.soniox import SonioxSessjon

    loop = asyncio.get_event_loop()

    async def send_json(data: dict):
        try:
            await websocket.send_json(data)
        except Exception:
            pass

    sessjon = SonioxSessjon(send_json=send_json, loop=loop)

    try:
        while True:
            melding = await websocket.receive()
            if "text" in melding:
                data = json.loads(melding["text"])
                if data.get("type") == "stopp":
                    break
            elif "bytes" in melding:
                raw = melding["bytes"]
                if not raw:
                    continue
                n_samples = len(raw) // 4
                if n_samples == 0:
                    continue
                samples = np.frombuffer(raw, dtype="<f4").copy()
                sessjon.send_pcm(samples)
    except WebSocketDisconnect:
        pass
    finally:
        sessjon.stopp()
