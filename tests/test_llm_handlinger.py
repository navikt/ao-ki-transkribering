from unittest.mock import AsyncMock

import httpx
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


def test_handling_bygger_prompt_fra_transkripsjon():
    handling = hent_handling("avtaler")

    assert handling is not None
    prompt = handling.bygg_bruker_prompt("Vi avtaler at bruker sender CV fredag.")

    assert "sender CV fredag" in prompt
    assert "{transkripsjon}" not in prompt


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
