from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import frontend.routes.referat as referat_routes
from frontend.routes.referat import router
from shared.services.llm_proxy_client import LlmModell, LlmStatus
from worker.prompts.handlinger import LLM_HANDLINGER, hent_handling, list_handlinger


def test_handlinger_har_stabile_ider():
    assert set(LLM_HANDLINGER) >= {
        "sammendrag",
        "referat",
        "rullerende_referat",
        "avtaler",
        "kvalitetssjekk",
    }


def test_referat_scenarier_lister_faste_promptvalg():
    from worker.prompts.handlinger import list_referat_scenarier

    scenarier = list_referat_scenarier()

    assert [scenario["id"] for scenario in scenarier] == [
        "veiledermote",
        "generelt_mote",
        "samarbeidsmote",
        "egendefinert",
    ]
    assert [scenario["id"] for scenario in scenarier if scenario["standard"]] == ["generelt_mote"]


def test_handling_bygger_prompt_fra_transkripsjon():
    handling = hent_handling("avtaler")

    assert handling is not None
    prompt = handling.bygg_bruker_prompt("Vi avtaler at bruker sender CV fredag.")

    assert "sender CV fredag" in prompt
    assert "{transkripsjon}" not in prompt


@pytest.mark.parametrize("rullerende", [False, True])
def test_egendefinert_referat_beholder_systemregler(rullerende):
    from shared.services.llm_proxy_client import LlmForesporsel

    system, bruker, scenario = referat_routes._bygg_referat_prompt(
        LlmForesporsel(
            transkripsjon="Vi møtes tirsdag.",
            scenario="egendefinert",
            instruksjoner="  Bruk punktliste med {ansvar}.  ",
        ),
        rullerende=rullerende,
    )
    assert scenario == "egendefinert"
    assert "Skriv BARE informasjon som faktisk finnes i transkripsjonen." in system
    assert "aldri kravet om faktabasert innhold" in system
    assert "Bruk punktliste med {ansvar}." in bruker
    assert "Bruk punktliste" not in system
    assert "Vi møtes tirsdag." in bruker
    assert ("Møtet pågår" in bruker) == rullerende


@pytest.mark.parametrize("endpoint", ["/referat", "/referat/stream", "/referat/rullerende/stream"])
@pytest.mark.parametrize("instruksjoner", [None, "", "   "])
def test_egendefinert_referat_krever_instruksjoner(endpoint, instruksjoner):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as klient:
        svar = klient.post(endpoint, json={
            "transkripsjon": "Vi møtes tirsdag.",
            "scenario": "egendefinert",
            "instruksjoner": instruksjoner,
        })
    assert svar.status_code == 400


def test_egendefinert_referat_begrenser_lengde():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as klient:
        svar = klient.post("/referat", json={
            "transkripsjon": "Vi møtes tirsdag.",
            "scenario": "egendefinert",
            "instruksjoner": "x" * 4001,
        })
    assert svar.status_code == 422


@pytest.mark.parametrize("endpoint", ["/referat", "/referat/stream", "/referat/rullerende/stream"])
def test_egendefinert_referat_sender_instruksjoner_til_modell(monkeypatch, endpoint):
    calls = []

    async def kall(system, bruker, modell):
        calls.append((system, bruker))
        return "Vi møtes tirsdag."

    async def stream(system, bruker, modell):
        calls.append((system, bruker))
        yield "Vi møtes tirsdag.", True, "Vi møtes tirsdag."

    monkeypatch.setattr(referat_routes, "kall_llm", kall)
    monkeypatch.setattr(referat_routes, "stream_llm_tokens", stream)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as klient:
        svar = klient.post(endpoint, json={
            "transkripsjon": "Vi møtes tirsdag.",
            "scenario": "egendefinert",
            "instruksjoner": "Fokuser på neste møte.",
        })
    assert svar.status_code == 200
    assert "Fokuser på neste møte." in calls[0][1]
    assert "Fokuser på neste møte." not in calls[0][0]


def test_list_handlinger_eksponerer_kun_metadata():
    metadata = list_handlinger()

    assert {"id", "tittel", "beskrivelse"} == set(metadata[0])
    assert "system_prompt" not in metadata[0]
    assert "bruker_prompt_template" not in metadata[0]


