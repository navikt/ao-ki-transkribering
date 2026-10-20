from unittest.mock import AsyncMock, MagicMock
import json

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


@pytest.mark.asyncio
async def test_exact_counter_uses_same_authenticated_chat_route(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "type": "token_count", "input_tokens": 731,
        })}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(llm_client, "LLM_BACKEND", "openai")
    monkeypatch.setattr(llm_client, "authenticated_headers", AsyncMock(return_value={"Authorization": "Bearer scoped-test-key"}))
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    assert await llm_client.count_tokens("Regler", "Kilde", "borealis-12b") == 731
    assert requests[0].url.path == "/v1/chat/completions"
    body = json.loads(requests[0].content)
    assert body["extra_body"] == {"transkriberit_count_tokens": True}
    assert body["max_tokens"] == 1 and body["stream"] is False
    assert requests[0].headers["Authorization"] == "Bearer scoped-test-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,content", [(403, {}), (200, {"choices": [{"message": {"content": "Not a counter"}}]})])
async def test_unsupported_counter_falls_back_without_exposing_content(monkeypatch, status, content):
    original = httpx.AsyncClient
    monkeypatch.setattr(llm_client, "LLM_BACKEND", "openai")
    monkeypatch.setattr(llm_client, "authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=content)), **kwargs))
    assert await llm_client.count_tokens("Regler", "Privat kilde", "borealis-12b") is None


@pytest.mark.asyncio
async def test_structured_quotes_are_not_normalized_and_truncation_is_rejected(monkeypatch):
    replies = [{"choices": [{"message": {"content": '{"quote":"Eg jobba ikkje med dette."}'}, "finish_reason": "stop"}]},
               {"choices": [{"message": {"content": '{"quote":"Eg'}, "finish_reason": "length"}]}]
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0))

    original = httpx.AsyncClient
    monkeypatch.setattr(llm_client, "LLM_BACKEND", "openai")
    monkeypatch.setattr(llm_client, "authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    assert await llm_client.kall("Regler", "Kilde", raw=True, json_mode=True) == '{"quote":"Eg jobba ikkje med dette."}'
    assert bodies[0]["response_format"] == {"type": "json_object"}
    with pytest.raises(llm_client.IncompleteModelResponse):
        await llm_client.kall("Regler", "Kilde", raw=True, json_mode=True)


@pytest.mark.asyncio
async def test_structured_client_sends_named_strict_schema(monkeypatch):
    from frontend.grounded_summary import Audit

    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"checks":[]}'}, "finish_reason": "stop"}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(llm_client, "LLM_BACKEND", "openai")
    monkeypatch.setattr(llm_client, "authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    schema = Audit.model_json_schema()
    assert await llm_client.kall("Rules", "Source", raw=True, json_mode=True, response_schema=schema) == '{"checks":[]}'
    assert bodies[0]["temperature"] == 0
    assert bodies[0]["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "transkriberit_Audit", "strict": True, "schema": schema,
    }}


@pytest.mark.asyncio
async def test_token_counter_does_not_hide_authentication_failure(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(llm_client, "LLM_BACKEND", "openai")
    monkeypatch.setattr(llm_client, "authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(401)), **kwargs))
    with pytest.raises(httpx.HTTPStatusError) as error:
        await llm_client.count_tokens("Rules", "Source", "borealis-12b")
    assert error.value.response.status_code == 401


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


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["openai", "ollama"])
async def test_intermediate_note_output_limit_reaches_model(monkeypatch, backend):
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        if backend == "ollama":
            return httpx.Response(200, text=json.dumps({"response": "Faktanotater", "done": True}) + "\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": "Faktanotater"}}]})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(llm_client, "LLM_BACKEND", backend)
    monkeypatch.setattr(llm_client, "authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    assert await llm_client.kall("Regler", "Kilde", "valgt-modell", max_tokens=384) == "Faktanotater"
    assert payloads[0]["model"] == "valgt-modell"
    assert (payloads[0]["options"]["num_predict"] if backend == "ollama" else payloads[0]["max_tokens"]) == 384


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["openai", "ollama"])
async def test_detailed_summary_stream_reserves_and_sends_larger_output_budget(monkeypatch, backend):
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        if backend == "ollama":
            return httpx.Response(200, text=json.dumps({"response": "Møtereferat", "done": True}) + "\n")
        chunk = {"choices": [{"delta": {"content": "Møtereferat"}}]}
        return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(llm_client, "LLM_BACKEND", backend)
    monkeypatch.setitem(llm_client._OPTIONS, "num_ctx", 8192)
    monkeypatch.setitem(llm_client._OPTIONS, "num_predict", 1024)
    monkeypatch.setattr(llm_client, "authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    assert llm_client.input_token_budget() == 6656
    assert llm_client.input_token_budget(3072) == 4608
    events = [event async for event in llm_client.stream_tokens("Regler", "Kilde", "valgt-modell", max_tokens=3072)]
    assert events[-1] == ("", True, "Møtereferat")
    assert (payloads[0]["options"]["num_predict"] if backend == "ollama" else payloads[0]["max_tokens"]) == 3072


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
        assert response.status_code == {400: 502, 401: 502, 503: 503}[status]
