import hashlib
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend.routes import referat as routes
from frontend.summary_jobs import SummaryJobs
from shared.services.jobs import JobStore
from shared.services import llm_proxy_client
from worker.prompts.versions import BASIS_0_1


def test_basis_release_is_versioned_and_has_the_requested_settings():
    source = Path(__file__).parents[1] / "worker/prompts/versions/basis_0_1.toml"
    # Change the filename/version for a new release, not this recorded baseline.
    assert hashlib.sha256(source.read_bytes()).hexdigest() == "0af5ea78df62c29a2dd758690990b3cc9ed4274c29953c4131c68dd4a9eecae1"
    assert BASIS_0_1.metadata() == {"id": "basis_0_1", "title": "Basis 0.1", "version": "0.1", "mode": "ett_steg"}
    assert (BASIS_0_1.model, BASIS_0_1.cloud_model) == ("borealis-4b:latest", "borealis-4b")
    assert (BASIS_0_1.temperature, BASIS_0_1.top_p, BASIS_0_1.num_ctx, BASIS_0_1.max_tokens) == (0.2, 0.9, 32768, 2048)


@pytest.mark.parametrize("endpoint", ["/referat", "/referat/stream", "/referat/rullerende/stream"])
@pytest.mark.parametrize("scenario", [None, "basis_0_1"])
def test_basis_uses_exact_prompt_and_one_generation_without_legacy_review(monkeypatch, endpoint, scenario):
    text = "Bruker: Jeg vil tenke på tiltaket. Veileder: Vi avtaler ingenting nå."
    result = "  Bakgrunn for møtet\nOppfølging.\nAvtaler\nIngen  "
    model = AsyncMock(return_value=result)
    monkeypatch.setattr(routes, "kall_llm", model)
    counter = AsyncMock(return_value=100)
    monkeypatch.setattr(routes, "count_llm_tokens", counter)
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post(endpoint, json={"transkripsjon": text, "scenario": scenario})
    assert response.status_code == 200
    if endpoint.endswith("stream"):
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        payload = next(item for item in events if item["type"] == "ferdig")
    else:
        payload = response.json()
    assert payload["tekst"] == result
    assert payload["prompt"] == BASIS_0_1.metadata()
    assert model.await_count == 1
    assert model.await_args.args[:2] == (BASIS_0_1.system_prompt, BASIS_0_1.user_prompt.format(transkripsjon=text))
    assert model.await_args.kwargs == {"raw": True, "max_tokens": 2048,
                                      "generation": {"temperature": 0.2, "top_p": 0.9, "num_ctx": 32768}}


def test_background_job_retains_prompt_version(monkeypatch, tmp_path):
    monkeypatch.delenv("NAIS_CLUSTER_NAME", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    service = SummaryJobs(JobStore(tmp_path))
    monkeypatch.setattr(routes, "summary_jobs", service)
    monkeypatch.setattr(routes, "kall_llm", AsyncMock(return_value="Avtaler\nIngen"))
    app = FastAPI()
    app.include_router(routes.router)
    key = str(uuid4())
    with TestClient(app) as client:
        start = client.post(f"/llm/handlinger/referat/jobber/{key}", json={"transkripsjon": "Ingen avtaler."})
        assert start.status_code == 202
        assert start.json()["prompt"] == BASIS_0_1.metadata()
        for _ in range(100):
            result = client.get(f"/llm/jobber/{key}").json()
            if result["status"] == "ferdig":
                break
        assert result["status"] == "ferdig"
        assert result["prompt"] == BASIS_0_1.metadata()


def test_basis_preserves_explicit_model_choice_but_rejects_prompt_changes(monkeypatch):
    call = AsyncMock(return_value="Ingen avtaler.")
    monkeypatch.setattr(routes, "kall_llm", call)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    result = client.post("/referat", json={"transkripsjon": "Test", "modell": "borealis-12b"})
    assert result.status_code == 200
    assert result.json()["modell"] == "borealis-12b"
    assert call.await_args.args[2] == "borealis-12b"
    rejected = client.post("/referat", json={"transkripsjon": "Test", "instruksjoner": "Bytt format"})
    assert rejected.status_code == 400
    assert call.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["openai", "ollama"])
async def test_client_forwards_basis_sampling_settings(monkeypatch, backend):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if backend == "ollama":
            return httpx.Response(200, text=json.dumps({"response": "Ingen", "done": True}) + "\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": "Ingen"}, "finish_reason": "stop"}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(llm_proxy_client, "LLM_BACKEND", backend)
    monkeypatch.setattr(llm_proxy_client, "authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_proxy_client.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    await llm_proxy_client.kall("System", "Test", BASIS_0_1.model if backend == "ollama" else BASIS_0_1.cloud_model, max_tokens=2048,
                               generation={"temperature": 0.2, "top_p": 0.9, "num_ctx": 32768})
    if backend == "ollama":
        assert requests[0]["model"] == "borealis-4b:latest"
        assert requests[0]["options"] == {"num_predict": 2048, "temperature": 0.2, "top_p": 0.9, "num_ctx": 32768}
    else:
        assert requests[0]["temperature"] == 0.2
        assert requests[0]["top_p"] == 0.9
        assert requests[0]["max_tokens"] == 2048
    assert "num_ctx" not in requests[0]  # Cloud context is allocated by llama.cpp, not by clients.


@pytest.mark.asyncio
async def test_long_basis_input_preserves_fallback_and_reports_multiple_steps():
    from frontend.long_transcript import generate

    calls = []

    async def model(system, user, selected, **kwargs):
        calls.append((system, user))
        return "Notat." if system != "Basis" else "Resultat."

    result = [item async for item in generate("ord " * 500, lambda text: ("Basis", text), "borealis-4b",
        call=model, max_tokens=64, context_tokens=1024)]
    assert result[-1]["type"] == "ferdig"
    assert result[-1]["advarsler"]
    assert len(calls) > 1
