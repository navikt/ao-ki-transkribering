import asyncio
import json
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


@pytest.mark.parametrize("model", ["borealis-4b", "borealis-12b",
                                   "borealis-4b:latest", "borealis-12b:latest"])
def test_model_metadata_uses_canonical_allowlisted_names(exporter, model):
    with observations.track("llm_call", model=model):
        pass
    assert spans(exporter)[0].attributes["langfuse.observation.model.name"] == model.removesuffix(":latest")


@pytest.mark.parametrize("template", ["basis_0_1", "stikkord_0_1", "utfyllende_0_1",
                                      "basis_0_2", "stikkord_0_2", "utfyllende_0_2"])
def test_versioned_prompts_are_safe_metadata(exporter, template):
    with observations.track("summary", template=template):
        pass
    assert spans(exporter)[0].attributes["langfuse.observation.metadata.template"] == template


def test_metadata_lookalikes_are_not_exported(exporter):
    with observations.track("summary", template="basis_0_2_PRIVATE"):
        with observations.track("llm_call", model="borealis-4b:PRIVATE"):
            pass
    assert b"PRIVATE" not in encode_spans(spans(exporter)).SerializeToString()


@pytest.mark.asyncio
async def test_summary_job_exports_nested_calls_without_job_data(exporter, tmp_path):
    from frontend.summary_jobs import SummaryJobs
    from shared.services.jobs import JobStore

    @observations.generation
    async def model(system, bruker, modell=None):
        observations.record_usage({"prompt_tokens": 10, "completion_tokens": 5})
        return "PRIVATE_RESULT"

    async def generate():
        result = await model("PRIVATE_PROMPT", "PRIVATE_TRANSCRIPT", "borealis-4b")
        yield {"type": "ferdig", "tekst": result}

    service = SummaryJobs(JobStore(tmp_path))
    job_id = str(uuid4())
    service.submit(job_id, "PRIVATE_OWNER", "PRIVATE_FINGERPRINT",
                   {"scenario": "basis_0_2", "transkripsjon": "PRIVATE_TRANSCRIPT"},
                   generate, lambda exc: "PRIVATE_ERROR")
    await asyncio.gather(*service.tasks.values())
    exported = spans(exporter)
    summary = next(span for span in exported if span.name == "summary")
    call = next(span for span in exported if span.name == "llm_call")
    assert call.parent.span_id == summary.context.span_id
    assert summary.attributes["langfuse.observation.metadata.template"] == "basis_0_2"
    assert call.attributes["langfuse.observation.model.name"] == "borealis-4b"
    assert b"PRIVATE" not in encode_spans(exported).SerializeToString()
    assert job_id.encode() not in encode_spans(exported).SerializeToString()


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


def test_only_production_app_enables_tracing_and_preserves_existing_secrets():
    root = Path(__file__).parents[1]
    prod = yaml.safe_load((root / ".nais/prod.yaml").read_text().replace("{{image}}", "test-image"))["spec"]
    dev = yaml.safe_load((root / ".nais/dev.yaml").read_text().replace("{{image}}", "test-image"))["spec"]
    env = {item["name"]: item["value"] for item in prod["env"]}
    assert env["LANGFUSE_ENABLED"] == "true"
    assert env["LANGFUSE_ENVIRONMENT"] == "prod"
    assert env["LANGFUSE_BASE_URL"] == "https://ao-ki-langfuse.intern.nav.no"
    assert {item["secret"] for item in prod["envFrom"]} >= {
        "ao-ki-langfuse-client", "ao-ki-model-api", "ao-ki-transkribering-secrets"}
    assert {"application": "ao-ki-langfuse"} in prod["accessPolicy"]["outbound"]["rules"]
    assert "ao-ki-langfuse.intern.nav.no" in {
        item["host"] for item in prod["accessPolicy"]["outbound"]["external"]}
    assert not any(item["name"].startswith("LANGFUSE_") for item in dev["env"])


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
