import asyncio
import hashlib
import secrets

import httpx
import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from frontend.auth import EntraAuthMiddleware
from frontend import password_gate
from frontend.password_gate import LoginLimiter, PasswordGateConfig, PasswordGateMiddleware, SESSION_SECONDS


@pytest.fixture
def gate_environment(monkeypatch):
    for name in ("NAIS_CLUSTER_NAME", "AUTH_REQUIRED", "ACCESS_PASSWORD_REQUIRED",
                 "ACCESS_PASSWORD", "ACCESS_SESSION_KEY", "FEEDBACK_ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    password = secrets.token_urlsafe(24)
    key = secrets.token_urlsafe(48)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(password_gate, "time", lambda: clock[0])
    return password, key, clock


@pytest.fixture
def build_app(monkeypatch, gate_environment):
    password, key, clock = gate_environment
    calls = []
    original = httpx.AsyncClient

    def introspect(request):
        calls.append(request)
        token = request.read()
        return httpx.Response(200, json={
            "active": True, "tid": "synthetic-tenant",
            "oid": "synthetic-two" if b"second" in token else "synthetic-one",
        })

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(introspect), **kwargs
    ))

    def build(enabled=True, auth=False):
        if enabled:
            monkeypatch.setenv("ACCESS_PASSWORD", password)
            monkeypatch.setenv("ACCESS_SESSION_KEY", key)
        if auth:
            monkeypatch.setenv("NAIS_CLUSTER_NAME", "test-gcp")
            monkeypatch.setenv("AUTH_REQUIRED", "false")
            monkeypatch.setenv("NAIS_TOKEN_INTROSPECTION_ENDPOINT", "http://introspection.invalid")
            monkeypatch.setenv("FEEDBACK_ALLOWED_ORIGINS", "https://demo.invalid")
        app = FastAPI()
        app.state.password_gate_config = PasswordGateConfig.from_env()
        app.add_middleware(PasswordGateMiddleware, config=app.state.password_gate_config)
        app.add_middleware(EntraAuthMiddleware)
        app.state.completed_sockets = 0

        @app.api_route("/private", methods=["GET", "HEAD", "POST", "PUT", "DELETE"])
        @app.api_route("/isAlive", methods=["GET", "HEAD", "POST"])
        @app.api_route("/isReady", methods=["GET", "HEAD", "POST"])
        @app.get("/static/app.js")
        def endpoint():
            return {"ok": True}

        @app.get("/stream")
        def stream(request: Request):
            async def chunks():
                yield b"started"
                clock[0] = request.state.access_password_expires_at
                yield b"finished"
            return StreamingResponse(chunks(), media_type="text/event-stream")

        @app.websocket("/ws")
        async def websocket(ws: WebSocket):
            await ws.accept()
            await ws.send_text("ready")
            try:
                while True:
                    await ws.send_text(await ws.receive_text())
            except WebSocketDisconnect:
                pass
            finally:
                app.state.completed_sockets += 1

        @app.websocket("/ws/done")
        async def completed_websocket(ws: WebSocket):
            await ws.accept()
            await ws.send_text("done")
            await ws.close(code=1000)

        app.state.introspection_calls = calls
        return app

    return build


def login(client, password, headers=None):
    return client.post("/login", data={"password": password}, headers=headers or {}, follow_redirects=False)


def test_default_disabled_preserves_local_behavior(build_app):
    with TestClient(build_app(enabled=False)) as client:
        assert client.get("/private").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/login").status_code == 404
        with client.websocket_connect("/ws/done") as ws:
            assert ws.receive_text() == "done"


@pytest.mark.parametrize("flag,password,key", [
    ("true", None, None), ("true", "", None), ("false", None, "long"),
    ("false", "password", None), ("true", "password", "short"),
    ("false", "", ""), ("false", None, ""),
])
def test_enabled_configuration_fails_closed(monkeypatch, gate_environment, flag, password, key):
    monkeypatch.setenv("ACCESS_PASSWORD_REQUIRED", flag)
    if password is not None:
        monkeypatch.setenv("ACCESS_PASSWORD", password)
    if key is not None:
        monkeypatch.setenv("ACCESS_SESSION_KEY", key)
    with pytest.raises(RuntimeError, match="ACCESS_"):
        PasswordGateConfig.from_env()


