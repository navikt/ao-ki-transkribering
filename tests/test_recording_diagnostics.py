import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend.recording_diagnostics import RecordingSocket, observe_task
from frontend.routes import sanntid
from shared import diagnostics
from worker.transkribering.sanntid import LydChunk


@pytest.fixture
def events(caplog):
    diagnostics.logger.addHandler(caplog.handler)
    yield lambda: [json.loads(r.message) for r in caplog.records if r.name == diagnostics.logger.name]
    diagnostics.logger.removeHandler(caplog.handler)


@pytest.mark.asyncio
async def test_disconnect_logs_metadata_without_content(events):
    class Socket:
        async def receive(self):
            return {"type": "websocket.disconnect", "code": 1006, "reason": "PRIVATE"}

        async def send_json(self, data):
            pass

    socket = RecordingSocket(Socket())
    await socket.send_json({"type": "segment", "tekst": "PRIVATE"})
    await socket.receive()
    assert events()[-1]["close_code"] == 1006
    assert events()[-1]["results"] == 1
    assert "PRIVATE" not in json.dumps(events())


def test_invalid_metadata_cannot_break_recording_or_expose_object_repr(events):
    class Private:
        def __repr__(self):
            return "SECRET"

    diagnostics.event("test.metadata", unknown=Private())
    diagnostics.event("test.metadata", duration=float("nan"))
    assert events()[0]["unknown"] is None
    assert events()[1]["event"] == "diagnostics.invalid_metadata"
    assert "SECRET" not in json.dumps(events())


@pytest.mark.asyncio
async def test_worker_failure_is_observed_without_waiting_for_cleanup(events):
    async def broken():
        raise ValueError("SECRET transcript and bearer token")

    task = observe_task(asyncio.create_task(broken()), "remote_worker")
    with pytest.raises(ValueError):
        await task
    await asyncio.sleep(0)
    assert events()[-1]["event"] == "recording.task_failed"
    assert events()[-1]["error_type"] == "ValueError"
    assert events()[-1]["frames"][-1]["function"] == "broken"
    assert "SECRET" not in json.dumps(events())


@pytest.mark.asyncio
async def test_queue_reports_backpressure_and_timing(events):
    socket = RecordingSocket(None)
    for start in range(4):
        await socket.queue.put(LydChunk(np.zeros(160), start))
    waiting = asyncio.create_task(socket.queue.put(LydChunk(np.zeros(160), 4)))
    await asyncio.sleep(0)
    assert not waiting.done()
    await socket.queue.get()
    assert socket.snapshot()["chunk_inflight_s"] is not None
    socket.queue.task_done()
    await waiting
    assert any(e["event"] == "recording.queue_backpressure" for e in events())
    assert any(e["event"] == "recording.chunk_started" and e["queue_wait_s"] >= 0 for e in events())


@pytest.mark.asyncio
async def test_session_correlation_and_context_cleanup(monkeypatch, events):
    sent = []

    async def accept():
        pass

    async def send(data):
        sent.append(data)

    async def handler(socket):
        assert diagnostics.recording_id.get() == sent[0]["recording_id"]

    monkeypatch.setattr(sanntid, "TRANSKRIPSJON_BACKEND", "remote")
    monkeypatch.setattr(sanntid, "_sanntid_remote", handler)
    await sanntid.sanntid_ws(SimpleNamespace(accept=accept, send_json=send))
    assert diagnostics.recording_id.get() is None
    assert {e["recording_id"] for e in events()} == {sent[0]["recording_id"]}
    assert events()[-1]["event"] == "recording.ended"


def test_browser_report_is_strict_and_content_free(events):
    app = FastAPI()
    app.include_router(sanntid.router)
    client = TestClient(app)
    report = dict(recording_id="00000000-0000-4000-8000-000000000000", code=1006,
                  elapsed_s=120, buffered_bytes=0, online=True, visibility="visible", audio_state="running")
    assert client.post("/diagnostics/recording-close", json=report).status_code == 204
    assert events()[-1]["event"] == "recording.browser_closed"
    assert client.post("/diagnostics/recording-close", json={**report, "transcript": "PRIVATE"}).status_code == 422
    assert "PRIVATE" not in json.dumps(events())


@pytest.mark.asyncio
async def test_heartbeat_send_failure_is_recorded(events):
    async def send(data):
        raise OSError("PRIVATE credentials")

    socket = RecordingSocket(SimpleNamespace(send_json=send))
    with pytest.raises(OSError):
        await socket.send_json({"type": "heartbeat"})
    assert events()[-1]["event"] == "recording.send_failed"
    assert events()[-1]["message_type"] == "heartbeat"
    assert "PRIVATE" not in json.dumps(events())


@pytest.mark.asyncio
async def test_heartbeat_ack_is_correlated_and_duplicates_ignored(events):
    sent = []

    async def send(data):
        sent.append(data)

    async def receive():
        return {"type": "websocket.receive", "text": json.dumps(
            {"type": "heartbeat_ack", "sequence": sent[-1]["sequence"]})}

    socket = RecordingSocket(SimpleNamespace(send_json=send, receive=receive))
    await socket.send_json({"type": "heartbeat"})
    await socket.receive()
    await socket.receive()
    acks = [e for e in events() if e["event"] == "recording.heartbeat_ack"]
    assert len(acks) == 1
    assert acks[0]["round_trip_s"] >= 0
    assert socket.snapshot()["heartbeat_acks"] == 1
    assert socket.snapshot()["last_ack_age_s"] is not None


def test_browser_timing_fields_are_bounded(events):
    app = FastAPI()
    app.include_router(sanntid.router)
    client = TestClient(app)
    report = dict(recording_id="00000000-0000-4000-8000-000000000000", code=1006,
                  elapsed_s=150, buffered_bytes=0, online=True, visibility="visible",
                  audio_state="running", was_clean=False, last_message_age_s=3,
                  last_heartbeat_age_s=8, heartbeat_count=9)
    assert client.post("/diagnostics/recording-close", json=report).status_code == 204
    assert client.post("/diagnostics/recording-close", json={**report, "last_message_age_s": -1}).status_code == 422
