import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

import shared.metrics as telemetry_module
from frontend.auth import EntraAuthMiddleware
from frontend.monitoring import HttpMetricsMiddleware
from frontend.summary_jobs import SummaryJobs
from shared.metrics import Metrics, observe, observe_stream
from shared.services.jobs import JobStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def telemetry(monkeypatch):
    value = Metrics()
    monkeypatch.setattr(telemetry_module, "metrics", value)
    return value


def sample(telemetry, name, **labels):
    return telemetry.registry.get_sample_value("transkriberit_" + name, labels)


def test_http_uses_route_templates_and_never_content(telemetry, monkeypatch):
    import frontend.monitoring as monitoring

    logs = []
    monkeypatch.setattr(monitoring, "event", lambda name, **fields: logs.append(fields))
    app = FastAPI()

    @app.post("/jobs/{key}")
    async def job(key: str):
        return {"secret_result": "do not log this"}

    app.add_middleware(HttpMetricsMiddleware, routes=["/jobs/{key}"], telemetry=telemetry)
    with TestClient(app) as client:
        for index in range(5):
            assert client.post(f"/jobs/private-{index}?token=private-token",
                               headers={"Authorization": "Bearer private-key"},
                               json={"transcript": "private meeting"}).status_code == 200
        assert client.get("/unknown-private-path").status_code == 404
    assert sample(telemetry, "http_requests_total", method="POST", route="/jobs/{key}", status="200") == 5
    assert sample(telemetry, "http_requests_total", method="GET", route="unmatched", status="404") == 1
    assert sample(telemetry, "http_inflight") == 0
    exported = generate_latest(telemetry.registry).decode() + json.dumps(logs)
    assert all(secret not in exported for secret in ["private-", "secret_result", "do not log this"])