def test_utf8_key_length_and_flag_enablement(monkeypatch, gate_environment):
    password, _, _ = gate_environment
    monkeypatch.setenv("ACCESS_PASSWORD", password)
    monkeypatch.setenv("ACCESS_SESSION_KEY", "ø" * 16)
    monkeypatch.setenv("ACCESS_PASSWORD_REQUIRED", "false")
    assert PasswordGateConfig.from_env().enabled
    monkeypatch.setenv("ACCESS_SESSION_KEY", "ø" * 15)
    with pytest.raises(RuntimeError, match="32 UTF-8 bytes"):
        PasswordGateConfig.from_env()


@pytest.mark.parametrize("path", ["/private", "/static/app.js", "/docs", "/openapi.json",
                                 "/isAlive/extra", "/isAlive/", "/login/extra", "/login/"])
def test_every_nonexempt_http_path_is_blocked(build_app, path):
    with TestClient(build_app()) as client:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 401
        assert response.json()["reason"] == "access_password"
        assert response.headers["X-Access-Password-Required"] == "true"
        assert response.headers["Cache-Control"] == "no-store"


def test_only_exact_health_get_head_and_login_methods_are_exempt(build_app):
    with TestClient(build_app()) as client:
        for path in ("/isAlive", "/isReady"):
            assert client.get(path).status_code == 200
            assert client.head(path).status_code == 200
            assert client.post(path).status_code == 401
        assert client.get("/login").status_code == 200
        assert client.head("/login").content == b""
        assert client.put("/login").status_code == 401
        assert client.delete("/login").status_code == 401


def test_only_page_navigation_redirects_without_query(build_app):
    with TestClient(build_app()) as client:
        response = client.get("/?next=https://evil.invalid", headers={"Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["Location"] == "/login"
        for method in ("POST", "PUT", "DELETE"):
            assert client.request(method, "/private", headers={"Accept": "text/html"}).status_code == 401
        assert client.get("/private", headers={"Accept": "text/html"}).status_code == 401
        assert client.get("/static/app.js", headers={"Accept": "text/html"}).status_code == 401
        assert client.get("/docs", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 303
        assert client.get("/private", headers={
            "Accept": "text/html", "Sec-Fetch-Dest": "empty",
        }).status_code == 401


def test_entra_runs_before_gate_and_protects_login(build_app, gate_environment):
    app = build_app(auth=True)
    password, _, _ = gate_environment
    auth = {"Authorization": "Bearer synthetic"}
    with TestClient(app, base_url="https://demo.invalid") as client:
        assert client.get("/login").status_code == 401
        assert login(client, password).status_code == 401
        assert not app.state.introspection_calls
        assert client.get("/isAlive").status_code == 200
        assert client.get("/login", headers=auth).status_code == 200
        assert client.get("/private", headers=auth).headers["X-Access-Password-Required"] == "true"
        assert login(client, password, auth).status_code == 303
        assert client.get("/private", headers=auth).status_code == 200
        assert client.get("/private").status_code == 401
        assert client.get("/private", headers={"Authorization": "Bearer second"}).status_code == 401


def test_login_is_accessible_and_does_not_echo_or_log_secrets(build_app, gate_environment, caplog):
    password, key, _ = gate_environment
    wrong = secrets.token_urlsafe(24)
    with TestClient(build_app()) as client:
        page = client.get("/login")
        assert 'lang="nb"' in page.text
        assert "syntetiske data" in page.text
        assert '<label for="password">Passord</label>' in page.text
        assert 'type="password"' in page.text
        assert page.headers["Cache-Control"] == "no-store"
        failed = login(client, wrong)
        assert failed.status_code == 401
        assert 'role="alert"' in failed.text
        success = login(client, password)
        assert success.status_code == 303
        assert success.headers["Location"] == "/"
        for secret in (password, wrong, key):
            assert secret not in page.text + failed.text + success.text + caplog.text
        assert client.get("/private").status_code == 200


@pytest.mark.parametrize("auth,base_url,name,secure", [
    (False, "http://testserver", "ao_gate", False),
    (False, "https://testserver", "__Host-ao_gate", True),
    (True, "https://demo.invalid", "__Host-ao_gate", True),
    (True, "http://demo.invalid", "__Host-ao_gate", True),
])
def test_cookie_flags(build_app, gate_environment, auth, base_url, name, secure):
    password, _, _ = gate_environment
    with TestClient(build_app(auth=auth), base_url=base_url) as client:
        response = login(client, password, {"Authorization": "Bearer synthetic"} if auth else {})
        cookie = response.headers["Set-Cookie"]
        assert cookie.startswith(name + "=")
        assert ("; Secure" in cookie) == secure
        assert "; HttpOnly" in cookie
        assert "; Path=/" in cookie
        assert "; SameSite=lax" in cookie
        assert f"; Max-Age={SESSION_SECONDS}" in cookie
        assert "Domain=" not in cookie


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.invalid"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://demo.invalid", "Sec-Fetch-Site": "cross-site"},
])
def test_cross_site_login_is_denied(build_app, gate_environment, headers):
    password, _, _ = gate_environment
    with TestClient(build_app(auth=True), base_url="https://demo.invalid") as client:
        response = login(client, password, {"Authorization": "Bearer synthetic", **headers})
        assert response.status_code == 403
        assert "Set-Cookie" not in response.headers


