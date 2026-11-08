"""Imported 0.2 prompts retain the supplied text and generation parameters."""

import hashlib
import json
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from frontend.routes import referat as routes
from shared.services import llm_proxy_client
from worker.prompts.handlinger import list_referat_scenarier
from worker.prompts.versions import CURRENT_PROMPTS


@pytest.mark.parametrize("profile,checksum,temperature,repeat", [
    (CURRENT_PROMPTS[0], "68378b5b2b2b32f440ac833350ae419fe0a4354d0bb84eab340b2289efab1201", 0.2, 1.05),
    (CURRENT_PROMPTS[1], "d8beff3ae211c44b438de3650025ad1db7c9aa880d6e9fab597b9b030f5d0bb9", 0.2, 1.2),
    (CURRENT_PROMPTS[2], "da682c13903b44f6df202940717beb9d85f7dc48f848ab052519f6c7fd8eb5e4", 0, 1.2),
], ids=lambda value: getattr(value, "id", None))
def test_import_matches_markdown_source_fingerprints(profile, checksum, temperature, repeat):
    assert hashlib.sha256((profile.system_prompt + "\0" + profile.user_prompt).encode()).hexdigest() == checksum
    assert profile.temperature == temperature
    assert profile.repeat_penalty == repeat
    assert (profile.max_tokens, profile.num_ctx, profile.top_p) == (2048, 32768, 0.9)
    assert profile.model == "hf.co/NbAiLab/borealis-12b-gguf:Q4_K_M"
    assert profile.cloud_model == "borealis-12b"
    assert profile.version == "0.2"
    assert profile.based_on == profile.id.replace("_0_2", "_0_1")


def test_three_current_choices_and_old_versions_remain_discoverable():
    choices = list_referat_scenarier()
    assert [item["id"] for item in choices if item["current"]] == [p.id for p in CURRENT_PROMPTS]
    assert [item["id"] for item in choices if item["standard"]] == ["basis_0_2"]
    assert {p.id.replace("_0_2", "_0_1") for p in CURRENT_PROMPTS} <= {item["id"] for item in choices}


@pytest.mark.parametrize("profile", CURRENT_PROMPTS, ids=lambda p: p.id)
@pytest.mark.parametrize("endpoint", ["/referat", "/referat/stream", "/referat/rullerende/stream"])
def test_generation_uses_exact_prompt_settings_and_explicit_model(monkeypatch, profile, endpoint):
    call = AsyncMock(return_value="Avtaler\nIngen")
    monkeypatch.setattr(routes, "kall_llm", call)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    response = client.post(endpoint, json={"transkripsjon": "Kilde", "scenario": profile.id, "modell": "borealis-4b"})
    assert response.status_code == 200
    assert call.await_count == 1
    assert call.await_args.args == (profile.system_prompt, profile.user_prompt.format(transkripsjon="Kilde"), "borealis-4b")
    assert call.await_args.kwargs == {"raw": True, "max_tokens": 2048, "generation": {
        "temperature": profile.temperature, "top_p": 0.9, "num_ctx": 32768, "repeat_penalty": profile.repeat_penalty}}
    definition = client.get("/llm/prompt-definitions/" + profile.id).json()
    assert definition["repeat_penalty"] == profile.repeat_penalty
    assert definition["prompt"] == profile.metadata()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["openai", "ollama"])
async def test_repeat_penalty_reaches_provider_without_changing_old_payloads(monkeypatch, backend):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if backend == "ollama":
            return httpx.Response(200, text=json.dumps({"response": "Svar", "done": True}) + "\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": "Svar"}, "finish_reason": "stop"}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(llm_proxy_client, "LLM_BACKEND", backend)
    monkeypatch.setattr(llm_proxy_client, "authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_proxy_client.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    for generation in [{"temperature": 0.2, "repeat_penalty": 1.2}, {"temperature": 0.2}]:
        await llm_proxy_client.kall("System", "Source", "borealis-12b", generation=generation)
    if backend == "ollama":
        assert requests[0]["options"]["repeat_penalty"] == 1.2
        assert "repeat_penalty" not in requests[1]["options"]
    else:
        assert requests[0]["extra_body"] == {"repeat_penalty": 1.2}
        assert "extra_body" not in requests[1]
