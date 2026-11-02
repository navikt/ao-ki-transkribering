"""Optional, content-free Langfuse observations on an isolated tracer provider."""

import os
import re
from collections.abc import Mapping
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from functools import wraps
from urllib.parse import urlsplit

from shared.diagnostics import event
from shared.metrics import outcome

_client = None
_provider = None
_current = ContextVar("llm_observation", default=None)
_NAMES = frozenset({"summary", "llm_call", "llm_stream"})
_MODELS = frozenset({"borealis-12b"})
_TEMPLATES = frozenset({"veiledermote", "generelt_mote", "samarbeidsmote", "egendefinert",
                        "minimalt_sammendrag", "utfyllende_sammendrag"})
_ATTRIBUTES = frozenset({
    "langfuse.observation.type", "langfuse.observation.model.name",
    "langfuse.observation.usage_details", "langfuse.observation.level",
    "langfuse.environment",
    "langfuse.release",
    "langfuse.observation.metadata.template", "langfuse.observation.metadata.variant",
    "langfuse.observation.metadata.outcome", "langfuse.observation.metadata.schema_version",
})


def _mask_spans(*, params):
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    return MaskOtelSpansResult(span_patches={
        identifier: OtelSpanPatch(
            delete_attributes=tuple(key for key in span.attributes if key not in _ATTRIBUTES),
        ) for identifier, span in params.spans.items()
    })


def start():
    global _client, _provider
    if _client is not None or os.getenv("LANGFUSE_ENABLED", "false").lower() != "true":
        return
    provider = None
    try:
        from langfuse import Langfuse
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        url = os.environ["LANGFUSE_BASE_URL"].rstrip("/")
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise ValueError("Invalid endpoint")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("HTTPS required outside localhost")
        public = os.environ["LANGFUSE_PUBLIC_KEY"]
        secret = os.environ["LANGFUSE_SECRET_KEY"]
        if not public or not secret:
            raise ValueError("Missing credentials")
        environment = os.getenv("LANGFUSE_ENVIRONMENT", "local")
        if environment not in {"local", "dev", "prod"}:
            raise ValueError("Invalid environment")
        release = os.getenv("LANGFUSE_RELEASE", "")
        if release and not re.fullmatch(r"[0-9a-f]{7,40}", release):
            raise ValueError("Release must be a Git SHA")
        provider = TracerProvider(resource=Resource({"service.name": "transkriberit-llm"}))
        _client = Langfuse(public_key=public, secret_key=secret, base_url=url, environment=environment,
                           tracer_provider=provider, timeout=2, flush_at=32, flush_interval=5,
                           mask_otel_spans=_mask_spans,
                           should_export_span=lambda span: span.name in _NAMES,
                           sample_rate=1, release=release or "unknown", debug=False)
        _provider = provider
        event("llm.observability_enabled")
    except Exception:
        if provider is not None:
            _safe(provider.shutdown)
        # Configuration and exporter errors must never expose credentials or stop inference.
        event("llm.observability_unavailable")


def shutdown():
    global _client, _provider
    client, _client = _client, None
    provider, _provider = _provider, None
    if client is not None:
        try:
            client.shutdown()
        except Exception:
            event("llm.observability_shutdown_failed")
    if provider is not None:
        _safe(provider.shutdown)


def _safe(action):
    try:
        return action()
    except Exception:
        return None


@contextmanager
def track(name, *, model=None, template=None, variant=None, activate=True):
    if name not in _NAMES:
        raise ValueError("Unknown LLM observation")
    parent = _current.get()
    span = None
    if _client is not None:
        metadata = {"schema_version": 1}
        if template is not None:
            metadata["template"] = template if template in _TEMPLATES else "other"
        if variant in {"kartlegging", "oppfolging"}:
            metadata["variant"] = variant
        trace_context = {"trace_id": parent.trace_id, "parent_span_id": parent.id} if parent else None
        span = _safe(lambda: _client.start_observation(
            name=name, as_type="span" if name == "summary" else "generation",
            trace_context=trace_context, metadata=metadata,
            **({"model": model if model in _MODELS else "other"} if name != "summary" else {}),
        ))
    token = _current.set(span) if activate else None
    state = {"outcome": "ok", "span": span}
    try:
        yield state
    except BaseException as exc:
        if not (isinstance(exc, GeneratorExit) and state.get("completed")):
            state["outcome"] = outcome(exc)
        raise
    finally:
        if token is not None:
            _current.reset(token)
        if span is not None:
            _safe(lambda: span.update(metadata={"outcome": state["outcome"]},
                                      level="DEFAULT" if state["outcome"] == "ok" else "ERROR"))
            _safe(span.end)


def record_usage(usage):
    """Only provider-reported integer counts, never response payloads or estimates."""
    span = _current.get()
    if span is None or not isinstance(usage, Mapping):
        return
    counts = {}
    for source, target in (("prompt_tokens", "input"), ("completion_tokens", "output")):
        value = usage.get(source)
        if type(value) is int and 0 <= value <= 100_000_000:
            counts[target] = value
    if counts:
        _safe(lambda: span.update(usage_details=counts))


def generation(function):
    @wraps(function)
    async def measured(system, bruker, modell=None, **kwargs):
        from shared.core.settings import LLM_MODELL
        with track("llm_call", model=modell or LLM_MODELL):
            return await function(system, bruker, modell, **kwargs)
    return measured


def generation_stream(function):
    @wraps(function)
    async def measured(system, bruker, modell=None, **kwargs):
        from shared.core.settings import LLM_MODELL
        # SSE consumers can run each anext() in a different task. Never keep a
        # ContextVar token attached across a yield to such a consumer.
        with track("llm_stream", model=modell or LLM_MODELL, activate=False) as state:
            state["outcome"] = "error"
            async with aclosing(function(system, bruker, modell, **kwargs)) as source:
                while True:
                    token = _current.set(state["span"])
                    try:
                        item = await anext(source)
                    except StopAsyncIteration:
                        return
                    finally:
                        _current.reset(token)
                    if item[1] is True:
                        state["outcome"] = "ok"
                        state["completed"] = True
                    yield item
    return measured
