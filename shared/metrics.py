"""Operational measurements with bounded labels and no request/result content."""

import asyncio
import time
from contextlib import aclosing, contextmanager
from functools import wraps

import httpx
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

OPERATIONS = frozenset({
    "summary_job", "recording_chunk", "transcription_upload", "speech_request",
    "llm_generate", "llm_stream", "model_listing", "token_count",
})
OUTCOMES = frozenset({"ok", "error", "cancelled", "timeout", "auth", "busy", "unavailable"})
DURATION_BUCKETS = (.05, .1, .25, .5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200)


def outcome(exc):
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return "cancelled"
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in {401, 403}:
            return "auth"
        if code == 429:
            return "busy"
        if code in {502, 503, 504}:
            return "unavailable"
    if isinstance(exc, httpx.TransportError):
        return "unavailable"
    return "error"


class Metrics:
    def __init__(self):
        # A private registry avoids hostname/platform labels and duplicate app registration.
        self.registry = CollectorRegistry()
        self.requests = Counter("transkriberit_http_requests_total", "Completed HTTP responses",
                                ["method", "route", "status"], registry=self.registry)
        self.request_duration = Histogram("transkriberit_http_duration_seconds", "HTTP response duration",
                                         ["method", "route"], buckets=DURATION_BUCKETS, registry=self.registry)
        self.http_active = Gauge("transkriberit_http_inflight", "Responses in progress", registry=self.registry)
        self.operations = Counter("transkriberit_operations_total", "Operational outcomes",
                                  ["operation", "outcome"], registry=self.registry)
        self.duration = Histogram("transkriberit_operation_duration_seconds", "Operational processing duration",
                                  ["operation"], buckets=DURATION_BUCKETS, registry=self.registry)
        self.active = Gauge("transkriberit_operations_inflight", "Active operations",
                            ["operation"], registry=self.registry)
        self.retries = Counter("transkriberit_speech_retries_total", "Scheduled speech retries", registry=self.registry)
        self.queued = Gauge("transkriberit_summary_queued", "Summary jobs waiting for a slot", registry=self.registry)
        self.oldest_queued = Gauge("transkriberit_summary_oldest_queued_timestamp_seconds",
                                   "Submission time of oldest waiting summary; zero when empty", registry=self.registry)
        self.queue_wait = Histogram("transkriberit_summary_queue_wait_seconds", "Wait before processing or cancellation",
                                    buckets=DURATION_BUCKETS, registry=self.registry)
        self.rejected = Counter("transkriberit_summary_rejected_total", "Summary submissions rejected at capacity",
                                registry=self.registry)
        for operation in OPERATIONS:
            self.active.labels(operation)
            for result in OUTCOMES:
                self.operations.labels(operation, result)

    def record(self, operation, result, elapsed):
        if operation not in OPERATIONS or result not in OUTCOMES:
            raise ValueError("Unsupported operational metric label")
        self.operations.labels(operation, result).inc()
        self.duration.labels(operation).observe(max(0, elapsed))

    @contextmanager
    def track(self, operation):
        if operation not in OPERATIONS:
            raise ValueError("Unsupported operational metric label")
        started = time.monotonic()
        self.active.labels(operation).inc()
        state = {"outcome": None}
        try:
            yield state
        except BaseException as exc:
            result = state["outcome"] or outcome(exc)
            raise
        else:
            result = state["outcome"] or "ok"
        finally:
            self.active.labels(operation).dec()
            self.record(operation, result, time.monotonic() - started)


metrics = Metrics()


def observe(operation):
    def decorate(function):
        @wraps(function)
        async def measured(*args, **kwargs):
            with metrics.track(operation):
                return await function(*args, **kwargs)
        return measured
    return decorate


def observe_stream(operation):
    """Count a delivered final LLM result once, including when its consumer closes."""
    def decorate(function):
        @wraps(function)
        async def measured(*args, **kwargs):
            with metrics.track(operation) as state:
                async with aclosing(function(*args, **kwargs)) as source:
                    async for item in source:
                        if item[1] is True:
                            state["outcome"] = "ok"
                        yield item
                    state["outcome"] = state["outcome"] or "error"
        return measured
    return decorate
