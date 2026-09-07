import io
import wave
from pathlib import Path

import httpx
import numpy as np

from shared.contracts.transcription import TranskripsjonSvar
from shared.core.settings import MODELL_ID, TRANSKRIPSJON_API_KEY, TRANSKRIPSJON_API_PATH, TRANSKRIPSJON_SERVICE_URL

_OPENAI_AUDIO_PATH = "/v1/audio/transcriptions"


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if TRANSKRIPSJON_API_KEY:
        headers["Authorization"] = f"Bearer {TRANSKRIPSJON_API_KEY}"
    return headers


def _is_openai_audio_path(path: str) -> bool:
    return path.rstrip("/").endswith(_OPENAI_AUDIO_PATH)


def _map_openai_segments(segments: list[dict]) -> list[dict]:
    mapped = []
    for seg in segments:
        tekst = str(seg.get("text", "")).strip()
        if not tekst:
            continue
        mapped.append(
            {
                "start": float(seg.get("start", 0.0)),
                "slutt": float(seg.get("end", 0.0)),
                "tekst": tekst,
                "taler": "SPEAKER_00",
            }
        )
    return mapped


def _normalize_transkripsjon_payload(payload: dict) -> TranskripsjonSvar:
    if "tekst" in payload and "segmenter" in payload:
        return TranskripsjonSvar.model_validate(payload)

    text = str(payload.get("text", "")).strip()
    raw_segments = payload.get("segments")
    if isinstance(raw_segments, list):
        segmenter = _map_openai_segments(raw_segments)
    elif text:
        segmenter = [{"start": 0.0, "slutt": 0.0, "tekst": text, "taler": "SPEAKER_00"}]
    else:
        segmenter = []

    return TranskripsjonSvar.model_validate(
        {
            "tekst": text,
            "segmenter": segmenter,
            "advarsler": [],
        }
    )


def _pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int = 16_000) -> bytes:
    clipped = np.clip(pcm, -1.0, 1.0)
    pcm_i16 = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_i16.tobytes())
    return buf.getvalue()


async def _call_transkripsjon_service(
    *,
    filename: str,
    content: bytes,
    n_talere: int,
    service_url: str,
) -> TranskripsjonSvar:
    url = service_url.rstrip("/") + TRANSKRIPSJON_API_PATH
    timeout = httpx.Timeout(10.0, read=None, write=None, pool=10.0)
    files = {"lydfil": (filename, content, "audio/wav")}

    if _is_openai_audio_path(TRANSKRIPSJON_API_PATH):
        data = {
            "model": MODELL_ID,
            "response_format": "verbose_json",
            "language": "no",
        }
    else:
        data = {"n_talere": str(n_talere)}

    async with httpx.AsyncClient(timeout=timeout) as klient:
        resp = await klient.post(url, data=data, files=files, headers=_headers())
        resp.raise_for_status()
        return _normalize_transkripsjon_payload(resp.json())


async def transkriber_remote(
    lydfil: Path,
    *,
    n_talere: int = 0,
    service_url: str = TRANSKRIPSJON_SERVICE_URL,
) -> TranskripsjonSvar:
    """Call the remote transcription model service over HTTP."""
    with lydfil.open("rb") as f:
        return await _call_transkripsjon_service(
            filename=lydfil.name,
            content=f.read(),
            n_talere=n_talere,
            service_url=service_url,
        )


async def transkriber_remote_pcm(
    pcm: np.ndarray,
    *,
    service_url: str = TRANSKRIPSJON_SERVICE_URL,
) -> TranskripsjonSvar:
    wav_content = _pcm_to_wav_bytes(pcm)
    return await _call_transkripsjon_service(
        filename="segment.wav",
        content=wav_content,
        n_talere=0,
        service_url=service_url,
    )
