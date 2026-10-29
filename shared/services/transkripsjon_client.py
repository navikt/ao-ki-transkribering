import asyncio
import io
import wave
import time
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np

from shared.contracts.transcription import TranskripsjonSvar
from shared.services.model_auth import authenticated_headers
from shared.core.settings import TRANSKRIPSJON_API_KEY, TRANSKRIPSJON_API_PATH, TRANSKRIPSJON_MODELL, TRANSKRIPSJON_SERVICE_URL
from shared.core.settings import REMOTE_DIARIZATION_MODEL
from shared.diagnostics import event, failure
from shared.metrics import metrics, observe
from shared.services.transkripsjon_backend import StatusCallback
from shared.services.uploaded_audio import SAMPLE_RATE, decoded_audio

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
                "taler": str(seg.get("speaker") or "SPEAKER_00"),
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
            "advarsler": payload.get("warnings", []),
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


@observe("speech_request")
async def _call_transkripsjon_service(
    *,
    filename: str,
    content: bytes,
    n_talere: int,
    service_url: str,
    session_id: str | None = None,
    chunk_start: float = 0,
    read_timeout: float = 600,
) -> TranskripsjonSvar:
    url = service_url.rstrip("/") + TRANSKRIPSJON_API_PATH
    timeout = httpx.Timeout(10.0, read=read_timeout, write=120, pool=10.0)
    if _is_openai_audio_path(TRANSKRIPSJON_API_PATH):
        files = {"file": (filename, content, "audio/wav")}
        data = {
            "model": TRANSKRIPSJON_MODELL,
            "response_format": "verbose_json",
            "language": "no",
        }
        if session_id and REMOTE_DIARIZATION_MODEL:
            data.update(session_id=session_id, chunk_start=str(chunk_start))
    else:
        files = {"lydfil": (filename, content, "audio/wav")}
        data = {"n_talere": str(n_talere)}

    async with httpx.AsyncClient(timeout=timeout) as klient:
        # Retry the same audio, never move on to the next chunk after a temporary failure.
        for attempt in range(3):
            started = time.monotonic()
            event("model.transcription_attempt", attempt=attempt + 1, chunk_start_s=chunk_start)
            try:
                resp = await klient.post(url, data=data, files=files,
                                        headers=await authenticated_headers(url, _headers()))
                resp.raise_for_status()
                result = _normalize_transkripsjon_payload(resp.json())
                event("model.transcription_succeeded", attempt=attempt + 1,
                      elapsed_s=round(time.monotonic() - started, 3), status=resp.status_code,
                      characters=len(result.tekst), segments=len(result.segmenter))
                return result
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                failure("model.transcription_attempt_failed", exc, attempt=attempt + 1,
                        elapsed_s=round(time.monotonic() - started, 3),
                        status=exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None)
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {429, 502, 503, 504}:
                    raise
                if attempt == 2:
                    raise
                metrics.retries.inc()
                await asyncio.sleep(2 ** attempt)
            except Exception as exc:
                failure("model.transcription_failed", exc, attempt=attempt + 1,
                        elapsed_s=round(time.monotonic() - started, 3))
                raise


async def transkriber_remote(
    lydfil: Path,
    *,
    n_talere: int = 0,
    service_url: str = TRANSKRIPSJON_SERVICE_URL,
    status_callback: StatusCallback | None = None,
) -> TranskripsjonSvar:
    """Send uploaded audio in bounded sections, sharing diarization state throughout."""
    if not _is_openai_audio_path(TRANSKRIPSJON_API_PATH):
        with lydfil.open("rb") as f:
            return await _call_transkripsjon_service(
                filename=lydfil.name, content=f.read(), n_talere=n_talere, service_url=service_url,
            )

    from worker.transkribering.sanntid import LydChunk, SegmentSamler

    collector = SegmentSamler()
    session_id = str(uuid4())
    texts, segments, warnings = [], [], []
    async with decoded_audio(lydfil) as audio:
        total = audio.getnframes()
        event("upload.audio_prepared", audio_seconds=round(total / SAMPLE_RATE, 3))
        if status_callback:
            status_callback({"fase": "transkriberer", "lyd_varighet_s": total / SAMPLE_RATE, "behandlet_lyd_s": 0})
        start = 0
        while start < total:
            audio.setpos(start)
            pcm = np.frombuffer(audio.readframes(15 * SAMPLE_RATE), dtype="<i2").astype(np.float32) / 32768
            end = start + len(pcm)
            result = await _call_transkripsjon_service(
                filename="segment.wav", content=_pcm_to_wav_bytes(pcm), n_talere=n_talere,
                service_url=service_url, session_id=session_id, chunk_start=start / SAMPLE_RATE,
                read_timeout=60,
            )
            warnings.extend(result.advarsler)
            merged = collector.legg_til(result.model_dump(), LydChunk(
                pcm, start / SAMPLE_RATE, 1 if start else 0,
            ))
            if merged:
                texts.append(merged["tekst"])
                segments.extend(merged["segmenter"])
            event("upload.section_processed", start_sample=start, end_sample=end, total_samples=total)
            if status_callback:
                status_callback({"behandlet_lyd_s": end / SAMPLE_RATE})
            if end == total:
                break
            start = end - SAMPLE_RATE
    return TranskripsjonSvar(tekst=" ".join(texts), segmenter=segments, advarsler=list(dict.fromkeys(warnings)))


async def transkriber_remote_pcm(
    pcm: np.ndarray,
    *,
    service_url: str = TRANSKRIPSJON_SERVICE_URL,
    session_id: str | None = None,
    chunk_start: float = 0,
) -> TranskripsjonSvar:
    wav_content = _pcm_to_wav_bytes(pcm)
    return await _call_transkripsjon_service(
        filename="segment.wav",
        content=wav_content,
        n_talere=0,
        service_url=service_url,
        session_id=session_id,
        chunk_start=chunk_start,
        read_timeout=60,
    )
