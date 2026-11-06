"""Advanced experiments must not mutate released prompts or bypass model limits."""

from unittest.mock import AsyncMock
import json
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
import pytest

from frontend.routes import referat as routes
from worker.prompts.versions import BASIS_0_1
from shared.services.llm_proxy_client import LlmForesporsel


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(routes, "kall_llm", AsyncMock(return_value="Et referat."))
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_prompt_editor_reads_immutable_definition(client):
    response = client.get("/llm/prompt-definitions/basis_0_1")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["system_prompt"] == BASIS_0_1.system_prompt
    assert data["user_prompt"] == BASIS_0_1.user_prompt
    assert data["context_tokens"] == 32768
    assert client.get("/llm/prompt-definitions/veiledermote").status_code == 404
    assert client.get("/llm/prompt-definitions/unknown").status_code == 404


def test_overrides_use_exact_custom_prompt_and_report_effective_settings(client):
    overrides = {"system_prompt": "Custom system", "user_prompt": "Source: {transkripsjon} {literal}",
                 "temperature": 0.4, "top_p": 0.8, "max_tokens": 4096}
    response = client.post("/referat", json={"transkripsjon": "Private source", "modell": "borealis-12b",
                                           "scenario": "basis_0_1", "overrides": overrides})
    assert response.status_code == 200
    call = routes.kall_llm.await_args
    assert call.args == ("Custom system", "Source: Private source {literal}", "borealis-12b")
    assert call.kwargs == {"raw": True, "max_tokens": 4096,
                           "generation": {"temperature": 0.4, "top_p": 0.8, "num_ctx": 32768}}
    metadata = response.json()["prompt"]
    assert metadata["custom"] is True
    assert metadata["id"] == "basis_0_1"
    assert len(metadata["definition_sha256"]) == 64
    assert metadata["parameters"] == {"temperature": 0.4, "top_p": 0.8, "max_tokens": 4096, "context_tokens": 32768}
    assert "Private source" not in response.text
    assert "Custom system" not in response.text
    assert client.get("/llm/prompt-definitions/basis_0_1").json()["system_prompt"] == BASIS_0_1.system_prompt


def test_parameter_only_override_keeps_baseline_prompt(client):
    response = client.post("/referat", json={"transkripsjon": "Kilde", "overrides": {"temperature": 0.0}})
    assert response.status_code == 200
    call = routes.kall_llm.await_args
    assert call.args[:2] == (BASIS_0_1.system_prompt, BASIS_0_1.user_prompt.format(transkripsjon="Kilde"))
    assert call.kwargs["generation"]["temperature"] == 0
    assert call.kwargs["max_tokens"] == 2048


@pytest.mark.parametrize("overrides", [
    {}, {"system_prompt": "Only system"}, {"user_prompt": "Only user"},
    {"system_prompt": " ", "user_prompt": "{transkripsjon}"},
    {"system_prompt": "System", "user_prompt": "No source"},
    {"system_prompt": "System", "user_prompt": "{transkripsjon} {transkripsjon}"},
    {"system_prompt": "X" * 12001, "user_prompt": "{transkripsjon}"},
    {"temperature": -0.1}, {"temperature": 2.1}, {"temperature": "0.2"},
    {"top_p": 0}, {"top_p": 1.1}, {"max_tokens": 127}, {"max_tokens": 8193},
    {"max_tokens": 512.5}, {"max_tokens": True}, {"context_tokens": 131072}, {"model": "other"},
])
def test_invalid_overrides_never_reach_model(client, overrides):
    response = client.post("/referat", json={"transkripsjon": "Kilde", "overrides": overrides})
    assert response.status_code == 422
    routes.kall_llm.assert_not_awaited()


@pytest.mark.parametrize("endpoint,scenario", [("/referat", "veiledermote"), ("/sammendrag", "basis_0_1")])
def test_legacy_actions_cannot_silently_ignore_overrides(client, endpoint, scenario):
    response = client.post(endpoint, json={"transkripsjon": "Kilde", "scenario": scenario,
                                           "overrides": {"max_tokens": 512}})
    assert response.status_code == 400
    routes.kall_llm.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["/referat/stream", "/referat/rullerende/stream"])
def test_streamed_overrides_keep_custom_metadata(client, endpoint):
    response = client.post(endpoint, json={"transkripsjon": "Kilde", "overrides": {"max_tokens": 4096}})
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    result = next(item for item in events if item["type"] == "ferdig")
    assert result["prompt"]["custom"] is True
    assert result["prompt"]["parameters"]["max_tokens"] == 4096
    assert routes.kall_llm.await_args.kwargs["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_job_identity_includes_overrides_but_metadata_does_not_store_prompt_text(monkeypatch):
    submissions = []

    class Jobs:
        def submit(self, key, owner, fingerprint, metadata, *args):
            submissions.append((fingerprint, metadata))
            return metadata

    monkeypatch.setattr(routes, "summary_jobs", Jobs())
    source = {"transkripsjon": "Kilde", "overrides": {"system_prompt": "Private prompt",
                                                        "user_prompt": "{transkripsjon}", "temperature": 0.3}}
    request = Request({"type": "http", "state": {"user": {"oid": "synthetic-user"}}})
    for temperature in [0.3, 0.3, 0.4]:
        source["overrides"]["temperature"] = temperature
        await routes.start_llm_job("referat", uuid4(), LlmForesporsel(**source), request, Response())
    assert submissions[0][0] == submissions[1][0]
    assert submissions[0][0] != submissions[2][0]
    assert "Private prompt" not in json.dumps(submissions)
    assert submissions[0][1]["prompt"]["parameters"]["temperature"] == 0.3