def test_allowed_origin_and_missing_authenticated_client_origin(build_app, gate_environment):
    password, _, _ = gate_environment
    with TestClient(build_app(auth=True), base_url="https://demo.invalid") as client:
        for extra in ({}, {"Origin": "https://demo.invalid", "Sec-Fetch-Site": "same-origin"}):
            assert login(client, password, {"Authorization": "Bearer synthetic", **extra}).status_code == 303


def test_deployment_never_uses_raw_host_for_origin_allowlist(build_app, gate_environment, monkeypatch):
    password, _, _ = gate_environment
    app = build_app(auth=True)
    monkeypatch.delenv("FEEDBACK_ALLOWED_ORIGINS")
    with TestClient(app, base_url="https://evil.invalid") as client:
        assert login(client, password, {
            "Authorization": "Bearer synthetic", "Origin": "https://evil.invalid",
        }).status_code == 403


@pytest.mark.parametrize("body,content_type,status", [
    ('{"password":"do-not-echo"}', "application/json", 415),
    ("password=do-not-echo&password=again", "application/x-www-form-urlencoded", 400),
    ("password=do-not-echo&extra=field", "application/x-www-form-urlencoded", 400),
    ("other=do-not-echo", "application/x-www-form-urlencoded", 400),
    ("password=" + "x" * 513, "application/x-www-form-urlencoded", 400),
    ("password=" + "x" * 2048, "application/x-www-form-urlencoded", 413),
    (b"password=\xff", "application/x-www-form-urlencoded", 400),
    ("password=%FF", "application/x-www-form-urlencoded", 400),
    ("", "application/x-www-form-urlencoded", 400),
])
def test_bounded_form_validation_without_input_echo(build_app, body, content_type, status, caplog):
    with TestClient(build_app()) as client:
        response = client.post("/login", content=body, headers={"Content-Type": content_type})
        assert response.status_code == status
        assert "do-not-echo" not in response.text + caplog.text


@pytest.mark.asyncio
async def test_body_stream_is_bounded_even_without_content_length(build_app):
    app = build_app()
    chunks = [b"password=" + b"x" * 1000, b"x" * 1100, b"never-read"]
    read = []
    messages = []

    async def receive():
        chunk = chunks[len(read)]
        read.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "method": "POST", "path": "/login", "raw_path": b"/login",
             "scheme": "http", "query_string": b"", "root_path": "", "http_version": "1.1",
             "server": ("testserver", 80), "client": ("testclient", 123),
             "headers": [(b"content-type", b"application/x-www-form-urlencoded")]}
    await app(scope, receive, send)
    assert messages[0]["status"] == 413
    assert len(read) == 2


def test_principal_rate_limit_returns_retry_after_and_recovers(build_app, gate_environment, monkeypatch):
    monotonic = [100.0]
    monkeypatch.setattr(password_gate, "monotonic", lambda: monotonic[0])
    password, _, _ = gate_environment
    with TestClient(build_app()) as client:
        for _ in range(LoginLimiter.PRINCIPAL_LIMIT):
            assert login(client, "wrong").status_code == 401
        limited = login(client, password)
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["Retry-After"]) <= 61
        monotonic[0] += LoginLimiter.WINDOW_SECONDS
        assert login(client, password).status_code == 303


def test_global_limit_and_principal_storage_are_bounded(gate_environment):
    limiter = LoginLimiter()
    for number in range(limiter.GLOBAL_LIMIT):
        assert limiter.consume(hashlib.sha256(str(number).encode()).hexdigest()) == 0
    assert limiter.consume(hashlib.sha256(b"another").hexdigest()) > 0
    assert len(limiter.principals) <= limiter.MAX_PRINCIPALS
    limiter = LoginLimiter()
    limiter.MAX_PRINCIPALS = 2
    assert limiter.consume("one") == 0
    assert limiter.consume("two") == 0
    assert limiter.consume("three") > 0
    assert len(limiter.principals) == 2


