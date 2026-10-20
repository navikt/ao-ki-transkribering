import io
import shutil
import wave
import math

import httpx
import numpy as np
import pytest

from shared.contracts.transcription import TranskripsjonSvar
from shared.services import transkripsjon_client as client


def write_audio(path, seconds=31.25):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(np.zeros((round(seconds * 48000), 2), dtype="<i2").tobytes())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for audio conversion")
@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [31.25, 181.25])
async def test_uploaded_file_uses_bounded_sections_with_shared_speakers_and_absolute_timestamps(tmp_path, monkeypatch, seconds):
    path = tmp_path / "meeting.wav"
    write_audio(path, seconds=seconds)
    if seconds > 180:
        assert path.stat().st_size > 32 * 1024 * 1024
    calls = []
    progress = []

    async def model(**kwargs):
        if len(kwargs["content"]) > 500_000:
            request = httpx.Request("POST", "https://model.test/v1/audio/transcriptions")
            response = httpx.Response(413, request=request)
            raise httpx.HTTPStatusError("Payload too large", request=request, response=response)
        with wave.open(io.BytesIO(kwargs["content"])) as audio:
            assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
            seconds = audio.getnframes() / 16000
        calls.append((kwargs, seconds))
        return TranskripsjonSvar(tekst=f"Del {len(calls)}.", segmenter=[
            {"tekst": f"Del {len(calls)}.", "start": 0, "slutt": seconds, "taler": "SPEAKER_01"}
        ], advarsler=["Testadvarsel"])

    monkeypatch.setattr(client, "TRANSKRIPSJON_API_PATH", "/v1/audio/transcriptions")
    monkeypatch.setattr(client, "_call_transkripsjon_service", model)
    result = await client.transkriber_remote(path, n_talere=2, status_callback=progress.append)
    starts = list(range(0, math.ceil(seconds - 15) + 14, 14))
    assert [round(call["chunk_start"], 2) for call, _ in calls] == starts
    assert [length for _, length in calls] == [min(15, seconds - start) for start in starts]
    assert len({call["session_id"] for call, _ in calls}) == 1
    assert all(call["session_id"] and call["n_talere"] == 2 for call, _ in calls)
    assert [segment.start for segment in result.segmenter] == starts
    assert result.segmenter[-1].slutt == seconds
    assert all(segment.taler == "SPEAKER_01" for segment in result.segmenter)
    assert result.advarsler == ["Testadvarsel"]
    assert result.tekst == " ".join(f"Del {i + 1}." for i in range(len(calls)))
    assert progress[0]["lyd_varighet_s"] == seconds
    assert progress[-1]["behandlet_lyd_s"] == seconds
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for audio conversion")
@pytest.mark.asyncio
async def test_invalid_audio_fails_before_model_call_and_cleans_converted_file(tmp_path, monkeypatch):
    from shared.services.uploaded_audio import AudioFileError

    source = tmp_path / "invalid.wav"
    source.write_bytes(b"not audio")

    async def model(**kwargs):
        pytest.fail("Invalid media must not be sent to the model")

    monkeypatch.setattr(client, "TRANSKRIPSJON_API_PATH", "/v1/audio/transcriptions")
    monkeypatch.setattr(client, "_call_transkripsjon_service", model)
    with pytest.raises(AudioFileError, match="kunne ikke leses"):
        await client.transkriber_remote(source)
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for audio conversion")
@pytest.mark.asyncio
async def test_section_failure_is_not_returned_as_partial_transcription(tmp_path, monkeypatch):
    path = tmp_path / "meeting.wav"
    write_audio(path)
    count = 0

    async def model(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise httpx.ReadTimeout("Timed out")
        return TranskripsjonSvar(tekst="Første del.", segmenter=[])

    monkeypatch.setattr(client, "TRANSKRIPSJON_API_PATH", "/v1/audio/transcriptions")
    monkeypatch.setattr(client, "_call_transkripsjon_service", model)
    with pytest.raises(httpx.ReadTimeout):
        await client.transkriber_remote(path)
    assert count == 2
    assert list(tmp_path.iterdir()) == [path]
