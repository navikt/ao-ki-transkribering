import asyncio

import pytest
from langsmith.run_helpers import get_tracing_context

from frontend.summary_workflow import run


async def test_events_are_preserved_and_engine_runs_once():
    calls = []
    events = [{"type": "progress", "melding": "Skriver"}, {"type": "token", "tekst": "Hei"},
              {"type": "ferdig", "tekst": "Hei", "advarsler": ["Kontroller"]}]

    async def generate():
        calls.append(True)
        for event in events:
            yield event

    assert [event async for event in run(generate)] == events
    assert calls == [True]


async def test_first_result_closes_engine_without_consuming_more():
    closed = []

    async def generate():
        try:
            yield {"type": "ferdig", "tekst": "Resultat"}
            pytest.fail("The existing first-result contract must not change")
        finally:
            closed.append(True)

    assert len([event async for event in run(generate)]) == 1
    assert closed == [True]


async def test_progress_is_not_buffered_until_generation_completes():
    release = asyncio.Event()

    async def generate():
        yield {"type": "progress", "melding": "Arbeider"}
        await release.wait()
        yield {"type": "ferdig", "tekst": "Resultat"}

    events = run(generate)
    assert (await asyncio.wait_for(anext(events), timeout=1))["type"] == "progress"
    release.set()
    assert (await anext(events))["type"] == "ferdig"
    await events.aclose()


async def test_model_error_is_not_retried_or_replaced():
    error = RuntimeError("PRIVATE_ERROR")

    async def generate():
        raise error
        yield

    with pytest.raises(RuntimeError) as caught:
        [event async for event in run(generate)]
    assert caught.value is error


async def test_missing_result_fails_as_before():
    async def generate():
        yield {"type": "progress", "melding": "Arbeider"}

    with pytest.raises(RuntimeError, match="Generation ended without a result"):
        [event async for event in run(generate)]


async def test_cancellation_closes_the_inflight_engine():
    started, closed = asyncio.Event(), asyncio.Event()

    async def generate():
        try:
            started.set()
            await asyncio.Event().wait()
            yield
        finally:
            closed.set()

    async def consume():
        return [event async for event in run(generate)]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_ambient_external_tracing_is_disabled(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    async def generate():
        assert get_tracing_context()["enabled"] is False
        yield {"type": "ferdig", "tekst": "PRIVATE_RESULT"}

    assert len([event async for event in run(generate)]) == 1
