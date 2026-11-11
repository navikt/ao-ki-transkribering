import json
import os
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from shared.services.model_selection import resolve_model, summary_models

from shared.core.settings import AI_PROXY_API_KEY, AI_PROXY_URL, LLM_BACKEND, LLM_MODELL, OLLAMA_URL
from worker.prompts.normalisering import normaliser_til_bokmal
from shared.services.model_auth import authenticated_headers
from shared.diagnostics import event
from shared.metrics import metrics, observe, observe_stream
from shared.services.llm_observability import generation, generation_stream, record_usage


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


_OPTIONS = {
    "temperature": 0.25,
    "num_ctx": _int_env("LLM_CONTEXT_TOKENS", _int_env(
        "LLM_NUM_CTX", _int_env("OLLAMA_NUM_CTX", 32768) if LLM_BACKEND == "ollama" else 8192
    )),
    "repeat_penalty": 1.3,
    # Deployments can reserve more output within their model's context budget.
    "num_predict": _int_env("LLM_MAX_TOKENS", 256),
}


class SummaryOverrides(BaseModel):
    """Request-scoped experiments; released prompt definitions remain immutable."""

    model_config = ConfigDict(extra="forbid")
    system_prompt: str | None = Field(default=None, min_length=1, max_length=12000)
    user_prompt: str | None = Field(default=None, min_length=1, max_length=18000)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False, strict=True)
    top_p: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False, strict=True)
    repeat_penalty: float | None = Field(default=None, ge=1, le=2, allow_inf_nan=False, strict=True)
    max_tokens: int | None = Field(default=None, ge=128, le=8192, strict=True)

    @model_validator(mode="after")
    def validate_prompts(self):
        if (self.system_prompt is None) != (self.user_prompt is None):
            raise ValueError("Both prompt fields are required for a custom prompt")
        if self.system_prompt is not None and not self.system_prompt.strip():
            raise ValueError("System prompt cannot be blank")
        if self.user_prompt is not None and self.user_prompt.count("{transkripsjon}") != 1:
            raise ValueError("User prompt must contain {transkripsjon} exactly once")
        if not self.model_dump(exclude_none=True):
            raise ValueError("At least one override is required")
        return self


class LlmForesporsel(BaseModel):
    transkripsjon: str
    modell: str | None = None
    scenario: str | None = None
    instruksjoner: str | None = Field(default=None, max_length=4000)
    begrenset_referat: StrictBool = False
    veileder_variant: Literal["kartlegging", "oppfolging"] | None = None
    overrides: SummaryOverrides | None = None

    @field_validator("modell")
    @classmethod
    def supported_model(cls, value):
        if value is not None:
            resolve_model(value, summary_models(LLM_BACKEND, LLM_MODELL))
        return value


class LlmModell(BaseModel):
    id: str


class LlmStatus(BaseModel):
    tilgjengelig: bool
    standard_modell: str
    modeller: list[LlmModell] = Field(default_factory=list)


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if AI_PROXY_API_KEY:
        headers["Authorization"] = f"Bearer {AI_PROXY_API_KEY}"
    return headers


def _chat_url() -> str:
    return AI_PROXY_URL.rstrip("/") + "/v1/chat/completions"


def _models_url() -> str:
    return AI_PROXY_URL.rstrip("/") + "/v1/models"


def _ollama_tags_url() -> str:
    return OLLAMA_URL.rstrip("/") + "/api/tags"


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def input_token_budget(max_tokens: int | None = None) -> int:
    """Reserve output and chat-template space inside the deployed context window."""
    requested = max_tokens if max_tokens is not None else _OPTIONS["num_predict"]
    output = max(requested, 600 if LLM_BACKEND == "ollama" else 512)
    return _OPTIONS["num_ctx"] - output - 512


def er_kontekstfeil(exc: httpx.HTTPStatusError) -> bool:
    return exc.response.status_code == 400 and any(
        marker in exc.response.text.lower()
        for marker in ("contextwindowexceeded", "context_length_exceeded", "maximum context length")
    )


def _build_payload(system: str, bruker: str, modell: str | None, stream: bool, max_tokens: int | None = None,
                   generation: dict | None = None) -> dict:
    return {
        "model": modell or LLM_MODELL,
        "stream": stream,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": bruker},
        ],
        "temperature": _OPTIONS["temperature"],
        "max_tokens": max_tokens if max_tokens is not None else _OPTIONS["num_predict"],
        **{key: value for key, value in (generation or {}).items() if key in {"temperature", "top_p"}},
        **({"extra_body": {"repeat_penalty": generation["repeat_penalty"]}}
           if generation and generation.get("repeat_penalty") is not None else {}),
    }


def _extract_text_from_response(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _extract_text_from_delta(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""
    delta = choices[0].get("delta", {})
    content = delta.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


class IncompleteModelResponse(RuntimeError):
    pass


async def count_tokens(system: str, bruker: str, modell: str | None = None) -> int | None:
    if LLM_BACKEND != "openai" or (modell or LLM_MODELL) not in {"borealis-12b", "borealis-4b"}:
        return None
    with metrics.track("token_count") as state:
        result = await _count_tokens(system, bruker, modell)
        if result is None:
            state["outcome"] = "unavailable"
        return result


async def _count_tokens(system: str, bruker: str, modell: str | None) -> int | None:
    payload = _build_payload(system, bruker, modell, stream=False, max_tokens=1)
    payload["extra_body"] = {"transkriberit_count_tokens": True}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5, read=20)) as client:
            response = await client.post(_chat_url(), json=payload,
                headers=await authenticated_headers(_chat_url(), _headers()))
            if response.status_code in {400, 403, 404, 429, 501, 502, 503, 504}:
                event("llm.token_count_unavailable", status=response.status_code)
                return None
            response.raise_for_status()
            data = json.loads(_extract_text_from_response(response.json()))
            if data.get("type") != "token_count" or type(data.get("input_tokens")) is not int or data["input_tokens"] < 1:
                raise ValueError("Invalid token counter response")
            return data["input_tokens"]
    except (httpx.RequestError, ValueError, TypeError, AttributeError):
        event("llm.token_count_unavailable")
        return None


