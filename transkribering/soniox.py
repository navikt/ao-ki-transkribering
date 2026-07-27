"""
Soniox STT-integrasjon for sanntidsmodus.

Alternativ til lokal faster-whisper. Sender PCM til Soniox via deira Python SDK
og returnerer transkribering med innebygd høyttalardiarisering.

Krev: pip install soniox
Krev: SONIOX_API_KEY miljøvariabel
"""

import asyncio
import os
import threading
from typing import Callable

import numpy as np

SONIOX_API_KEY = os.getenv("SONIOX_API_KEY")


def _f32le_til_s16le(pcm: np.ndarray) -> bytes:
    """Konverterer float32 PCM (–1.0 … 1.0) til int16 little-endian bytes."""
    return (np.clip(pcm, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class SonioxSessjon:
    """
    Wrapper rundt Soniox real-time STT SDK.

    send_pcm(pcm)  – send ein float32-chunk frå nettlesaren
    stopp()        – signal at opptaket er avslutta (sender finalize)
    Resultat kjem via callback-funksjonen send_json.
    """

    def __init__(self, send_json: Callable, loop: asyncio.AbstractEventLoop):
        self._send_json = send_json
        self._loop = loop
        self._pcm_kø: "queue.Queue[bytes | None]" = __import__("queue").Queue()
        self._startet = threading.Event()
        self._trad = threading.Thread(target=self._kjoer, daemon=True)
        self._trad.start()
        self._startet.wait(timeout=15)

    def send_pcm(self, pcm: np.ndarray) -> None:
        self._pcm_kø.put(_f32le_til_s16le(pcm))

    def stopp(self) -> None:
        self._pcm_kø.put(None)
        self._trad.join(timeout=10)

    def _kjoer(self) -> None:
        try:
            from soniox import SonioxClient
            from soniox.types import RealtimeSTTConfig
        except ImportError:
            self._send_feil("soniox-pakken er ikkje installert. Køyr: pip install soniox")
            return

        if not SONIOX_API_KEY:
            self._send_feil("Miljøvariabelen SONIOX_API_KEY manglar.")
            return

        config = RealtimeSTTConfig(
            model="stt-rt-v4",
            language_hints=["no"],
            enable_speaker_diarization=True,
            enable_endpoint_detection=True,
            audio_format="pcm_s16le",
            sample_rate=16000,
            num_channels=1,
        )

        client = SonioxClient(api_key=SONIOX_API_KEY)
        try:
            with client.realtime.stt.connect(config=config) as session:
                self._startet.set()

                # Mottakar-tråd: les events frå Soniox og sender til WebSocket
                final_tokens: list = []

                def _motta():
                    nonlocal final_tokens
                    for event in session.receive_events():
                        if event.error_code:
                            self._send_feil(f"{event.error_code}: {event.error_message}")
                            return

                        non_final = [t for t in event.tokens if not t.is_final]
                        for t in event.tokens:
                            if t.is_final:
                                final_tokens.append(t)

                        if event.tokens:
                            alle = final_tokens + non_final
                            tekst = "".join(t.text for t in alle).strip()
                            if tekst:
                                segmenter = _bygg_segmenter(final_tokens, non_final)
                                asyncio.run_coroutine_threadsafe(
                                    self._send_json({
                                        "type": "segment",
                                        "tekst": tekst,
                                        "segmenter": segmenter,
                                    }),
                                    self._loop,
                                )

                        if event.finished:
                            return

                motta_trad = threading.Thread(target=_motta, daemon=True)
                motta_trad.start()

                # Sender-løkke: les PCM frå køen og send til Soniox
                while True:
                    chunk = self._pcm_kø.get()
                    if chunk is None:
                        try:
                            session.finalize()
                        except Exception:
                            pass
                        break
                    session.send_audio(chunk)

                motta_trad.join(timeout=30)

        except Exception as e:
            self._startet.set()
            self._send_feil(str(e))

    def _send_feil(self, melding: str) -> None:
        asyncio.run_coroutine_threadsafe(
            self._send_json({"type": "feil", "melding": melding}),
            self._loop,
        )


def _bygg_segmenter(final_tokens: list, non_final_tokens: list) -> list:
    """Bygger segmentliste frå Soniox-tokens gruppert per talar."""
    segmenter = []
    gjeldande_talar = None
    gjeldande_ord: list = []
    gjeldande_start: float = 0.0
    gjeldande_slutt: float = 0.0

    for token in final_tokens + non_final_tokens:
        talar = getattr(token, "speaker", None) or "SPEAKER_00"
        tekst = token.text
        start = getattr(token, "start_ms", 0) / 1000
        slutt = getattr(token, "end_ms", 0) / 1000

        if talar != gjeldande_talar:
            if gjeldande_ord:
                segmenter.append({
                    "taler": gjeldande_talar,
                    "start": round(gjeldande_start, 1),
                    "slutt": round(gjeldande_slutt, 1),
                    "tekst": "".join(gjeldande_ord).strip(),
                })
            gjeldande_talar = talar
            gjeldande_ord = [tekst]
            gjeldande_start = start
            gjeldande_slutt = slutt
        else:
            gjeldande_ord.append(tekst)
            gjeldande_slutt = slutt

    if gjeldande_ord:
        segmenter.append({
            "taler": gjeldande_talar,
            "start": round(gjeldande_start, 1),
            "slutt": round(gjeldande_slutt, 1),
            "tekst": "".join(gjeldande_ord).strip(),
        })

    return segmenter