def test_tampering_duplicate_and_oversized_cookies_are_rejected(build_app, gate_environment):
    password, _, _ = gate_environment
    with TestClient(build_app()) as client:
        token = login(client, password).cookies["ao_gate"]
        client.cookies.clear()
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        for cookie in (f"ao_gate={tampered}", f"ao_gate={token}; ao_gate={token}",
                       "ao_gate=" + "x" * 9000, "ao_gate=" + "1." * 200):
            assert client.get("/private", headers={"Cookie": cookie}).status_code == 401


def test_server_enforces_exact_eight_hour_expiry(build_app, gate_environment):
    password, _, clock = gate_environment
    with TestClient(build_app()) as client:
        token = login(client, password).cookies["ao_gate"]
        client.cookies.clear()
        headers = {"Cookie": f"ao_gate={token}"}
        clock[0] += SESSION_SECONDS - 1
        assert client.get("/private", headers=headers).status_code == 200
        clock[0] += 1
        assert client.get("/private", headers=headers).status_code == 401


def test_signed_future_or_excessive_lifetime_tokens_are_rejected(gate_environment, monkeypatch):
    password, key, clock = gate_environment
    monkeypatch.setenv("ACCESS_PASSWORD", password)
    monkeypatch.setenv("ACCESS_SESSION_KEY", key)
    gate = PasswordGateMiddleware(FastAPI())
    owner = hashlib.sha256(b"local").hexdigest()
    now = int(clock[0])
    for issued, expiry in ((now + 1, now + 1 + SESSION_SECONDS), (now, now + SESSION_SECONDS + 1),
                           (now, now), (now - SESSION_SECONDS, now)):
        payload = f"1.{issued}.{expiry}.{owner}.{gate.fingerprint}"
        assert gate._verify(payload + "." + gate._sign(payload), owner) is None


def test_password_and_signing_key_rotation_invalidate_sessions(build_app, gate_environment, monkeypatch):
    password, key, _ = gate_environment
    with TestClient(build_app()) as client:
        token = login(client, password).cookies["ao_gate"]
    for changed_password, changed_key in ((secrets.token_urlsafe(24), key),
                                           (password, secrets.token_urlsafe(48))):
        monkeypatch.setenv("ACCESS_PASSWORD", changed_password)
        monkeypatch.setenv("ACCESS_SESSION_KEY", changed_key)
        app = build_app(enabled=False)
        with TestClient(app) as client:
            assert client.get("/private", headers={"Cookie": f"ao_gate={token}"}).status_code == 401


def test_required_auth_never_falls_back_to_local_owner(gate_environment, monkeypatch):
    password, key, _ = gate_environment
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ACCESS_PASSWORD", password)
    monkeypatch.setenv("ACCESS_SESSION_KEY", key)
    app = FastAPI()
    app.add_middleware(PasswordGateMiddleware)
    with TestClient(app) as client:
        assert login(client, password).status_code == 401


def test_authorized_http_stream_can_finish_after_expiry(build_app, gate_environment):
    password, _, _ = gate_environment
    with TestClient(build_app()) as client:
        assert login(client, password).status_code == 303
        response = client.get("/stream")
        assert response.status_code == 200
        assert response.content == b"startedfinished"
        assert client.get("/private").status_code == 401


def test_websocket_requires_both_gates_and_allowed_origin(build_app, gate_environment):
    password, _, _ = gate_environment
    headers = {"Authorization": "Bearer synthetic", "Origin": "https://demo.invalid"}
    with TestClient(build_app(auth=True), base_url="https://demo.invalid") as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("wss://demo.invalid/ws", headers=headers):
                pass
        assert error.value.code == 1008
        assert login(client, password, headers).status_code == 303
        for denied in ({}, {"Origin": "https://demo.invalid"},
                       {"Authorization": "Bearer synthetic"},
                       {**headers, "Origin": "https://evil.invalid"},
                       {**headers, "Authorization": "Bearer second"}):
            with pytest.raises(WebSocketDisconnect) as error:
                with client.websocket_connect("wss://demo.invalid/ws", headers=denied):
                    pass
            assert error.value.code == 1008
        with client.websocket_connect("wss://demo.invalid/ws", headers=headers) as ws:
            assert ws.receive_text() == "ready"
            ws.send_text("synthetic message")
            assert ws.receive_text() == "synthetic message"
        with client.websocket_connect("wss://demo.invalid/ws/done", headers=headers) as ws:
            assert ws.receive_text() == "done"
            assert ws.receive()["code"] == 1000


