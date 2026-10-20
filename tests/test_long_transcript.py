import json
import asyncio
import re

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import frontend.routes.referat as routes
import frontend.long_transcript as long_text


@pytest.mark.parametrize("endpoint", [
    "/referat", "/referat/stream", "/referat/rullerende/stream", "/sammendrag/stream",
    "/llm/handlinger/avtaler", "/llm/handlinger/kvalitetssjekk/stream",
])
def test_long_meeting_is_processed_without_manual_splitting(monkeypatch, endpoint):
    monkeypatch.setattr(long_text, "input_token_budget", lambda *_args: 3150)
    source = "".join(f"Taler 1: Sak {i}: Fristen er fredag.\n" for i in range(1200))
    calls = []

    def result(system, user):
        if len((system + user).encode()) > 6500:
            request = httpx.Request("POST", "https://model.test")
            response = httpx.Response(400, request=request, text="maximum context length exceeded")
            raise httpx.HTTPStatusError("context limit", request=request, response=response)
        calls.append((system, user))
        return "Alle fristene er fredag."

    async def call(system, user, model, **kwargs):
        return result(system, user)

    async def stream(system, user, model, **kwargs):
        text = result(system, user)
        yield text, False, ""
        yield "", True, text

    monkeypatch.setattr(routes, "kall_llm", call)
    monkeypatch.setattr(routes, "stream_llm_tokens", stream)
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post(endpoint, json={
        "transkripsjon": source, "scenario": "utfyllende_sammendrag",
    })
    assert response.status_code == 200
    assert "Del opp teksten" not in response.text
    if endpoint.endswith("/stream"):
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert not any(e["type"] == "feil" for e in events)
        assert events[-1]["type"] == "ferdig"
        assert events[-1]["tekst"] == "Alle fristene er fredag."
        assert any(e["type"] == "progress" for e in events)
    else:
        assert response.json()["tekst"] == "Alle fristene er fredag."
    assert len(calls) > 2
    assert "Sak 0:" in "".join(user for _, user in calls)
    assert "Sak 1199:" in "".join(user for _, user in calls)
    submitted_parts = [user.removeprefix("KILDEUTDRAG:\n") for _, user in calls if user.startswith("KILDEUTDRAG:\n")]
    assert "".join(submitted_parts) == source
    assert all(len((system + user).encode()) <= 6500 for system, user in calls)


@pytest.mark.parametrize("source", ["Æøå 🎙️ samtale\n" * 40, "x" * 500, "Første linje.\nNeste linje. " * 30])
def test_split_preserves_every_character_and_byte_limit(source):
    parts = long_text.split_text(source, 43)
    assert "".join(parts) == source
    assert all(0 < len(part.encode()) <= 43 for part in parts)


@pytest.mark.asyncio
async def test_context_rejection_retries_in_smaller_sections(monkeypatch):
    monkeypatch.setattr(long_text, "input_token_budget", lambda: 7000)
    source = "Taler 1: Vi beholder oppgaven og fristen.\n" * 120
    accepted = []
    rejected = []

    async def call(system, user, model, **kwargs):
        if len((system + user).encode()) > 3000:
            rejected.append(user)
            req = httpx.Request("POST", "https://model.test")
            resp = httpx.Response(400, request=req, text="context_length_exceeded")
            raise httpx.HTTPStatusError("context", request=req, response=resp)
        accepted.append(user)
        return "Oppgaven og fristen beholdes."

    events = [event async for event in long_text.generate(source, lambda text: ("Regler", text), None, call=call)]
    assert rejected
    assert events[-1] == {"type": "ferdig", "tekst": "Oppgaven og fristen beholdes."}
    parts = [user.removeprefix("KILDEUTDRAG:\n") for user in accepted if user.startswith("KILDEUTDRAG:\n")]
    assert "".join(parts) == source


@pytest.mark.asyncio
async def test_hierarchical_merge_retains_first_last_parts_and_final_instructions(monkeypatch):
    monkeypatch.setattr(long_text, "input_token_budget", lambda: 2000)
    source = "".join(f"<F{i}> " + "Møtedetaljer. " * 100 + "\n" for i in range(40))
    calls = []

    async def call(system, user, model, **kwargs):
        calls.append((system, user, kwargs))
        assert len((system + user).encode()) <= 4000
        if user.startswith("KILDEUTDRAG:"):
            return "Faktagrunnlag. " * 60 + " ".join(re.findall(r"<F\d+>", user))
        return "Referat med alle deler."

    events = [event async for event in long_text.generate(
        source, lambda text: ("Bare fakta", "EGEN MAL: Bruk ansvar og frister.\n" + text),
        "valgt-modell", call=call,
    )]
    assert events[-1]["tekst"] == "Referat med alle deler."
    assert calls[-1][0] == "Bare fakta"
    assert calls[-1][1].startswith("EGEN MAL: Bruk ansvar og frister.")
    assert re.findall(r"<F\d+>", calls[-1][1]) == [f"<F{i}>" for i in range(40)]
    assert any("KILDEUTDRAG:\nDel 1:" in user for _, user, _ in calls)


@pytest.mark.asyncio
async def test_heartbeat_keeps_section_processing_alive_and_cancels_on_disconnect():
    cancelled = asyncio.Event()

    async def slow_model():
        try:
            await asyncio.Event().wait()
            yield {"type": "ferdig", "tekst": "Ingen"}
        finally:
            cancelled.set()

    stream = long_text.with_heartbeats(slow_model(), interval=0.001)
    assert await anext(stream) is None
    assert await anext(stream) is None
    await stream.aclose()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_section_failure_cannot_produce_a_partial_success(monkeypatch):
    monkeypatch.setattr(long_text, "input_token_budget", lambda: 4000)
    count = 0

    async def call(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise httpx.ConnectError("Unavailable")
        return "Faktanotater"

    events = []
    with pytest.raises(httpx.ConnectError):
        async for event in long_text.generate("Møtedetaljer. " * 1000, lambda text: ("Regler", text), None, call=call):
            events.append(event)
    assert not any(event["type"] == "ferdig" for event in events)
