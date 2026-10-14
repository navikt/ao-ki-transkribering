"""Authenticated TLS boundary for the loopback-only llama.cpp server."""

import json
from contextlib import asynccontextmanager

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from k8s.model_security import ModelAccessMiddleware

MAX_BODY_BYTES = 8 * 1024 * 1024
MODEL = "borealis-12b"


class RequestLimitMiddleware:
    """Count complete response lifetimes, including queued and streamed responses."""

    def __init__(self, app, capacity=32):
        self.app, self.capacity, self.pending = app, capacity, 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope["method"], scope["path"]) != ("POST", "/v1/chat/completions"):
            return await self.app(scope, receive, send)
        if self.pending >= self.capacity:
            response = JSONResponse({"detail": "Model queue is full"}, status_code=429,
                                    headers={"Retry-After": "10"})
            return await response(scope, receive, send)
        self.pending += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.pending -= 1


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001", trust_env=False,
                                timeout=httpx.Timeout(300, connect=5),
                                limits=httpx.Limits(max_connections=40)) as client:
        app.state.upstream = client
        yield


app = FastAPI(title="Borealis model API", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RequestLimitMiddleware)
app.add_middleware(ModelAccessMiddleware)


@app.get("/health")
async def health():
    try:
        response = await app.state.upstream.get("/health", timeout=5)
        ready = response.status_code == 200
    except httpx.RequestError:
        ready = False
    return JSONResponse({"status": "ok" if ready else "loading", "model": MODEL,
                         "runtime": "llama.cpp", "quantization": "Q8_0"},
                        status_code=200 if ready else 503)


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "NbAiLab"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "Request is too large")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid JSON") from None
    if (not isinstance(payload, dict) or not isinstance(payload.get("model"), str)
            or payload["model"] not in {MODEL, "/models/borealis-12b"}):
        raise HTTPException(400, "Unknown model")
    payload["model"] = MODEL
    try:
        upstream = await app.state.upstream.send(
            app.state.upstream.build_request("POST", "/v1/chat/completions", json=payload), stream=True)
    except httpx.TimeoutException:
        raise HTTPException(504, "Model response timed out") from None
    except httpx.RequestError:
        raise HTTPException(503, "Model unavailable") from None

    async def chunks():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            with anyio.CancelScope(shield=True):
                await upstream.aclose()

    return StreamingResponse(chunks(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type", "application/json"),
                             background=BackgroundTask(upstream.aclose))
