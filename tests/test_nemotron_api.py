import asyncio
import io
import wave
from types import SimpleNamespace

import httpx
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


def test_faster_whisper_runtime_startup_and_health(monkeypatch):
    from worker.transkribering import ctranslate2_api_model

    monkeypatch.setenv("SPEECH_ASR_BACKEND", "faster-whisper")
    monkeypatch.setenv("NEMOTRON_DIAR_MODELL", "")
    monkeypatch.setattr(ctranslate2_api_model, "CTranslate2Transkriberer",
                        lambda: SimpleNamespace(enhet="cuda", inference_concurrency=2))
    with TestClient(api.app) as client:
        body = client.get("/health").json()
        assert body["runtime"] == "faster-whisper"
        assert body["inference_concurrency"] == 2
        assert body["outstanding_requests"] == 0
        assert api.app.state.requests.capacity == 32


def test_queue_and_upload_limits(client, monkeypatch):
    assert api.app.state.requests.capacity == 32
    api.app.state.requests.pending = 32
    response = request(client)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "10"
    api.app.state.requests.pending = 0
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 10)
    assert request(client).status_code == 413
    assert api.app.state.requests.pending == 0


def test_invalid_audio_does_not_leave_worker_busy(client):
    response = client.post("/v1/audio/transcriptions", headers={"Authorization": "Bearer test-model-key"},
                           files={"file": ("bad.wav", b"not audio")}, data={"model": api.MODEL_NAME})
    assert response.status_code == 400
    assert api.app.state.requests.pending == 0


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


@pytest.mark.asyncio
async def test_http_accepts_32_outstanding_requests(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "test-model-key")
    monkeypatch.setattr(api.app.state, "requests", api.SpeechRequestQueue(), raising=False)
    release = asyncio.Event()
    processed = []

    async def process(file, response_format, session_id, chunk_start):
        await release.wait()
        processed.append(await file.read())
        return {"text": "Hei"}

    monkeypatch.setattr(api, "transcribe_audio", process)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://speech") as client:
        async def post():
            return await client.post("/v1/audio/transcriptions",
                                     headers={"Authorization": "Bearer test-model-key"},
                                     files={"file": ("test.wav", wav_audio())}, data={"model": api.MODEL_NAME})

        tasks = [asyncio.create_task(post()) for _ in range(32)]

        async def wait_for_queue():
            while api.app.state.requests.pending != 32:
                await asyncio.sleep(0)

        try:
            await asyncio.wait_for(wait_for_queue(), timeout=5)
            assert (await post()).status_code == 429
        finally:
            release.set()
            responses = await asyncio.gather(*tasks)
        assert all(response.status_code == 200 for response in responses)
        assert processed == [wav_audio()] * 32
        assert api.app.state.requests.pending == 0


@pytest.mark.asyncio
async def test_disconnected_request_is_not_processed(monkeypatch):
    monkeypatch.setattr(api.app.state, "requests", api.SpeechRequestQueue(), raising=False)

    async def disconnected():
        return True

    async def forbidden(*args):
        pytest.fail("Disconnected request must not run inference")

    monkeypatch.setattr(api, "transcribe_audio", forbidden)
    file = api.UploadFile(file=io.BytesIO(wav_audio()))
    with pytest.raises(api.HTTPException) as error:
        await api.transcribe(SimpleNamespace(is_disconnected=disconnected), file, api.MODEL_NAME,
                             "no", "json", None, 0)
    assert error.value.status_code == 499
    assert file.file.closed
    assert api.app.state.requests.pending == 0
