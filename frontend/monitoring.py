"""HTTP telemetry and a separate, non-ingress metrics listener."""

import asyncio
import logging
import os
import time

from prometheus_client import start_http_server

from shared.diagnostics import event
from shared.metrics import metrics


class HttpMetricsMiddleware:
    def __init__(self, app, routes, telemetry=metrics):
        self.app = app
        self.routes = frozenset(routes)
        self.telemetry = telemetry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            method = "OTHER"
        started, status, finished = time.monotonic(), 500, False
        self.telemetry.http_active.inc()

        def finish():
            nonlocal finished
            if finished:
                return
            finished = True
            # Never use scope.path, query strings, headers or route parameter values.
            route = getattr(scope.get("route"), "path", None)
            route = route if route in self.routes else "unmatched"
            elapsed = time.monotonic() - started
            self.telemetry.http_active.dec()
            self.telemetry.requests.labels(method, route, str(status)).inc()
            self.telemetry.request_duration.labels(method, route).observe(elapsed)
            if route not in {"/isAlive", "/isReady"}:
                event("http.response_completed", level=logging.WARNING if status >= 500 else logging.INFO,
                      method=method, route=route, status=status,
                      elapsed_s=round(elapsed, 3))

        async def measured_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                code = message["status"]
                status = code if type(code) is int and 100 <= code <= 599 else 500
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                # BackgroundTasks execute after this point and have their own metrics.
                finish()

        try:
            await self.app(scope, receive, measured_send)
        except asyncio.CancelledError:
            status = 499
            raise
        finally:
            finish()


def start_metrics_server():
    if os.getenv("METRICS_ENABLED", "false").lower() != "true":
        return None
    port = int(os.getenv("METRICS_PORT", "9091"))
    if not 1 <= port <= 65535:
        raise ValueError("METRICS_PORT must be between 1 and 65535")
    return start_http_server(port, addr=os.getenv("METRICS_HOST", "127.0.0.1"), registry=metrics.registry)


async def stop_metrics_server(server):
    if server is not None:
        httpd, thread = server
        await asyncio.to_thread(httpd.shutdown)
        httpd.server_close()
        await asyncio.to_thread(thread.join)