def test_ukjent_handling_gir_404():
    app = FastAPI()
    app.include_router(router)
    klient = TestClient(app)

    res = klient.post("/llm/handlinger/finnes-ikke", json={"transkripsjon": "tekst"})

    assert res.status_code == 404


def test_llm_status_lister_modeller(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    klient = TestClient(app)
    monkeypatch.setattr(
        referat_routes,
        "hent_llm_status",
        AsyncMock(
            return_value=LlmStatus(
                tilgjengelig=True,
                standard_modell="borealis-12b",
                modeller=[LlmModell(id="borealis-12b")],
            )
        ),
    )

    res = klient.get("/llm/status")

    assert res.status_code == 200
    assert res.json() == {
        "tilgjengelig": True,
        "standard_modell": "borealis-12b",
        "modeller": [{"id": "borealis-12b"}],
    }


def test_referat_bruker_valgt_scenario(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    klient = TestClient(app)
    kall = AsyncMock(return_value="Referat")
    monkeypatch.setattr(referat_routes, "kall_llm", kall)

    res = klient.post(
        "/referat",
        json={"transkripsjon": "Vi tok en beslutning.", "scenario": "generelt_mote"},
    )

    assert res.status_code == 200
    assert res.json()["scenario"] == "generelt_mote"
    system_prompt, bruker_prompt, _modell = kall.await_args.args
    assert "generelt møtereferat" in bruker_prompt
    assert "nøytralt møtereferat" in system_prompt


def test_ukjent_referat_scenario_gir_400():
    app = FastAPI()
    app.include_router(router)
    klient = TestClient(app)

    res = klient.post("/referat", json={"transkripsjon": "Hei", "scenario": "ukjent"})

    assert res.status_code == 400
    assert "Ukjent referat-scenario" in res.json()["detail"]


def test_referat_uten_scenario_bruker_generelt_mote(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    kall = AsyncMock(return_value="Referat")
    monkeypatch.setattr(referat_routes, "kall_llm", kall)
    response = TestClient(app).post("/referat", json={"transkripsjon": "Vi avtaler et nytt møte."})
    assert response.status_code == 200
    assert response.json()["scenario"] == "generelt_mote"
    assert "generelt møtereferat" in kall.await_args.args[1]


@pytest.mark.parametrize("scenario", [None, "generelt_mote", "veiledermote", "samarbeidsmote"])
def test_live_referat_bruker_motetype(monkeypatch, scenario):
    from worker.prompts.handlinger import hent_referat_scenario

    calls = []

    async def stream(system, bruker, modell):
        calls.append((system, bruker))
        yield "Referat", True, "Referat"

    monkeypatch.setattr(referat_routes, "stream_llm_tokens", stream)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/referat/rullerende/stream",
        json={"transkripsjon": "Vi avtaler mandag.", "scenario": scenario},
    )
    expected = scenario or "generelt_mote"
    assert response.status_code == 200
    assert f'"scenario": "{expected}"' in response.text
    assert "Vi avtaler mandag." in calls[0][1]
    if expected == "veiledermote":
        assert calls[0][0] == hent_handling("rullerende_referat").system_prompt
    else:
        assert calls[0][0] == hent_referat_scenario(expected).system_prompt
        assert "Møtet pågår" in calls[0][1]


def test_live_referat_avviser_ukjent_scenario():
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/referat/rullerende/stream", json={"transkripsjon": "Hei", "scenario": "ukjent"},
    )
    assert response.status_code == 400


def test_utilgjengelig_llm_modell_gir_503(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    klient = TestClient(app)
    request = httpx.Request("POST", "http://test/v1/chat/completions")
    response = httpx.Response(
        429,
        request=request,
        text="No deployments available for selected model",
    )
    monkeypatch.setattr(
        referat_routes,
        "kall_llm",
        AsyncMock(side_effect=httpx.HTTPStatusError("utilgjengelig", request=request, response=response)),
    )

    res = klient.post("/llm/handlinger/sammendrag", json={"transkripsjon": "Hei"})

    assert res.status_code == 503
    assert res.json()["detail"] == "AI-modellen er ikke tilgjengelig nå"
