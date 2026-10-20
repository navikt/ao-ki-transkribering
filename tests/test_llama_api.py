import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from worker import llama_api as api


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "test-model-key")
    calls = []

    async def upstream(request):
        calls.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        payload = json.loads(request.content)
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "<bos>" + payload["messages"][-1]["content"]})
        if request.url.path == "/tokenize":
            assert payload["add_special"] is True
            assert payload["parse_special"] is True
            return httpx.Response(200, json={"tokens": [1, 2, 3, 4]})
        if payload.get("stream"):
            return httpx.Response(200, content=b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
                                  headers={"content-type": "text/event-stream"})
        if payload.get("test_timeout"):
            raise httpx.ReadTimeout("private upstream details")
        return httpx.Response(200, json={"model": payload["model"], "choices": [
            {"message": {"role": "assistant", "content": "Hei"}, "finish_reason": "stop"}]})

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream),
                                    base_url="http://127.0.0.1:8001") as client:
            app.state.upstream = client
            yield

    monkeypatch.setattr(api.app.router, "lifespan_context", lifespan)
    with TestClient(api.app) as client:
        yield client, calls


AUTH = {"Authorization": "Bearer test-model-key"}


@pytest.mark.parametrize("stream_options", [{"stream": False}, {}])
def test_exact_counter_uses_native_template_without_running_inference(proxy, stream_options):
    client, calls = proxy
    payload = {"model": "borealis-12b", "messages": [{"role": "user", "content": "Hei"}],
               "transkriberit_count_tokens": True, **stream_options}
    assert client.post("/v1/chat/completions", json=payload).status_code == 401
    response = client.post("/v1/chat/completions", headers=AUTH, json=payload)
    assert response.status_code == 200
    assert json.loads(response.json()["choices"][0]["message"]["content"]) == {"type": "token_count", "input_tokens": 4}
    assert [request.url.path for request in calls] == ["/apply-template", "/tokenize"]
    assert all("authorization" not in request.headers for request in calls)
    assert client.post("/tokenize", headers=AUTH, json={"content": "Hei"}).status_code == 403


@pytest.mark.parametrize("messages", [None, [], [{"role": "user", "content": []}], [{"role": "tool", "content": "Hei"}]])
def test_invalid_counter_inputs_do_not_reach_native_server(proxy, messages):
    client, calls = proxy
    assert client.post("/v1/chat/completions", headers=AUTH, json={"model": "borealis-12b",
        "stream": False, "transkriberit_count_tokens": True, "messages": messages}).status_code == 400
    assert not calls


@pytest.mark.parametrize("stream", [True, None, "false", 0])
def test_counter_rejects_explicit_invalid_or_streaming_modes(proxy, stream):
    client, calls = proxy
    response = client.post("/v1/chat/completions", headers=AUTH, json={"model": "borealis-12b",
        "messages": [{"role": "user", "content": "Hei"}], "stream": stream,
        "transkriberit_count_tokens": True})
    assert response.status_code == 400
    assert not calls


@pytest.mark.parametrize("model", ["borealis-12b", "/models/borealis-12b"])
def test_gateway_model_alias_and_no_forwarded_credentials(proxy, model):
    client, calls = proxy
    response = client.post("/v1/chat/completions", headers=AUTH,
                           json={"model": model, "messages": [{"role": "user", "content": "Hei"}]})
    assert response.status_code == 200
    assert response.json()["model"] == "borealis-12b"
    assert "authorization" not in calls[-1].headers
    assert json.loads(calls[-1].content)["model"] == "borealis-12b"


def test_streaming_contract(proxy):
    client, _ = proxy
    response = client.post("/v1/chat/completions", headers=AUTH,
                           json={"model": "borealis-12b", "stream": True})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.endswith("data: [DONE]\n\n")


@pytest.mark.parametrize("path", ["/completion", "/props", "/slots", "/metrics", "/docs", "/"])
def test_native_routes_remain_inaccessible(proxy, path):
    client, calls = proxy
    assert client.get(path, headers=AUTH).status_code == 403
    assert not calls


def test_authentication_and_health(proxy):
    client, calls = proxy
    assert client.get("/v1/models").status_code == 401
    assert client.post("/v1/chat/completions", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert not calls
    assert client.get("/health").json()["quantization"] == "Q8_0"
    assert client.get("/v1/models", headers=AUTH).json()["data"][0]["id"] == "borealis-12b"


@pytest.mark.parametrize("payload", [[], {"model": []}, {"model": "other"}, {}])
def test_malformed_model_never_reaches_upstream(proxy, payload):
    client, calls = proxy
    assert client.post("/v1/chat/completions", headers=AUTH, json=payload).status_code == 400
    assert not calls


def test_body_limit_and_safe_timeout(proxy, monkeypatch):
    client, _ = proxy
    response = client.post("/v1/chat/completions", headers=AUTH,
                           json={"model": "borealis-12b", "test_timeout": True})
    assert response.status_code == 504
    assert "private upstream" not in response.text
    monkeypatch.setattr(api, "MAX_BODY_BYTES", 4)
    assert client.post("/v1/chat/completions", headers=AUTH, content=b"12345").status_code == 413


@pytest.mark.asyncio
async def test_admission_limit_is_held_until_stream_finishes():
    started, finish = asyncio.Event(), asyncio.Event()
    sent = []

    async def upstream(scope, receive, send):
        started.set()
        await finish.wait()

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions"}
    limited = api.RequestLimitMiddleware(upstream, capacity=1)
    first = asyncio.create_task(limited(scope, None, send))
    await started.wait()
    await limited(scope, None, send)
    assert sent[0]["status"] == 429
    assert limited.pending == 1
    finish.set()
    await first
    assert limited.pending == 0
