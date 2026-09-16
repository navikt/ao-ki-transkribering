from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend.routes.referat import router
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