def test_authentication_is_not_bypassed_for_metrics(telemetry, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("NAIS_TOKEN_INTROSPECTION_ENDPOINT", "http://token.invalid/introspect")
    app = FastAPI()
    app.add_middleware(EntraAuthMiddleware)
    app.add_middleware(HttpMetricsMiddleware, routes=["/"], telemetry=telemetry)
    with TestClient(app) as client:
        for path in ("/", "/metrics", "/internal/metrics"):
            assert client.get(path).status_code == 401
    assert sample(telemetry, "http_requests_total", method="GET", route="unmatched", status="401") == 3


def test_http_finishes_before_background_work_and_handles_failure(telemetry):
    app = FastAPI()

    @app.get("/background")
    async def background(tasks: BackgroundTasks):
        def work():
            assert sample(telemetry, "http_inflight") == 0
            assert sample(telemetry, "http_requests_total", method="GET", route="/background", status="200") == 1
        tasks.add_task(work)
        return {"ok": True}

    @app.get("/failure")
    def failure():
        raise RuntimeError("private exception body")

    app.add_middleware(HttpMetricsMiddleware, routes=["/background", "/failure"], telemetry=telemetry)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/background").status_code == 200
        assert client.get("/failure").status_code == 500
    assert sample(telemetry, "http_requests_total", method="GET", route="/failure", status="500") == 1
    assert sample(telemetry, "http_inflight") == 0
    assert "private" not in generate_latest(telemetry.registry).decode()


async def test_http_cancellation_and_websocket_passthrough(telemetry):
    async def cancelled(scope, receive, send):
        raise asyncio.CancelledError()

    wrapper = HttpMetricsMiddleware(cancelled, routes=[], telemetry=telemetry)
    with pytest.raises(asyncio.CancelledError):
        await wrapper({"type": "http", "method": "PRIVATE"}, None, None)
    assert sample(telemetry, "http_requests_total", method="OTHER", route="unmatched", status="499") == 1
    assert sample(telemetry, "http_inflight") == 0

    seen = []
    async def websocket(scope, receive, send):
        seen.append(scope["type"])
    await HttpMetricsMiddleware(websocket, [], telemetry)({"type": "websocket"}, None, None)
    assert seen == ["websocket"]


@pytest.mark.parametrize("status,expected", [(401, "auth"), (403, "auth"), (429, "busy"),
                                         (502, "unavailable"), (503, "unavailable"), (400, "error")])
async def test_model_errors_have_fixed_labels(telemetry, status, expected):
    @observe("llm_generate")
    async def call():
        request = httpx.Request("POST", "https://private.invalid/?token=secret")
        httpx.Response(status, request=request, text="private model result").raise_for_status()

    with pytest.raises(httpx.HTTPStatusError):
        await call()
    assert sample(telemetry, "operations_total", operation="llm_generate", outcome=expected) == 1
    assert sample(telemetry, "operations_inflight", operation="llm_generate") == 0
    assert "private" not in generate_latest(telemetry.registry).decode()


async def test_timeout_success_and_cancellation(telemetry):
    @observe("speech_request")
    async def call(error=None):
        if error:
            raise error
        return "private result"

    assert await call() == "private result"
    for error, expected in [(httpx.ReadTimeout("private"), "timeout"),
                            (asyncio.CancelledError(), "cancelled")]:
        with pytest.raises(type(error)):
            await call(error)
        assert sample(telemetry, "operations_total", operation="speech_request", outcome=expected) == 1
    assert sample(telemetry, "operations_total", operation="speech_request", outcome="ok") == 1
    assert sample(telemetry, "operations_inflight", operation="speech_request") == 0
    with pytest.raises(ValueError):
        telemetry.record("private operation", "ok", 1)
    with pytest.raises(ValueError):
        telemetry.record("speech_request", "private outcome", 1)


async def test_stream_completion_close_and_incomplete_stream(telemetry):
    @observe_stream("llm_stream")
    async def stream(done):
        yield ("private token", False, "")
        if done:
            yield ("", True, "private output")

    complete = stream(True)
    await anext(complete)
    await anext(complete)
    await complete.aclose()
    assert sample(telemetry, "operations_total", operation="llm_stream", outcome="ok") == 1
    interrupted = stream(True)
    await anext(interrupted)
    await interrupted.aclose()
    assert sample(telemetry, "operations_total", operation="llm_stream", outcome="cancelled") == 1
    assert [item async for item in stream(False)]
    assert sample(telemetry, "operations_total", operation="llm_stream", outcome="error") == 1
    assert sample(telemetry, "operations_inflight", operation="llm_stream") == 0


async def test_summary_queue_idempotency_capacity_and_waiting_cancellation(telemetry, tmp_path):
    service = SummaryJobs(JobStore(tmp_path), concurrency=1, capacity=2, telemetry=telemetry)
    release = asyncio.Event()

    async def generate():
        await release.wait()
        yield {"type": "ferdig", "tekst": "private result"}

    one, two, three = (str(uuid4()) for _ in range(3))
    for key in (one, two):
        service.submit(key, "private owner", "private fingerprint", {}, generate, lambda _: "Failed")
    service.submit(one, "private owner", "private fingerprint", {}, generate, lambda _: "Failed")
    assert sample(telemetry, "summary_queued") == 2
    with pytest.raises(HTTPException) as error:
        service.submit(three, "private owner", "private fingerprint", {}, generate, lambda _: "Failed")
    assert error.value.status_code == 429
    assert sample(telemetry, "summary_rejected_total") == 1
    await asyncio.sleep(0)
    assert sample(telemetry, "summary_queued") == 1
    assert sample(telemetry, "operations_inflight", operation="summary_job") == 1
    assert sample(telemetry, "summary_oldest_queued_timestamp_seconds") > 0
    service.tasks[two].cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sample(telemetry, "summary_queued") == 0
    assert sample(telemetry, "summary_oldest_queued_timestamp_seconds") == 0
    release.set()
    await asyncio.gather(*service.tasks.values(), return_exceptions=True)
    await asyncio.sleep(0)
    assert sample(telemetry, "operations_total", operation="summary_job", outcome="ok") == 1
    assert sample(telemetry, "operations_total", operation="summary_job", outcome="cancelled") == 1
    assert sample(telemetry, "operations_inflight", operation="summary_job") == 0
    assert "private" not in generate_latest(telemetry.registry).decode()


async def test_summary_immediate_cancellation_and_generation_error(telemetry, tmp_path):
    service = SummaryJobs(JobStore(tmp_path), telemetry=telemetry)

    async def fail():
        raise RuntimeError("private result")
        yield

    key = str(uuid4())
    service.submit(key, "owner", "fingerprint", {}, fail, lambda _: "Failed")
    await service.shutdown()
    await asyncio.sleep(0)
    assert sample(telemetry, "summary_queued") == 0
    assert service.status(key, "owner")["status"] == "feil"
    assert sample(telemetry, "operations_total", operation="summary_job", outcome="cancelled") == 1
    key = str(uuid4())
    service.submit(key, "owner", "fingerprint", {}, fail, lambda _: "Failed")
    await asyncio.gather(*service.tasks.values())
    assert sample(telemetry, "operations_total", operation="summary_job", outcome="error") == 1
    assert sample(telemetry, "operations_inflight", operation="summary_job") == 0


async def test_metrics_listener_is_opt_in_and_closed(monkeypatch):
    import frontend.monitoring as monitoring

    monkeypatch.delenv("METRICS_ENABLED", raising=False)
    assert monitoring.start_metrics_server() is None
    called = []
    httpd = SimpleNamespace(shutdown=lambda: called.append("shutdown"), server_close=lambda: called.append("close"))
    thread = SimpleNamespace(join=lambda: called.append("join"))
    def start(port, **kwargs):
        assert port == 9091 and kwargs["addr"] == "0.0.0.0"
        return httpd, thread
    monkeypatch.setattr(monitoring, "start_http_server", start)
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_PORT", "9091")
    monkeypatch.setenv("METRICS_HOST", "0.0.0.0")
    await monitoring.stop_metrics_server(monitoring.start_metrics_server())
    assert called == ["shutdown", "close", "join"]
    monkeypatch.setenv("METRICS_PORT", "87654321")
    with pytest.raises(ValueError):
        monitoring.start_metrics_server()


def test_manifests_scrape_a_separate_port_without_disabling_auth():
    for env in ("dev", "prod"):
        spec = yaml.safe_load((ROOT / f".nais/{env}.yaml").read_text().replace("{{image}}", "test-image"))["spec"]
        assert spec["prometheus"] == {"enabled": True, "path": "/metrics", "port": "9091"}
        assert spec["port"] == 8765
        assert {item["name"]: item["value"] for item in spec["env"]}["METRICS_ENABLED"] == "true"
        assert spec["azure"]["sidecar"]["autoLogin"] is True
    rule = yaml.safe_load((ROOT / ".nais/alerts.yaml").read_text())
    for item in rule["spec"]["groups"][0]["rules"]:
        assert item["labels"]["namespace"] == "ao-ki-taskforce"
        assert item["for"]
        assert "namespace=\"ao-ki-taskforce\"" in item["expr"]


def test_dashboard_and_deploy_workflow_are_consistent():
    dashboard = json.loads((ROOT / "observability/dashboard.json").read_text())
    assert dashboard["uid"] == "transkriberit-operations"
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == "${DS_PROMETHEUS}"
        for target in panel["targets"]:
            assert 'namespace="ao-ki-taskforce"' in target["expr"]
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    for env in ("dev", "prod"):
        deployment = workflow["jobs"]["deploy-" + env]["steps"][-1]
        assert deployment["env"]["RESOURCE"] == f".nais/{env}.yaml,.nais/alerts.yaml"


async def test_real_speech_retry_path_counts_once_and_preserves_privacy(telemetry, monkeypatch, caplog):
    from shared.services import transkripsjon_client as speech
    from shared import diagnostics

    monkeypatch.setattr(speech, "metrics", telemetry)
    monkeypatch.setattr(speech, "TRANSKRIPSJON_API_PATH", "/v1/audio/transcriptions")
    attempts = []

    def respond(request):
        attempts.append(True)
        return httpx.Response(503 if len(attempts) < 3 else 200,
                              json={"text": "PRIVATE RESULT", "segments": []})

    async def headers(url, values):
        return {"Authorization": "Bearer PRIVATE CREDENTIAL"}

    async def sleep(_):
        pass

    original = httpx.AsyncClient
    monkeypatch.setattr(speech.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(speech, "authenticated_headers", headers)
    monkeypatch.setattr(speech.asyncio, "sleep", sleep)
    diagnostics.logger.addHandler(caplog.handler)
    try:
        result = await speech._call_transkripsjon_service(
            filename="PRIVATE FILENAME", content=b"PRIVATE AUDIO", n_talere=0,
            service_url="https://model.invalid", session_id="PRIVATE SESSION")
    finally:
        diagnostics.logger.removeHandler(caplog.handler)
    assert result.tekst == "PRIVATE RESULT"
    assert len(attempts) == 3
    assert sample(telemetry, "speech_retries_total") == 2
    assert sample(telemetry, "operations_total", operation="speech_request", outcome="ok") == 1
    assert "PRIVATE" not in generate_latest(telemetry.registry).decode()
    assert "PRIVATE" not in caplog.text


async def test_real_llm_client_counts_access_failure_without_content(telemetry, monkeypatch):
    from shared.services import llm_proxy_client as llm

    monkeypatch.setattr(llm, "LLM_BACKEND", "openai")

    async def headers(url, values):
        return {"Authorization": "Bearer PRIVATE CREDENTIAL"}

    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, text="PRIVATE RESULT")), **kwargs))
    monkeypatch.setattr(llm, "authenticated_headers", headers)
    with pytest.raises(httpx.HTTPStatusError):
        await llm.kall("PRIVATE PROMPT", "PRIVATE SOURCE")
    assert sample(telemetry, "operations_total", operation="llm_generate", outcome="auth") == 1
    assert "PRIVATE" not in generate_latest(telemetry.registry).decode()


def test_actual_app_route_templates_do_not_include_job_ids(monkeypatch):
    from frontend.app import create_app
    from frontend.monitoring import metrics

    monkeypatch.delenv("NAIS_CLUSTER_NAME", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    key = str(uuid4())
    # No lifespan: this route-label check must not start local models or cleanup jobs.
    client = TestClient(create_app())
    try:
        assert client.get(f"/status/{key}").status_code == 404
    finally:
        client.close()
    assert key not in generate_latest(metrics.registry).decode()
    assert sample(metrics, "http_requests_total", method="GET", route="/status/{jobb_id}", status="404") >= 1