def test_websocket_is_cancelled_and_closed_at_server_expiry(build_app, gate_environment, monkeypatch):
    password, _, clock = gate_environment
    trigger = asyncio.Event()

    async def expire(expiry):
        await trigger.wait()
        clock[0] = expiry

    monkeypatch.setattr(password_gate, "_wait_until", expire)
    app = build_app()
    with TestClient(app) as client:
        assert login(client, password).status_code == 303
        with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_text() == "ready"
            ws.portal.call(trigger.set)
            assert ws.receive() == {"type": "websocket.close", "code": 1008}
        assert app.state.completed_sockets == 1


@pytest.mark.asyncio
async def test_expired_socket_closes_before_cleanup_and_does_not_close_twice(gate_environment, monkeypatch):
    password, key, clock = gate_environment
    monkeypatch.setenv("ACCESS_PASSWORD", password)
    monkeypatch.setenv("ACCESS_SESSION_KEY", key)
    expire_now = asyncio.Event()
    accepted = asyncio.Event()
    closed = asyncio.Event()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    messages = []

    async def expiry_timer(expiry):
        await expire_now.wait()
        clock[0] = expiry

    async def application(scope, receive, send):
        await send({"type": "websocket.accept"})
        accepted.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await finish_cleanup.wait()
            await send({"type": "websocket.send", "text": "must not be sent after expiry"})
            await send({"type": "websocket.close", "code": 1000})

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "websocket.close":
            closed.set()

    monkeypatch.setattr(password_gate, "_wait_until", expiry_timer)
    gate = PasswordGateMiddleware(application)
    owner = hashlib.sha256(b"local").hexdigest()
    scope = {"type": "websocket", "path": "/ws", "scheme": "ws", "query_string": b"",
             "root_path": "", "server": ("testserver", 80),
             "headers": [(b"host", b"testserver"), (b"origin", b"http://testserver"),
                         (b"cookie", f"ao_gate={gate._issue(owner)}".encode("ascii"))]}
    task = asyncio.create_task(gate(scope, receive, send))
    try:
        await asyncio.wait_for(accepted.wait(), timeout=2)
        expire_now.set()
        await asyncio.wait_for(closed.wait(), timeout=2)
        await asyncio.wait_for(cleanup_started.wait(), timeout=2)
        assert not task.done()
        assert messages[-1] == {"type": "websocket.close", "code": 1008}
        finish_cleanup.set()
        await asyncio.wait_for(task, timeout=2)
        assert messages == [{"type": "websocket.accept"}, {"type": "websocket.close", "code": 1008}]
    finally:
        finish_cleanup.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_real_app_validates_config_and_keeps_metrics_entra_gate_order(gate_environment, monkeypatch):
    from frontend.app import create_app
    from frontend.monitoring import HttpMetricsMiddleware

    app = create_app()
    classes = [middleware.cls for middleware in app.user_middleware]
    assert classes[:3] == [HttpMetricsMiddleware, EntraAuthMiddleware, PasswordGateMiddleware]
    assert not app.state.password_gate_config.enabled
    monkeypatch.setenv("ACCESS_PASSWORD_REQUIRED", "true")
    with pytest.raises(RuntimeError, match="ACCESS_PASSWORD"):
        create_app()


def test_password_sessions_use_the_same_identity_as_existing_summary_jobs(build_app, gate_environment):
    from frontend.routes.referat import _job_owner

    password, _, _ = gate_environment
    app = build_app(auth=True)
    request = Request({"type": "http", "state": {"user": {
        "tid": "synthetic-tenant", "oid": "synthetic-one",
    }}})
    job_owner = _job_owner(request)
    gate = PasswordGateMiddleware(app, config=app.state.password_gate_config)
    with TestClient(app, base_url="https://demo.invalid") as client:
        # A session issued for the pre-existing job identity must still work.
        token = gate._issue(job_owner)
        assert client.get("/private", headers={
            "Authorization": "Bearer synthetic", "Cookie": f"__Host-ao_gate={token}",
        }).status_code == 200
        response = login(client, password, {"Authorization": "Bearer synthetic"})
        assert response.status_code == 303
        assert response.cookies["__Host-ao_gate"].split(".")[3] == job_owner
