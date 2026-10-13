from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import shared.services.llm_proxy_client as llm_client
from frontend.routes.referat import router


class _FakeResponse:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.get = MagicMock(side_effect=self._get)

    async def _get(self, url: str):
        return _FakeResponse(
            {
                "models": [
                    {"name": "qwen3:8b"},
                    {"model": "llama3.1:8b"},
                ]
            }
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_ollama_status_uses_tags_endpoint(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_BACKEND", "ollama")
    monkeypatch.setattr(llm_client, "LLM_MODELL", "qwen3:8b")
    monkeypatch.setattr(llm_client, "OLLAMA_URL", "http://ollama.test")
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", _FakeAsyncClient)

    status = await llm_client.hent_status()

    assert status.tilgjengelig is True
    assert status.standard_modell == "qwen3:8b"
    assert [modell.id for modell in status.modeller] == ["qwen3:8b", "llama3.1:8b"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status,message,expected", [
    (400, "ContextWindowExceededError: maximum context length is 768 tokens", "tekstgrense"),
    (401, "Unauthorized", "401"),
    (503, "Unavailable", "ikke tilgjengelig"),
])
def test_model_http_errors_return_readable_errors(monkeypatch, stream, status, message, expected):
    class ErrorBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            import json

            yield json.dumps({"error": {"message": message}}).encode()

    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, stream=ErrorBody())
    )
    monkeypatch.setattr(llm_client, "LLM_BACKEND", "openai")
    monkeypatch.setattr(llm_client, "AI_PROXY_URL", "https://model.test")
    monkeypatch.setattr(llm_client, "authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: client_class(transport=transport, **kwargs))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/referat/stream" if stream else "/referat", json={
            "scenario": "utfyllende_sammendrag", "transkripsjon": "Vi avtaler et nytt mote."
        })
    assert expected in response.text
    if stream:
        assert response.status_code == 200
        assert '"type": "feil"' in response.text
        assert '"type": "ferdig"' not in response.text
    else:
        assert response.status_code == {400: 400, 401: 502, 503: 503}[status]
