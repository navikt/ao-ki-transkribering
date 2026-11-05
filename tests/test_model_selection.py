import pytest
from pydantic import ValidationError

from shared.services.model_selection import resolve_model, summary_models, transcription_models
from shared.services import llm_proxy_client


def test_choices_keep_configured_defaults_and_separate_model_types():
    assert transcription_models("remote", "nb-whisper-large") == ["nb-whisper-large", "nb-whisper-small"]
    assert summary_models("openai", "borealis-12b") == ["borealis-12b", "borealis-4b"]
    assert transcription_models("local", "local-asr") == ["local-asr"]
    assert summary_models("ollama", "local-llm") == ["local-llm"]
    assert resolve_model(None, ["default"]) == "default"
    with pytest.raises(ValueError):
        resolve_model("borealis-4b", ["nb-whisper-large"])


def test_summary_request_accepts_small_borealis_but_not_arbitrary_models(monkeypatch):
    monkeypatch.setattr(llm_proxy_client, "LLM_BACKEND", "openai")
    assert llm_proxy_client.LlmForesporsel(transkripsjon="Test", modell="borealis-4b").modell == "borealis-4b"
    with pytest.raises(ValidationError):
        llm_proxy_client.LlmForesporsel(transkripsjon="Test", modell="nb-whisper-small")
    monkeypatch.setattr(llm_proxy_client, "LLM_BACKEND", "ollama")
    monkeypatch.setattr(llm_proxy_client, "LLM_MODELL", "local-llm")
    with pytest.raises(ValidationError):
        llm_proxy_client.LlmForesporsel(transkripsjon="Test", modell="borealis-4b")


def test_uploaded_file_forwards_choice_to_background_job(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from frontend.routes import transkripsjon
    from shared.services.jobs import JobStore
    from shared.contracts.transcription import TranskripsjonSvar

    monkeypatch.setattr(transkripsjon, "TRANSKRIPSJON_BACKEND", "remote")
    monkeypatch.setattr(transkripsjon, "TRANSKRIPSJON_MODELL", "nb-whisper-large")
    monkeypatch.setattr(transkripsjon, "job_store", JobStore(tmp_path))
    call = AsyncMock(return_value=TranskripsjonSvar(tekst="Test", segmenter=[]))
    monkeypatch.setattr(transkripsjon, "transkriber_remote", call)
    app = FastAPI()
    app.include_router(transkripsjon.router)
    with TestClient(app) as client:
        response = client.post("/transkriber", data={"modell": "nb-whisper-small"},
                               files={"lydfil": ("synthetic.wav", b"test", "audio/wav")})
        assert response.status_code == 200
        assert call.await_args.kwargs["model"] == "nb-whisper-small"
        rejected = client.post("/transkriber", data={"modell": "borealis-4b"},
                               files={"lydfil": ("synthetic.wav", b"test", "audio/wav")})
        assert rejected.status_code == 422
        assert call.await_count == 1
