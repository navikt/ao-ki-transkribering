import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from frontend import compact_summary
from frontend.routes import referat
from shared.services.llm_proxy_client import LlmForesporsel
from worker.prompts.handlinger import hent_handling, VEILEDER_VARIANTER
from worker.prompts.motereferat import (
    SYSTEM_REFERAT, SYSTEM_RULLERENDE, SYSTEM_VEILEDERSAMTALE, SYSTEM_VEILEDER_BASIS,
)


@pytest.mark.parametrize("live", [False, True])
def test_adviser_default_uses_new_source_only_conversation_format(live):
    request = LlmForesporsel(transkripsjon="Du vil snakke med legen først.", scenario="veiledermote")
    system, user, scenario = referat._bygg_referat_prompt(request, rullerende=live)
    assert system == SYSTEM_VEILEDERSAMTALE + "\n\n" + VEILEDER_VARIANTER["oppfolging"]["instruksjoner"]
    assert scenario == "veiledermote"
    assert "vi/du-form" in system
    assert "Relevante opplysninger om helse" in system
    assert "ikke en forhåndsbestemt historie" in system
    assert "Ikke legg til bakgrunn" in system
    assert "Et tiltak som er diskutert er ikke avtalt" in system
    assert request.transkripsjon in user
    assert ("Møtet pågår" in user) is live
    assert not request.begrenset_referat


@pytest.mark.parametrize("live", [False, True])
def test_restricted_adviser_format_is_explicit_for_final_and_live(live):
    request = LlmForesporsel(transkripsjon="Vi drøfter arbeid.", scenario="veiledermote", begrenset_referat=True)
    system, user, _ = referat._bygg_referat_prompt(request, rullerende=live)
    assert system == (SYSTEM_VEILEDER_BASIS + "\n\n" + (SYSTEM_RULLERENDE if live else SYSTEM_REFERAT)
                      + "\n\n" + VEILEDER_VARIANTER["oppfolging"]["instruksjoner"])
    assert "KAN IKKE SKRIVES" in system
    assert request.transkripsjon in user


@pytest.mark.parametrize("scenario", [None, "generelt_mote", "samarbeidsmote", "egendefinert"])
def test_restricted_flag_cannot_be_silently_ignored_by_another_template(scenario):
    with pytest.raises(HTTPException) as error:
        referat._bygg_referat_prompt(LlmForesporsel(
            transkripsjon="Hei", scenario=scenario, begrenset_referat=True,
        ))
    assert error.value.status_code == 400


def test_restricted_flag_is_rejected_on_non_referat_actions():
    with pytest.raises(HTTPException) as error:
        referat._bygg_handling_prompt(hent_handling("sammendrag"), LlmForesporsel(
            transkripsjon="Hei", scenario="veiledermote", begrenset_referat=True,
        ))
    assert error.value.status_code == 400


def test_restricted_flag_requires_a_json_boolean():
    app = FastAPI()
    app.include_router(referat.router)
    result = TestClient(app).post("/referat", json={
        "transkripsjon": "Hei", "scenario": "veiledermote", "begrenset_referat": "false",
    })
    assert result.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("comprehensive", [False, True])
async def test_restricted_output_never_appends_unfiltered_source_quotes(comprehensive):
    source = "Ada: Jeg skal kontakte legen om diagnosen og husleierestansene."

    async def model(system, user, *args, **kwargs):
        if system == compact_summary.REVIEW_SYSTEM:
            data = json.loads(user)
            assert "husleierestansene" not in data["draft"]
            return json.dumps({"accepted": [True] * len(data["claims"]), "conflicts": [], "missing_topics": []})
        return "Arbeidsønsker er uavklart."

    async def count(*args):
        return 100

    events = [event async for event in compact_summary.generate(
        source, lambda text: ("Avgrenset format", text), None,
        call=model, count=count, comprehensive=comprehensive, source_quotes=False,
    )]
    assert events[-1]["tekst"] == "Arbeidsønsker er uavklart."


