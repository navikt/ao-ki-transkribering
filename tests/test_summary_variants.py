import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from frontend.routes import referat as routes
from frontend.summary_jobs import SummaryJobs
from shared.services.jobs import JobStore
from worker.prompts.handlinger import REFERAT_SCENARIER, STANDARD_REFERAT_SCENARIO, list_referat_scenarier
from worker.prompts.versions import BASIS_0_1, STIKKORD_0_1, UTFYLLENDE_0_1

CURRENT = [STIKKORD_0_1, BASIS_0_1, UTFYLLENDE_0_1]


def test_first_release_remains_available_with_its_original_baseline():
    choices = list_referat_scenarier()
    current = [item for item in choices if item["id"] in {profile.id for profile in CURRENT}]
    assert [item["id"] for item in current] == [profile.id for profile in CURRENT]
    assert not any(item["current"] or item["standard"] for item in current)
    assert STANDARD_REFERAT_SCENARIO == "basis_0_2"
    for profile in CURRENT:
        assert profile.system_prompt == BASIS_0_1.system_prompt
        assert (profile.model, profile.cloud_model, profile.temperature, profile.top_p, profile.num_ctx, profile.mode) == (
            BASIS_0_1.model, BASIS_0_1.cloud_model, 0.2, 0.9, 32768, "ett_steg")
    assert [profile.max_tokens for profile in CURRENT] == [768, 2048, 3072]
    assert len({profile.user_prompt for profile in CURRENT}) == 3
    assert BASIS_0_1.based_on is None
    assert all(profile.based_on == BASIS_0_1.id for profile in (STIKKORD_0_1, UTFYLLENDE_0_1))
    assert STIKKORD_0_1.metadata()["based_on"] == BASIS_0_1.id
    assert UTFYLLENDE_0_1.metadata()["based_on"] == BASIS_0_1.id
    assert "stikkord" in STIKKORD_0_1.user_prompt
    assert "sammenhengende prosa" in UTFYLLENDE_0_1.user_prompt


@pytest.mark.parametrize("name,checksum", [
    ("stikkord_0_1", "dce4506ee7e11d4b976f6a502de0ff6ea94d872c9a12f7a25e3e5494a722ce8b"),
    ("utfyllende_0_1", "72bdc2c35365dea528622209ecd9b7abab3ff0464334741121a9ef870137903f"),
])
def test_new_prompt_releases_are_frozen(name, checksum):
    path = Path(__file__).parents[1] / "worker/prompts/versions" / (name + ".toml")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_old_templates_are_retained_and_not_exact_duplicates_of_new_choices():
    legacy = {name: item for name, item in REFERAT_SCENARIER.items() if item.profile is None}
    assert set(legacy) == {"minimalt_sammendrag", "utfyllende_sammendrag", "veiledermote",
                           "generelt_mote", "samarbeidsmote", "egendefinert"}
    for old in legacy.values():
        assert all((old.system_prompt, old.bruker_prompt_template) != (new.system_prompt, new.user_prompt) for new in CURRENT)


@pytest.mark.parametrize("profile", [STIKKORD_0_1, UTFYLLENDE_0_1], ids=lambda item: item.id)
@pytest.mark.parametrize("endpoint", ["/referat", "/referat/stream", "/referat/rullerende/stream"])
@pytest.mark.parametrize("model", ["borealis-4b", "borealis-12b"])
def test_new_variants_use_their_exact_versioned_prompt_and_one_generation(monkeypatch, profile, endpoint, model):
    text = "Bruker: Jeg er usikker. Veileder: Vi har ikke avtalt et tiltak."
    call = AsyncMock(return_value="Avtaler\nIngen")
    monkeypatch.setattr(routes, "kall_llm", call)
    monkeypatch.setattr(routes, "count_llm_tokens", AsyncMock(return_value=100))
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post(endpoint, json={"transkripsjon": text, "scenario": profile.id, "modell": model})
    assert response.status_code == 200
    if endpoint.endswith("stream"):
        data = next(json.loads(line[6:]) for line in response.text.splitlines()
                    if line.startswith("data: ") and json.loads(line[6:])["type"] == "ferdig")
    else:
        data = response.json()
    assert data["prompt"] == profile.metadata()
    assert data["modell"] == model
    assert call.await_count == 1
    assert call.await_args.args == (profile.system_prompt, profile.user_prompt.format(transkripsjon=text), model)
    assert call.await_args.kwargs == {"raw": True, "max_tokens": profile.max_tokens,
                                      "generation": {"temperature": 0.2, "top_p": 0.9, "num_ctx": 32768}}


@pytest.mark.parametrize("profile", [STIKKORD_0_1, UTFYLLENDE_0_1], ids=lambda item: item.id)
def test_versioned_variants_reject_legacy_context_and_custom_overrides(monkeypatch, profile):
    call = AsyncMock()
    monkeypatch.setattr(routes, "kall_llm", call)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    for extra in ({"instruksjoner": "Endre prompten"}, {"veileder_variant": "kartlegging"}, {"begrenset_referat": True}):
        result = client.post("/referat", json={"transkripsjon": "Test", "scenario": profile.id, **extra})
        assert result.status_code == 400
    assert call.await_count == 0


@pytest.mark.parametrize("profile", [STIKKORD_0_1, UTFYLLENDE_0_1], ids=lambda item: item.id)
def test_background_jobs_keep_variant_and_version(monkeypatch, tmp_path, profile):
    monkeypatch.delenv("NAIS_CLUSTER_NAME", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setattr(routes, "summary_jobs", SummaryJobs(JobStore(tmp_path)))
    monkeypatch.setattr(routes, "kall_llm", AsyncMock(return_value="Avtaler\nIngen"))
    app = FastAPI()
    app.include_router(routes.router)
    key = str(uuid4())
    with TestClient(app) as client:
        response = client.post(f"/llm/handlinger/referat/jobber/{key}",
                               json={"transkripsjon": "Ingen avtaler.", "scenario": profile.id})
        assert response.status_code == 202
        for _ in range(100):
            data = client.get(f"/llm/jobber/{key}").json()
            if data["status"] == "ferdig":
                break
        assert data["status"] == "ferdig"
        assert data["scenario"] == profile.id
        assert data["prompt"] == profile.metadata()
