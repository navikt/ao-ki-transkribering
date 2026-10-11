"""Database-free inference credentials; application keys cannot administer LiteLLM."""

import os
import secrets

from fastapi import HTTPException, Request

_ROUTES = {
    ("GET", "/v1/models"): None,
    ("POST", "/v1/chat/completions"): "borealis-12b",
    ("POST", "/v1/audio/transcriptions"): "nb-whisper-large",
}
_FIELDS = {
    "/v1/chat/completions": {"model", "messages", "stream", "temperature", "max_tokens"},
    "/v1/audio/transcriptions": {"model", "file", "response_format", "language"},
}


async def authorize(request: Request, api_key: str) -> str:
    environment = None
    for name in ("dev", "prod"):
        expected = os.getenv(f"NAIS_{name.upper()}_API_KEY", "")
        if expected and api_key and secrets.compare_digest(api_key.encode(), expected.encode()):
            environment = name
    if environment is None:
        raise HTTPException(status_code=401, detail="Invalid application credential")
    route = (request.method, request.url.path)
    if route not in _ROUTES:
        raise HTTPException(status_code=403, detail="Application credential cannot access this route")
    model = _ROUTES[route]
    if model:
        try:
            if request.url.path == "/v1/audio/transcriptions":
                # Cache the raw body too: downstream LiteLLM may read it again.
                await request.body()
                payload = await request.form()
            else:
                payload = await request.json()
        except (ValueError, AssertionError):
            raise HTTPException(status_code=400, detail="Invalid inference request") from None
        if not isinstance(payload, dict) and request.url.path != "/v1/audio/transcriptions":
            raise HTTPException(status_code=400, detail="Invalid inference request")
        if payload.get("model") != model or set(payload) - _FIELDS[request.url.path]:
            raise HTTPException(status_code=403, detail="Model or request parameter is not permitted")
    # Forbid query-based provider overrides as well as JSON/multipart overrides.
    if request.query_params:
        raise HTTPException(status_code=403, detail="Query parameters are not permitted")
    return environment


async def user_api_key_auth(request: Request, api_key: str):
    from litellm.proxy._types import UserAPIKeyAuth

    environment = await authorize(request, api_key)
    return UserAPIKeyAuth(
        api_key=api_key,
        key_alias=f"nais-{environment}",
        models=["nb-whisper-large", "borealis-12b"],
        max_parallel_requests=5,
        rpm_limit=120,
    )