@pytest.mark.parametrize("action", ["referat", "rullerende_referat"])
def test_restricted_job_wires_source_quote_policy_and_fingerprint_input(monkeypatch, action):
    captured = {}

    def generate(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(referat, "generate_grounded", generate)
    request = LlmForesporsel(transkripsjon="Hei", scenario="veiledermote", begrenset_referat=True)
    referat._handling_events(hent_handling(action), request)
    assert captured["source_quotes"] is False
    assert request.model_dump() != request.model_copy(update={"begrenset_referat": False}).model_dump()


@pytest.mark.asyncio
async def test_new_optional_field_preserves_old_job_fingerprints(monkeypatch):
    import hashlib
    from uuid import uuid4
    from fastapi import Request, Response

    fingerprints = []

    class Jobs:
        def submit(self, key, owner, fingerprint, *args):
            fingerprints.append(fingerprint)
            return {}

    monkeypatch.setattr(referat, "summary_jobs", Jobs())
    payload = LlmForesporsel(transkripsjon="Vi drøfter arbeid.", scenario="veiledermote")
    old_fields = payload.model_dump()
    old_fields.pop("begrenset_referat")
    old_fields.pop("veileder_variant")
    old_fingerprint = hashlib.sha256(json.dumps(
        ["referat", old_fields], sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()
    request = Request({"type": "http", "state": {"user": {"oid": "synthetic-user"}}})
    await referat.start_llm_job("referat", uuid4(), payload, request, Response())
    await referat.start_llm_job("referat", uuid4(), payload.model_copy(update={"begrenset_referat": True}), request, Response())
    assert fingerprints[0] == old_fingerprint
    assert fingerprints[1] != old_fingerprint


@pytest.mark.parametrize("variant", ["kartlegging", "oppfolging"])
@pytest.mark.parametrize("restricted", [False, True])
@pytest.mark.parametrize("live", [False, True])
def test_purpose_overlay_keeps_same_foundation_and_independent_restriction(variant, restricted, live):
    source = "Du er usikker på neste steg. Et tiltak er foreslått, ikke avtalt."
    payload = LlmForesporsel(transkripsjon=source, scenario="veiledermote",
                            veileder_variant=variant, begrenset_referat=restricted)
    system, user, _ = referat._bygg_referat_prompt(payload, rullerende=live)
    assert system.startswith(SYSTEM_VEILEDER_BASIS)
    assert system.endswith(VEILEDER_VARIANTER[variant]["instruksjoner"])
    other = "kartlegging" if variant == "oppfolging" else "oppfolging"
    assert VEILEDER_VARIANTER[other]["instruksjoner"] not in system
    assert "Ikke legg til bakgrunn" in system
    assert "Ikke gjett hvem som har en oppgave" in system
    assert ("KAN IKKE SKRIVES" in system) is restricted
    assert ("AVGRENSNING: Relevante opplysninger om helse" in system) is not restricted
    assert source in user
    assert ("Møtet pågår" in user) is live
    assert referat._veileder_valg(payload, "veiledermote") == {
        "veileder_variant": variant, "begrenset_referat": restricted,
    }


def test_purpose_metadata_is_server_defined_and_does_not_expose_prompts():
    from worker.prompts.handlinger import list_referat_scenarier

    scenarios = list_referat_scenarier()
    variants = next(s for s in scenarios if s["id"] == "veiledermote")["varianter"]
    assert [item["id"] for item in variants] == ["kartlegging", "oppfolging"]
    assert [item["id"] for item in variants if item["standard"]] == ["oppfolging"]
    assert all("instruksjoner" not in item for item in variants)
    assert all("varianter" not in item for item in scenarios if item["id"] != "veiledermote")


@pytest.mark.parametrize("scenario", [None, "generelt_mote", "samarbeidsmote", "egendefinert"])
def test_purpose_is_not_silently_ignored_for_other_templates(scenario):
    with pytest.raises(HTTPException) as error:
        referat._bygg_referat_prompt(LlmForesporsel(
            transkripsjon="Hei", scenario=scenario, veileder_variant="kartlegging",
        ))
    assert error.value.status_code == 400


@pytest.mark.parametrize("action", ["sammendrag", "avtaler", "kvalitetssjekk"])
def test_purpose_is_not_silently_ignored_for_other_actions(action):
    with pytest.raises(HTTPException) as error:
        referat._bygg_handling_prompt(hent_handling(action), LlmForesporsel(
            transkripsjon="Hei", scenario="veiledermote", veileder_variant="kartlegging",
        ))
    assert error.value.status_code == 400


@pytest.mark.parametrize("variant", ["ukjent", "", False, ["kartlegging"]])
def test_unknown_or_untyped_purpose_is_rejected(variant):
    app = FastAPI()
    app.include_router(referat.router)
    result = TestClient(app).post("/referat", json={
        "transkripsjon": "Hei", "scenario": "veiledermote", "veileder_variant": variant,
    })
    assert result.status_code == 422


@pytest.mark.parametrize("endpoint", ["/referat", "/referat/stream", "/referat/rullerende/stream"])
@pytest.mark.parametrize("variant", ["kartlegging", "oppfolging"])
def test_all_generation_routes_use_and_report_selected_purpose(monkeypatch, endpoint, variant):
    from unittest.mock import AsyncMock
    from tests.summary_fakes import checked_model

    calls = AsyncMock(return_value="Arbeidsønsker er uavklart.")
    checked_model(monkeypatch, calls)
    app = FastAPI()
    app.include_router(referat.router)
    result = TestClient(app).post(endpoint, json={
        "transkripsjon": "Du er usikker på arbeid.", "scenario": "veiledermote", "veileder_variant": variant,
    })
    assert result.status_code == 200
    assert VEILEDER_VARIANTER[variant]["instruksjoner"] in calls.await_args.args[0]
    if endpoint == "/referat":
        assert result.json()["veileder_variant"] == variant
    else:
        assert f'"veileder_variant": "{variant}"' in result.text


@pytest.mark.asyncio
async def test_purpose_changes_job_fingerprint_and_job_metadata(monkeypatch):
    from uuid import uuid4
    from fastapi import Request, Response

    submitted = []

    class Jobs:
        def submit(self, key, owner, fingerprint, metadata, run, error):
            submitted.append((fingerprint, metadata))
            return metadata

    monkeypatch.setattr(referat, "summary_jobs", Jobs())
    request = Request({"type": "http", "state": {"user": {"oid": "synthetic-user"}}})
    for variant in ["kartlegging", "oppfolging"]:
        await referat.start_llm_job("referat", uuid4(), LlmForesporsel(
            transkripsjon="Vi drøfter arbeid.", scenario="veiledermote", veileder_variant=variant,
        ), request, Response())
    assert submitted[0][0] != submitted[1][0]
    assert submitted[0][1]["veileder_variant"] == "kartlegging"
    assert submitted[1][1]["veileder_variant"] == "oppfolging"
