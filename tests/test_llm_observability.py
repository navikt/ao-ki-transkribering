import asyncio
import importlib.util
import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import yaml
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from shared.services import llm_observability as observations
from shared.services import llm_proxy_client as llm


@pytest.fixture
def exporter(monkeypatch):
    import langfuse

    real = langfuse.Langfuse
    exporter = InMemorySpanExporter()
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-" + uuid4().hex)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "PRIVATE_API_KEY")
    monkeypatch.setenv("LANGFUSE_ENVIRONMENT", "local")
    monkeypatch.setattr(langfuse, "Langfuse", lambda **kwargs: real(**kwargs, span_exporter=exporter))
    observations.start()
    assert observations._client is not None
    yield exporter
    observations.shutdown()


def spans(exporter):
    observations._client.flush()
    return exporter.get_finished_spans()


def test_disabled_never_initializes_sdk(monkeypatch):
    import langfuse
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    monkeypatch.setattr(langfuse, "Langfuse", lambda **kwargs: pytest.fail("SDK must remain disabled"))
    observations.start()
    with observations.track("summary"):
        pass
    assert observations._client is None


@pytest.mark.asyncio
async def test_real_client_exports_only_safe_metadata_and_provider_usage(exporter, monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(llm, "LLM_BACKEND", "openai")
    monkeypatch.setattr(llm, "authenticated_headers", AsyncMock(return_value={"Authorization": "PRIVATE_AUTH"}))
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "choices": [{"message": {"content": "PRIVATE_RESPONSE"}}],
            "usage": {"prompt_tokens": 101, "completion_tokens": 17, "secret": "PRIVATE_USAGE"},
        })), **kwargs))
    with observations.track("summary", template="veiledermote", variant="oppfolging"):
        assert await llm.kall("PRIVATE_SYSTEM", "PRIVATE_TRANSCRIPT", "borealis-12b", raw=True) == "PRIVATE_RESPONSE"
    exported = spans(exporter)
    assert len(exported) == 2
    call = next(span for span in exported if span.name == "llm_call")
    summary = next(span for span in exported if span.name == "summary")
    assert call.parent.span_id == summary.context.span_id
    assert call.context.trace_id == summary.context.trace_id
    assert json.loads(call.attributes["langfuse.observation.usage_details"]) == {"input": 101, "output": 17}
    assert summary.attributes["langfuse.observation.metadata.template"] == "veiledermote"
    payload = encode_spans(exported).SerializeToString()
    assert b"PRIVATE" not in payload
    assert all(set(span.resource.attributes) == {"service.name"} for span in exported)


@pytest.mark.asyncio
async def test_unknown_user_values_exception_messages_and_attributes_are_not_exported(exporter):
    @observations.generation
    async def failing(system, bruker, modell=None):
        observations._current.get().update(input="PRIVATE_INPUT", output="PRIVATE_OUTPUT",
                                           metadata={"unsafe": "PRIVATE_METADATA"})
        raise RuntimeError("PRIVATE_EXCEPTION")

    with pytest.raises(RuntimeError, match="PRIVATE_EXCEPTION"):
        with observations.track("summary", template="PRIVATE_TEMPLATE", variant="PRIVATE_VARIANT"):
            await failing("PRIVATE_SYSTEM", "PRIVATE_TRANSCRIPT", "PRIVATE_MODEL")
    exported = spans(exporter)
    assert len(exported) == 2
    assert all(span.attributes["langfuse.observation.metadata.outcome"] == "error" for span in exported)
    assert all(not span.events for span in exported)
    assert b"PRIVATE" not in encode_spans(exported).SerializeToString()


@pytest.mark.asyncio
async def test_stream_supports_separate_anext_tasks_and_final_close(exporter):
    @observations.generation_stream
    async def stream(system, bruker, modell=None):
        observations.record_usage({"prompt_tokens": True, "completion_tokens": -1})
        yield "PRIVATE_TOKEN", False, ""
        observations.record_usage({"prompt_tokens": 3, "completion_tokens": 2})
        yield "", True, "PRIVATE_RESPONSE"

    source = stream("PRIVATE_SYSTEM", "PRIVATE_TRANSCRIPT", "borealis-12b")
    assert (await asyncio.create_task(anext(source)))[0] == "PRIVATE_TOKEN"
    assert (await asyncio.create_task(anext(source)))[1] is True
    await asyncio.create_task(source.aclose())
    exported = spans(exporter)
    assert len(exported) == 1
    assert exported[0].attributes["langfuse.observation.metadata.outcome"] == "ok"
    assert b"PRIVATE" not in encode_spans(exported).SerializeToString()


@pytest.mark.asyncio
async def test_broken_sdk_cannot_break_generation_or_change_exception(monkeypatch):
    class Broken:
        def start_observation(self, **kwargs):
            raise RuntimeError("PRIVATE")
    monkeypatch.setattr(observations, "_client", Broken())

    @observations.generation
    async def call(system, bruker, modell=None):
        return "unchanged"
    assert await call("PRIVATE", "PRIVATE") == "unchanged"


@pytest.mark.parametrize("endpoint", ["http://external.example", "https://user:secret@host.example", "https://host.example?secret=x"])
def test_insecure_or_credential_bearing_endpoint_disables_export(monkeypatch, endpoint):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_BASE_URL", endpoint)
    observations.start()
    assert observations._client is None


def test_pilot_secrets_are_private_random_and_not_replaced(tmp_path):
    spec = importlib.util.spec_from_file_location("pilot", Path(__file__).parents[1] / "scripts/langfuse-pilot.py")
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    path = tmp_path / ".env"
    assert pilot.initialize(path)
    original = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not pilot.initialize(path)
    assert path.read_bytes() == original
    other = tmp_path / "other.env"
    pilot.initialize(other)
    assert other.read_bytes() != original
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="0600"):
        pilot.initialize(path)


def test_pilot_exposes_only_authenticated_web_on_loopback_and_keeps_datastores_persistent():
    compose = yaml.safe_load((Path(__file__).parents[1] / "observability/langfuse/compose.yaml").read_text())
    services = compose["services"]
    assert services["web"]["ports"] == ["127.0.0.1:3000:3000"]
    assert services["web"]["environment"]["AUTH_DISABLE_SIGNUP"] == "true"
    for name, service in services.items():
        if name != "web":
            assert not service.get("ports")
    for name in ("postgres", "clickhouse", "redis", "minio"):
        assert services[name]["volumes"]
        assert services[name]["healthcheck"]
    for name in ("web", "worker"):
        assert services[name]["environment"]["TELEMETRY_ENABLED"] == "false"
        assert services[name]["environment"]["LANGFUSE_IN_APP_AGENT_ENABLED"] == "false"


@pytest.mark.asyncio
async def test_cancelled_generation_remains_cancelled_and_has_no_exception_text(exporter):
    @observations.generation
    async def cancelled(system, bruker, modell=None):
        raise asyncio.CancelledError("PRIVATE_CANCEL_REASON")
    with pytest.raises(asyncio.CancelledError):
        await cancelled("PRIVATE_SYSTEM", "PRIVATE_TRANSCRIPT", "borealis-12b")
    exported = spans(exporter)
    assert len(exported) == 1
    assert exported[0].attributes["langfuse.observation.metadata.outcome"] == "cancelled"
    assert b"PRIVATE" not in encode_spans(exported).SerializeToString()