@observe("llm_generate")
@generation
async def kall(system: str, bruker: str, modell: str | None = None, *, max_tokens: int | None = None,
               raw: bool = False, json_mode: bool = False, response_schema: dict | None = None,
               generation: dict | None = None) -> str:
    if LLM_BACKEND == "ollama":
        from worker.ollama.klient import kall as kall_ollama

        options = {"max_tokens": max_tokens} if max_tokens is not None else {}
        if raw or json_mode:
            options.update(raw=raw, json_mode=json_mode)
        if response_schema is not None:
            options["response_schema"] = response_schema
        if generation is not None:
            options["generation"] = generation
        return await kall_ollama(system, bruker, modell or LLM_MODELL, **options)

    payload = _build_payload(system, bruker, modell, stream=False, max_tokens=max_tokens, generation=generation)
    if json_mode:
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "transkriberit_" + response_schema["title"], "strict": True, "schema": response_schema,
        }} if response_schema is not None else {"type": "json_object"}
        payload["temperature"] = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        resp = await klient.post(_chat_url(), json=payload, headers=await authenticated_headers(_chat_url(), _headers()))
        resp.raise_for_status()
        data = resp.json()
        record_usage(data.get("usage"))
        if any(choice.get("finish_reason") == "length" for choice in data.get("choices", [])):
            raise IncompleteModelResponse("Modellen nådde svargrensen før svaret var ferdig.")
        text = _extract_text_from_response(data)
        return text if raw else normaliser_til_bokmal(text)


@observe("model_listing")
async def hent_status() -> LlmStatus:
    if LLM_BACKEND == "ollama":
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=15.0)) as klient:
            resp = await klient.get(_ollama_tags_url())
            resp.raise_for_status()
            data = resp.json()

        modeller = []
        for item in data.get("models", []):
            model_id = item.get("name") or item.get("model")
            if isinstance(model_id, str) and model_id:
                modeller.append(LlmModell(id=model_id))

        return LlmStatus(
            tilgjengelig=True,
            standard_modell=LLM_MODELL,
            modeller=modeller,
        )

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=45.0)) as klient:
        resp = await klient.get(_models_url(), headers=await authenticated_headers(_models_url(), _headers()))
        resp.raise_for_status()
        data = resp.json()

    modeller = []
    for item in data.get("data", []):
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id:
            modeller.append(LlmModell(id=model_id))

    return LlmStatus(
        tilgjengelig=True,
        standard_modell=LLM_MODELL,
        modeller=modeller,
    )


@observe_stream("llm_stream")
@generation_stream
async def stream_tokens(system: str, bruker: str, modell: str | None = None, *, max_tokens: int | None = None):
    """Async generator som gir (token, er_ferdig, full_normalisert_tekst)."""
    if LLM_BACKEND == "ollama":
        from worker.ollama.klient import stream_tokens as stream_ollama_tokens

        options = {"max_tokens": max_tokens} if max_tokens is not None else {}
        async for token, er_ferdig, full_normalisert_tekst in stream_ollama_tokens(
            system,
            bruker,
            modell or LLM_MODELL,
            **options,
        ):
            yield token, er_ferdig, full_normalisert_tekst
        return

    payload = _build_payload(system, bruker, modell, stream=True, max_tokens=max_tokens)
    svar_deler: list[str] = []

    # Tilstandsmaskin for å filtrere bort <think>-blokkar.
    tilstand = "undecided"  # "undecided" | "thinking" | "answering"
    buf = ""

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        async with klient.stream(
            "POST",
            _chat_url(),
            json=payload,
            headers=await authenticated_headers(_chat_url(), _headers()),
        ) as resp:
            # Streaming responses do not load error bodies automatically.
            if not resp.is_success:
                await resp.aread()
            resp.raise_for_status()
            async for linje in resp.aiter_lines():
                if not linje or not linje.startswith("data:"):
                    continue
                innhold = linje[5:].strip()
                if not innhold:
                    continue
                if innhold == "[DONE]":
                    if tilstand in ("undecided", "thinking") and buf:
                        flushed = normaliser_til_bokmal(buf)
                        if "<think>" in flushed:
                            flushed = ""
                        flushed = flushed.strip()
                        if flushed:
                            svar_deler.append(flushed)
                    full = normaliser_til_bokmal("".join(svar_deler).strip())
                    yield "", True, full
                    return
                try:
                    chunk = json.loads(innhold)
                except json.JSONDecodeError:
                    continue
                token = _extract_text_from_delta(chunk)
                record_usage(chunk.get("usage"))
                if not token:
                    continue
                if tilstand == "undecided":
                    buf += token
                    if "<think>" in buf:
                        tilstand = "thinking"
                        buf = buf[buf.index("<think>"):]
                    elif len(buf) > 20:
                        tilstand = "answering"
                        svar_deler.append(buf)
                        yield buf, False, ""
                        buf = ""
                elif tilstand == "thinking":
                    buf += token
                    if "</think>" in buf:
                        tilstand = "answering"
                        etter = buf[buf.index("</think>") + len("</think>"):].lstrip()
                        buf = ""
                        if etter:
                            svar_deler.append(etter)
                            yield etter, False, ""
                else:
                    svar_deler.append(token)
                    yield token, False, ""
