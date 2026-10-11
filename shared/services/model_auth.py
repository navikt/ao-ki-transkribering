"""Cloud Run identity tokens, kept separate from application API keys."""

import asyncio
from functools import lru_cache
from threading import Lock
from urllib.parse import urlsplit

from shared.core.settings import MODEL_API_AUDIENCE, MODEL_API_IAM_AUTH

_lock = Lock()


@lru_cache(maxsize=4)
def _credentials(audience: str):
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token_credentials

    return fetch_id_token_credentials(audience, request=Request())


def _identity_token(audience: str) -> str:
    from google.auth.transport.requests import Request

    with _lock:
        credentials = _credentials(audience)
        if not credentials.valid:
            credentials.refresh(Request())
        if not credentials.token:
            raise RuntimeError("Cloud Run identity token is unavailable")
        return credentials.token


async def authenticated_headers(url: str, headers: dict[str, str]) -> dict[str, str]:
    if not MODEL_API_IAM_AUTH:
        return headers
    target = urlsplit(url)
    audience = urlsplit(MODEL_API_AUDIENCE)
    if (
        target.scheme != "https"
        or audience.scheme != "https"
        or not audience.hostname
        or target.netloc != audience.netloc
        or audience.path not in {"", "/"}
        or audience.query
        or audience.fragment
        or audience.username
    ):
        raise ValueError("Cloud Run authentication requires HTTPS and a matching service audience")
    token = await asyncio.to_thread(_identity_token, MODEL_API_AUDIENCE)
    # Cloud Run consumes this header; Authorization remains available to LiteLLM.
    return {**headers, "X-Serverless-Authorization": f"Bearer {token}"}
