import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from frontend.auth import EntraAuthMiddleware


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setenv("NAIS_CLUSTER_NAME", "test-gcp")
    monkeypatch.setenv("NAIS_TOKEN_INTROSPECTION_ENDPOINT", "http://127.0.0.1:7164/api/v1/introspect")
    app = FastAPI()
    app.add_middleware(EntraAuthMiddleware)

    @app.get("/private")
    @app.get("/isAlive")
    @app.get("/isReady")
    def endpoint():
        return {"ok": True}

    @app.websocket("/ws/sanntid")
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_text("authenticated")
        await ws.close()

    return app


def mock_introspection(monkeypatch, data, status=200):
    original = httpx.AsyncClient
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json=data)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    return calls


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"},
    [("Authorization", "Bearer one"), ("Authorization", "Bearer two")]])
def test_missing_or_malformed_token_is_rejected(secured, headers):
    with TestClient(secured) as client:
        assert client.get("/private", headers=headers).status_code == 401
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/ws/sanntid", headers=httpx.Headers(headers)):
                pass
        assert error.value.code == 1008


@pytest.mark.parametrize("data,status,expected", [
    ({"active": False}, 200, 401),
    ({"active": True, "sub": "employee"}, 200, 200),
    ({"active": "true"}, 200, 503),
    ({}, 200, 503),
    ([], 200, 503),
    ({"active": True}, 500, 503),
])
def test_introspection_fail_closed_http_and_websocket(secured, monkeypatch, data, status, expected):
    calls = mock_introspection(monkeypatch, data, status)
    headers = {"Authorization": "Bearer synthetic-token"}
    with TestClient(secured) as client:
        response = client.get("/private", headers=headers)
        assert response.status_code == expected
        if expected == 200:
            with client.websocket_connect("/ws/sanntid", headers=headers) as ws:
                assert ws.receive_text() == "authenticated"
        else:
            with pytest.raises(WebSocketDisconnect) as error:
                with client.websocket_connect("/ws/sanntid", headers=headers):
                    pass
            assert error.value.code == (1013 if expected == 503 else 1008)
    assert len(calls) == 2
    assert all(b'"identity_provider":"entra_id"' in call.content for call in calls)


def test_only_health_probes_are_public(secured):
    with TestClient(secured) as client:
        for path in ("/isAlive", "/isReady"):
            assert client.get(path).status_code == 200
            assert client.post(path).status_code == 401
        for path in ("/", "/docs", "/openapi.json", "/static/app.js", "/system/status", "/isAlive/extra"):
            assert client.get(path).status_code == 401


def test_local_mode_and_deployment_configuration(secured, monkeypatch):
    monkeypatch.delenv("NAIS_CLUSTER_NAME")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    with TestClient(secured) as client:
        assert client.get("/private").status_code == 200
    monkeypatch.setenv("NAIS_CLUSTER_NAME", "prod-gcp")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.delenv("NAIS_TOKEN_INTROSPECTION_ENDPOINT")
    with pytest.raises(RuntimeError, match="introspection"):
        EntraAuthMiddleware(secured)


@pytest.mark.parametrize("failure", ["timeout", "invalid_json"])
def test_token_service_failure_denies_access(secured, monkeypatch, failure):
    original = httpx.AsyncClient

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("unavailable")
        return httpx.Response(200, content="invalid json")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    with TestClient(secured) as client:
        assert client.get("/private", headers={"Authorization": "Bearer synthetic"}).status_code == 503


def test_frontend_installs_authentication():
    from frontend.app import create_app

    assert EntraAuthMiddleware in [middleware.cls for middleware in create_app().user_middleware]
