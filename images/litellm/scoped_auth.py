"""Database-free inference credentials; application keys cannot administer LiteLLM."""

import os
import json
import math
import secrets
from uuid import UUID

from fastapi import HTTPException, Request

_ROUTES = {
    ("GET", "/v1/models"): None,
    ("POST", "/v1/chat/completions"): "borealis-12b",
    ("POST", "/v1/audio/transcriptions"): "nb-whisper-large",
}
_FIELDS = {
    "/v1/chat/completions": {"model", "messages", "stream", "temperature", "max_tokens", "extra_body", "response_format"},
    "/v1/audio/transcriptions": {"model", "file", "response_format", "language", "session_id", "chunk_start"},
}


def _safe_response_format(value):
    if value == {"type": "json_object"}:
        return True
    if not isinstance(value, dict) or set(value) != {"type", "json_schema"} or value["type"] != "json_schema":
        return False
    spec = value["json_schema"]
    if not isinstance(spec, dict) or set(spec) != {"name", "strict", "schema"} or spec["strict"] is not True:
        return False
    if not isinstance(spec["name"], str) or spec["name"] not in {"transkriberit_EvidenceBatch", "transkriberit_Audit", "transkriberit_Coverage", "transkriberit_CompactReview"}:
        return False
    schema = spec["schema"]
    if not isinstance(schema, dict) or schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        return False
    try:
        size = len(json.dumps(schema))
    except (ValueError, TypeError, RecursionError):
        return False
    if size > 16384:
        return False
    keywords = {"type", "properties", "required", "additionalProperties", "items", "enum", "minItems", "maxItems",
                "minLength", "maxLength", "minimum", "maximum", "anyOf", "$defs", "$ref", "title", "default", "description"}
    nodes = 0

    def valid(node, depth=0, names=False):
        nonlocal nodes
        nodes += 1
        if depth > 12 or nodes > 2048:
            return False
        if isinstance(node, dict):
            if not names and set(node) - keywords:
                return False
            if "$ref" in node and (not isinstance(node["$ref"], str) or not node["$ref"].startswith("#/$defs/")):
                return False
            return all(valid(item, depth + 1, key in {"properties", "$defs"}) for key, item in node.items())
        if isinstance(node, list):
            return len(node) <= 512 and all(valid(item, depth + 1) for item in node)
        if type(node) in {int, float}:
            return math.isfinite(node) and abs(node) <= 65536
        return node is None or type(node) is bool or isinstance(node, str) and len(node) <= 4096

    if not valid(schema):
        return False

    def references(node):
        if isinstance(node, dict):
            refs = {node["$ref"][len("#/$defs/"):]} if "$ref" in node else set()
            return refs.union(*(references(item) for item in node.values()))
        if isinstance(node, list):
            return set().union(*(references(item) for item in node))
        return set()

    definitions = schema.get("$defs", {})
    checked = set()

    def acyclic(name, visiting):
        if name not in definitions or name in visiting:
            return False
        if name in checked:
            return True
        if not all(acyclic(target, visiting | {name}) for target in references(definitions[name])):
            return False
        checked.add(name)
        return True

    return all(acyclic(name, set()) for name in references(schema))


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
                # LiteLLM/FastAPI may already have parsed the upload. Reuse its form cache.
                payload = await request.form()
            else:
                payload = await request.json()
        except (ValueError, AssertionError):
            raise HTTPException(status_code=400, detail="Invalid inference request") from None
        if not isinstance(payload, dict) and request.url.path != "/v1/audio/transcriptions":
            raise HTTPException(status_code=400, detail="Invalid inference request")
        if payload.get("model") != model or set(payload) - _FIELDS[request.url.path]:
            raise HTTPException(status_code=403, detail="Model or request parameter is not permitted")
        if request.url.path == "/v1/chat/completions":
            if "extra_body" in payload and (
                payload["extra_body"] != {"transkriberit_count_tokens": True}
                or type(payload["extra_body"].get("transkriberit_count_tokens")) is not bool
                or payload.get("stream") is not False or type(payload.get("max_tokens")) is not int or payload["max_tokens"] != 1
            ):
                raise HTTPException(403, "Only tokenizer requests may use extra_body")
            if "response_format" in payload and not _safe_response_format(payload["response_format"]):
                raise HTTPException(403, "Structured output schema is not permitted")
        if request.url.path == "/v1/audio/transcriptions":
            try:
                if "session_id" in payload:
                    UUID(payload["session_id"])
                offset = float(payload.get("chunk_start", 0))
                if not math.isfinite(offset) or not 0 <= offset <= 7200:
                    raise ValueError
                if offset and "session_id" not in payload:
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise HTTPException(status_code=400, detail="Invalid recording metadata") from None
    # Forbid query-based provider overrides as well as JSON/multipart overrides.
    if request.query_params:
        raise HTTPException(status_code=403, detail="Query parameters are not permitted")
    return environment


async def user_api_key_auth(request: Request, api_key: str):
    from litellm.proxy._types import UserAPIKeyAuth

    environment = await authorize(request, api_key)
    parallel_requests = int(os.getenv("MODEL_API_MAX_PARALLEL_REQUESTS", "32"))
    if not 1 <= parallel_requests <= 128:
        raise ValueError("MODEL_API_MAX_PARALLEL_REQUESTS must be between 1 and 128")
    return UserAPIKeyAuth(
        api_key=api_key,
        key_alias=f"nais-{environment}",
        models=["nb-whisper-large", "borealis-12b"],
        max_parallel_requests=parallel_requests,
        rpm_limit=120,
    )
