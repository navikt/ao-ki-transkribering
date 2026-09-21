from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from shared.services import transkripsjon_client
from shared.services.transkripsjon_client import (
    _call_transkripsjon_service,
    _normalize_transkripsjon_payload,
    _pcm_to_wav_bytes,
)


def test_normalize_transkripsjon_payload_supports_internal_contract():
    payload = {
        "tekst": "Hei",
        "segmenter": [{"start": 0.0, "slutt": 1.0, "tekst": "Hei", "taler": "SPEAKER_00"}],
        "advarsler": [],
    }

    svar = _normalize_transkripsjon_payload(payload)

    assert svar.tekst == "Hei"
    assert svar.segmenter[0].taler == "SPEAKER_00"


def test_normalize_transkripsjon_payload_supports_openai_verbose_json():
    payload = {
        "text": "Hei der",
        "segments": [{"start": 0.1, "end": 0.9, "text": "Hei der"}],
    }

    svar = _normalize_transkripsjon_payload(payload)

    assert svar.tekst == "Hei der"
    assert len(svar.segmenter) == 1
    assert svar.segmenter[0].start == 0.1
    assert svar.segmenter[0].slutt == 0.9
    assert svar.segmenter[0].taler == "SPEAKER_00"


def test_pcm_to_wav_bytes_creates_nonempty_payload():
    pcm = np.array([0.0, 0.25, -0.25, 0.5, -0.5], dtype=np.float32)

    wav = _pcm_to_wav_bytes(pcm)

    assert len(wav) > 44
    assert wav.startswith(b"RIFF")


@pytest.mark.asyncio
async def test_openai_audio_path_uses_file_multipart_field(monkeypatch):
    monkeypatch.setattr(transkripsjon_client, "TRANSKRIPSJON_API_PATH", "/v1/audio/transcriptions")
    monkeypatch.setattr(transkripsjon_client, "MODELL_ID", "nb-whisper-large")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"text": "Hei"}

    mock_klient = MagicMock()
    mock_klient.post = AsyncMock(return_value=mock_resp)

    mock_klient_cm = MagicMock()
    mock_klient_cm.__aenter__ = AsyncMock(return_value=mock_klient)
    mock_klient_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("shared.services.transkripsjon_client.httpx.AsyncClient", return_value=mock_klient_cm):
        await _call_transkripsjon_service(
            filename="test.wav",
            content=b"wav",
            n_talere=0,
            service_url="http://model-api",
        )

    _, kwargs = mock_klient.post.call_args
    assert "file" in kwargs["files"]
    assert "lydfil" not in kwargs["files"]
    assert kwargs["data"]["model"] == "nb-whisper-large"


@pytest.mark.asyncio
async def test_internal_transcription_path_uses_lydfil_multipart_field(monkeypatch):
    monkeypatch.setattr(transkripsjon_client, "TRANSKRIPSJON_API_PATH", "/transkriber")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"tekst": "Hei", "segmenter": [], "advarsler": []}

    mock_klient = MagicMock()
    mock_klient.post = AsyncMock(return_value=mock_resp)

    mock_klient_cm = MagicMock()
    mock_klient_cm.__aenter__ = AsyncMock(return_value=mock_klient)
    mock_klient_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("shared.services.transkripsjon_client.httpx.AsyncClient", return_value=mock_klient_cm):
        await _call_transkripsjon_service(
            filename="test.wav",
            content=b"wav",
            n_talere=2,
            service_url="http://model-api",
        )

    _, kwargs = mock_klient.post.call_args
    assert "lydfil" in kwargs["files"]
    assert "file" not in kwargs["files"]
    assert kwargs["data"] == {"n_talere": "2"}
