import io
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from worker import nemotron_api as api


def wav_audio():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 1600)
    return out.getvalue()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "test-model-key")

    def infer(pcm):
        assert pcm.shape == (1600,)
        return {"text": "Hei", "chunks": [{"text": "Hei", "timestamp": (0.0, 0.1)}]}

    monkeypatch.setattr(api, "hent_nemotron_modell", lambda: SimpleNamespace(transkriber=infer))
    with TestClient(api.app) as client:
        yield client


def request(client, **data):
    return client.post("/v1/audio/transcriptions", headers={"Authorization": "Bearer test-model-key"},
                       files={"file": ("test.wav", wav_audio())}, data={"model": api.MODEL_NAME, **data})


@pytest.mark.parametrize("response_format", ["json", "verbose_json", "text"])
def test_audio_contract(client, response_format):
    response = request(client, response_format=response_format, language="no")
    assert response.status_code == 200
    if response_format == "text":
        assert response.text == "Hei"
    else:
        assert response.json()["text"] == "Hei"
        if response_format == "verbose_json":
            assert response.json()["segments"] == [{"id": 0, "text": "Hei", "start": 0.0, "end": 0.1}]
            assert response.json()["duration"] == 0.1


@pytest.mark.parametrize("data,status", [({"model": "nb-whisper-large"}, 404),
    ({"language": "en"}, 400), ({"response_format": "srt"}, 400)])
def test_rejects_unsupported_requests(client, data, status):
    assert request(client, **data).status_code == status


def test_authentication_and_readiness(client):
    assert client.get("/health").json()["model"] == api.MODEL_NAME
    assert client.get("/v1/models").status_code == 401
    assert client.post("/v1/audio/transcriptions").status_code == 401
    assert client.get("/docs").status_code == 403
    response = client.get("/v1/models", headers={"Authorization": "Bearer test-model-key"})
    assert response.json()["data"][0]["id"] == api.MODEL_NAME


def test_busy_and_upload_limits(client, monkeypatch):
    api.app.state.busy = True
    assert request(client).status_code == 429
    api.app.state.busy = False
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 10)
    assert request(client).status_code == 413
    assert api.app.state.busy is False


def test_invalid_audio_does_not_leave_worker_busy(client):
    response = client.post("/v1/audio/transcriptions", headers={"Authorization": "Bearer test-model-key"},
                           files={"file": ("bad.wav", b"not audio")}, data={"model": api.MODEL_NAME})
    assert response.status_code == 400
    assert api.app.state.busy is False


def test_decode_duration_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "MAX_AUDIO_SECONDS", 0)
    path = tmp_path / "test.wav"
    path.write_bytes(wav_audio())
    with pytest.raises(api.HTTPException) as error:
        api.decode_audio(path)
    assert error.value.status_code == 413


def test_no_silently_corrupted_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(api.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=np.array([np.nan], dtype="<f4").tobytes()))
    with pytest.raises(api.HTTPException) as error:
        api.decode_audio(tmp_path / "test.wav")
    assert error.value.status_code == 400
