from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import frontend.routes.helse as helse_routes
from frontend.routes.helse import router
from shared.services.llm_proxy_client import LlmModell, LlmStatus


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_system_status_reports_ok_models(monkeypatch):
    monkeypatch.setattr(helse_routes, "TRANSKRIPSJON_BACKEND", "remote")
    monkeypatch.setattr(helse_routes, "TRANSKRIPSJON_MODELL", "nb-whisper-large")
    monkeypatch.setattr(helse_routes, "LLM_MODELL", "borealis-12b")
    monkeypatch.setattr(
        helse_routes,
        "hent_llm_status",
        AsyncMock(
            return_value=LlmStatus(
                tilgjengelig=True,
                standard_modell="borealis-12b",
                modeller=[LlmModell(id="borealis-12b"), LlmModell(id="nb-whisper-large")],
            )
        ),
    )

    res = _client().get("/system/status")

    assert res.status_code == 200
    data = res.json()
    assert data["api"]["status"] == "ok"
    assert data["llm"]["status"] == "ok"
    assert data["transcription"]["status"] == "ok"
    assert data["diarization"]["status"] == "degraded"


def test_system_status_reports_missing_models(monkeypatch):
    monkeypatch.setattr(helse_routes, "TRANSKRIPSJON_BACKEND", "remote")
    monkeypatch.setattr(helse_routes, "TRANSKRIPSJON_MODELL", "nb-whisper-large")
    monkeypatch.setattr(helse_routes, "LLM_MODELL", "borealis-12b")
    monkeypatch.setattr(
        helse_routes,
        "hent_llm_status",
        AsyncMock(
            return_value=LlmStatus(
                tilgjengelig=True,
                standard_modell="borealis-12b",
                modeller=[LlmModell(id="annet")],
            )
        ),
    )

    data = _client().get("/system/status").json()

    assert data["llm"]["status"] == "missing_model"
    assert data["transcription"]["status"] == "missing_model"
