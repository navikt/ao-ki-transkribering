import asyncio
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend.routes import recordings

KEY = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def client(monkeypatch):
    recordings.sessions.clear()
    monkeypatch.setattr(recordings, "TRANSKRIPSJON_BACKEND", "local")
    monkeypatch.setattr(recordings, "transkriber_pcm", lambda pcm, speakers: (None, speakers))
    app = FastAPI()
    app.include_router(recordings.router)
    with TestClient(app) as client:
        yield client
    recordings.sessions.clear()


def post(client, seq=0, start=0, pcm=None):
    return client.post(f"/recordings/{KEY}/chunks/{seq}?start_sample={start}",
                       content=(np.zeros(240000, dtype="<f4") if pcm is None else pcm).tobytes())


def test_silence_is_processed_and_duplicate_is_cached(client, monkeypatch):
    calls = []
    monkeypatch.setattr(recordings, "transkriber_pcm", lambda pcm, state: (calls.append(1), None))
    first = post(client)
    assert first.status_code == 200
    assert first.json()["state"] == "processed"
    assert first.json()["end_sample"] == 240000
    assert first.json()["tekst"] == ""
    assert post(client).json() == first.json()
    assert len(calls) == 1
    assert post(client, pcm=np.ones(240000, dtype="<f4")).status_code == 409


def test_contiguous_timeline_and_final_partial_chunk(client):
    assert post(client).status_code == 200
    assert post(client, 1, 250000).status_code == 409
    assert post(client, 1, 224000, np.zeros(17000, dtype="<f4")).json()["end_sample"] == 241000


def test_failed_chunk_can_retry_without_advancing(client, monkeypatch):
    def fail(*args):
        raise RuntimeError("private")
    monkeypatch.setattr(recordings, "transkriber_pcm", fail)
    assert post(client).status_code == 503
    assert recordings.sessions[KEY].sequence == -1
    monkeypatch.setattr(recordings, "transkriber_pcm", lambda *args: (None, None))
    assert post(client).status_code == 200


def test_segments_use_absolute_audio_time(client, monkeypatch):
    assert post(client).status_code == 200
    monkeypatch.setattr(recordings, "transkriber_pcm", lambda *args: (
        {"tekst": "hei", "segmenter": [{"tekst": "hei", "start": 2, "slutt": 3}]}, None))
    result = post(client, 1, 224000).json()
    assert result["segmenter"][0]["start"] == 16
    assert result["segmenter"][0]["slutt"] == 17


def test_invalid_and_oversized_audio(client):
    assert post(client, pcm=np.zeros(240001, dtype="<f4")).status_code == 413
    assert post(client, pcm=np.array([float("nan")], dtype="<f4")).status_code == 422


@pytest.mark.asyncio
async def test_disconnected_request_does_not_cancel_processing(monkeypatch):
    from starlette.requests import Request
    from uuid import UUID
    from types import SimpleNamespace

    recordings.sessions.clear()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        entered.set()
        await release.wait()
        return SimpleNamespace(model_dump=lambda: {"tekst": "", "segmenter": []})

    def request():
        async def receive():
            return {"type": "http.request", "body": np.zeros(16000, dtype="<f4").tobytes()}
        return Request({"type": "http", "method": "POST", "path": "/"}, receive)

    monkeypatch.setattr(recordings, "TRANSKRIPSJON_BACKEND", "remote")
    monkeypatch.setattr(recordings, "transkriber_remote_pcm", model)
    first = asyncio.create_task(recordings.upload_chunk(UUID(KEY), 0, request(), 0))
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(recordings.upload_chunk(UUID(KEY), 0, request(), 0))
    await asyncio.sleep(0)
    release.set()
    assert (await second)["state"] == "processed"
    assert calls == [1]
    recordings.sessions.clear()
