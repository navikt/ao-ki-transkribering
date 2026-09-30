from unittest.mock import MagicMock

import pytest

import shared.services.llm_proxy_client as llm_client


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
