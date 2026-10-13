"""Validate inbound employee tokens through the NAIS token service."""

import os

import httpx
from starlette.datastructures import Headers
from starlette.responses import JSONResponse


class EntraAuthMiddleware:
    def __init__(self, app):
        self.app = app
        # A deployment cannot accidentally disable authentication with a local flag.
        self.required = bool(os.getenv("NAIS_CLUSTER_NAME")) or os.getenv("AUTH_REQUIRED", "").lower() == "true"
        self.endpoint = os.getenv("NAIS_TOKEN_INTROSPECTION_ENDPOINT", "")
        if self.required and not self.endpoint:
            raise RuntimeError("NAIS token introspection must be configured when authentication is required")

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or not self.required:
            await self.app(scope, receive, send)
            return
        if (scope["type"] == "http" and scope.get("method") in {"GET", "HEAD"}
                and scope["path"] in {"/isAlive", "/isReady"}):
            await self.app(scope, receive, send)
            return

        status = 401
        headers = Headers(scope=scope).getlist("authorization")
        parts = headers[0].split() if len(headers) == 1 else []
        claims = None
        if len(parts) == 2 and parts[0].lower() == "bearer":
            try:
                async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                    response = await client.post(self.endpoint, json={
                        "identity_provider": "entra_id", "token": parts[1],
                    })
                    response.raise_for_status()
                    claims = response.json()
                if not isinstance(claims, dict) or not isinstance(claims.get("active"), bool):
                    status = 503
            except (httpx.HTTPError, ValueError):
                # Never log the token or the introspection response.
                status = 503

        if status != 503 and isinstance(claims, dict) and claims.get("active") is True:
            scope.setdefault("state", {})["user"] = claims
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013 if status == 503 else 1008})
        else:
            message = "Innlogging kunne ikke kontrolleres" if status == 503 else "Innlogging kreves"
            await JSONResponse(
                {"detail": message}, status_code=status,
                headers={"Cache-Control": "no-store", **({"WWW-Authenticate": "Bearer"} if status == 401 else {})},
            )(scope, receive, send)
