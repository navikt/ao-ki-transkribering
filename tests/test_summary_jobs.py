import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend.routes import referat as routes
from frontend.summary_jobs import SummaryJobs
from shared.services.jobs import JobStore


def test_summary_submission_is_idempotent_and_result_survives_status_connection(monkeypatch, tmp_path):
    service = SummaryJobs(JobStore(tmp_path))
    monkeypatch.setattr(routes, "summary_jobs", service)
    calls = []

    async def model(system, user, model):
        calls.append(user)
        await asyncio.sleep(0.03)
        return "Vi avtaler fredag."

    monkeypatch.setattr(routes, "kall_llm", model)
    app = FastAPI()
    app.include_router(routes.router)
    key = str(uuid4())
    payload = {"transkripsjon": "Vi avtaler fredag.", "scenario": "generelt_mote"}
    with TestClient(app) as client:
        url = f"/llm/handlinger/referat/jobber/{key}"
        assert client.post(url, json=payload).status_code == 202
        assert client.post(url, json=payload).status_code == 202
        for _ in range(100):
            state = client.get(f"/llm/jobber/{key}").json()
            if state["status"] == "ferdig":
                break
        assert state["status"] == "ferdig"
        assert state["tekst"] == "Vi avtaler fredag."
        assert state["scenario"] == "generelt_mote"
        assert client.get(f"/llm/jobber/{key}").json()["tekst"] == state["tekst"]
        assert client.get(f"/llm/jobber/{key}").headers["cache-control"] == "no-store"
        assert "owner" not in state and "fingerprint" not in state
        assert client.post(url, json={**payload, "transkripsjon": "Annen tekst"}).status_code == 409
    assert len(calls) == 1


async def test_queue_limits_active_work_and_survives_client_waiter_cancellation(tmp_path):
    service = SummaryJobs(JobStore(tmp_path), concurrency=1, capacity=2)
    release = asyncio.Event()
    started = []

    async def generate():
        started.append(True)
        await release.wait()
        yield {"type": "ferdig", "tekst": "Resultat"}

    keys = [str(uuid4()) for _ in range(3)]
    for key in keys[:2]:
        service.submit(key, "owner", "fingerprint", {}, generate, routes._job_error)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        service.submit(keys[2], "owner", "fingerprint", {}, generate, routes._job_error)
    assert exc.value.status_code == 429
    await asyncio.sleep(0)
    assert len(started) == 1

    async def browser():
        service.status(keys[0], "owner")
        await asyncio.sleep(60)

    waiter = asyncio.create_task(browser())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await asyncio.gather(*service.tasks.values())
    assert len(started) == 2
    assert all(service.status(key, "owner")["status"] == "ferdig" for key in keys[:2])


async def test_owner_isolation_failure_redaction_and_restart(tmp_path):
    from fastapi import HTTPException

    service = SummaryJobs(JobStore(tmp_path))
    key = str(uuid4())

    async def generate():
        raise RuntimeError("secret meeting content")
        yield  # pragma: no cover

    service.submit(key, "alice", "fingerprint", {}, generate, routes._job_error)
    for operation in [lambda: service.status(key, "bob"),
                      lambda: service.submit(key, "bob", "fingerprint", {}, generate, routes._job_error)]:
        with pytest.raises(HTTPException) as exc:
            operation()
        assert exc.value.status_code == 404
    await asyncio.gather(*service.tasks.values())
    assert service.status(key, "alice")["status"] == "feil"
    assert "secret" not in str(service.status(key, "alice"))
    service.store.update_path(service.store.result_path(key), {"status": "genererer"})
    restarted = SummaryJobs(service.store)
    assert "omstart" in restarted.status(key, "alice")["melding"]


async def test_shutdown_marks_active_jobs_as_interrupted(tmp_path):
    service = SummaryJobs(JobStore(tmp_path))
    key = str(uuid4())

    async def generate():
        await asyncio.Event().wait()
        yield  # pragma: no cover

    service.submit(key, "owner", "fingerprint", {}, generate, routes._job_error)
    await asyncio.sleep(0)
    await service.shutdown()
    assert service.status(key, "owner")["status"] == "feil"


def test_production_owner_requires_identity_and_scopes_to_tenant(monkeypatch):
    from fastapi import HTTPException, Request

    monkeypatch.setenv("NAIS_CLUSTER_NAME", "prod-gcp")
    request = Request({"type": "http", "state": {}})
    with pytest.raises(HTTPException) as exc:
        routes._job_owner(request)
    assert exc.value.status_code == 401
    request.state.user = {"oid": "alice", "tid": "tenant-one"}
    owner = routes._job_owner(request)
    request.state.user = {"oid": "alice", "tid": "tenant-two"}
    assert routes._job_owner(request) != owner
    assert "alice" not in owner


@pytest.mark.parametrize("scenario", ["generelt_mote", "minimalt_sammendrag", "utfyllende_sammendrag", "egendefinert"])
async def test_background_job_preserves_selected_prompt_and_output_budget(monkeypatch, tmp_path, scenario):
    from fastapi import Request, Response

    service = SummaryJobs(JobStore(tmp_path))
    monkeypatch.setattr(routes, "summary_jobs", service)
    calls = []

    async def model(system, user, model, **kwargs):
        calls.append((system, user, kwargs))
        return "Fredag tester vi løsningen."

    monkeypatch.setattr(routes, "kall_llm", model)
    payload = routes.LlmForesporsel(
        transkripsjon="Fredag tester vi løsningen.", scenario=scenario,
        instruksjoner="Bruk en enkel overskrift" if scenario == "egendefinert" else None,
    )
    request = Request({"type": "http", "state": {"user": {"oid": "test"}}})
    key = uuid4()
    await routes.start_llm_job("referat", key, payload, request, Response())
    await asyncio.gather(*service.tasks.values())
    assert len(calls) == 1
    assert calls[0][:2] == routes._bygg_referat_prompt(payload)[:2]
    assert calls[0][2] == ({"max_tokens": 3072} if scenario == "utfyllende_sammendrag" else {})
    result = await routes.llm_job_status(key, request, Response())
    assert result["status"] == "ferdig" and result["scenario"] == scenario
