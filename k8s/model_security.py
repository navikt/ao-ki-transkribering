"""Restrict every vLLM route, including aliases outside /v1."""

import os
import secrets


class ModelAccessMiddleware:
    def __init__(self, app):
        self.app = app
        self.key = os.environ["VLLM_API_KEY"]
        if not self.key:
            raise ValueError("VLLM_API_KEY must not be empty")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        route = (scope["method"], scope["path"])
        if route == ("GET", "/health"):
            await self.app(scope, receive, send)
            return
        allowed = {
            ("GET", "/v1/models"),
            ("POST", "/v1/audio/transcriptions"),
            ("POST", "/v1/chat/completions"),
        }
        values = [v for k, v in scope["headers"] if k.lower() == b"authorization"]
        expected = f"Bearer {self.key}".encode()
        status = 403 if route not in allowed else 401
        if route in allowed and len(values) == 1 and secrets.compare_digest(values[0], expected):
            await self.app(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"Model access denied"}'})
