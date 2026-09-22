import json

import httpx
from pydantic import BaseModel, Field

from shared.core.settings import AI_PROXY_API_KEY, AI_PROXY_URL, LLM_MODELL
from worker.prompts.normalisering import normaliser_til_bokmal

_OPTIONS = {"temperature": 0.25, "num_ctx": 32768, "repeat_penalty": 1.3, "num_predict": 600}


class LlmForesporsel(BaseModel):
    transkripsjon: str
    modell: str | None = None


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


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_payload(system: str, bruker: str, modell: str | None, stream: bool) -> dict:
    return {
        "model": modell or LLM_MODELL,
        "stream": stream,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": bruker},
        ],
        "temperature": _OPTIONS["temperature"],
        "max_tokens": _OPTIONS["num_predict"],
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


async def kall(system: str, bruker: str, modell: str | None = None) -> str:
    payload = _build_payload(system, bruker, modell, stream=False)
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        resp = await klient.post(_chat_url(), json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
        return normaliser_til_bokmal(_extract_text_from_response(data))


async def hent_status() -> LlmStatus:
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=15.0)) as klient:
        resp = await klient.get(_models_url(), headers=_headers())
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


async def stream_tokens(system: str, bruker: str, modell: str | None = None):
    """Async generator som gir (token, er_ferdig, full_normalisert_tekst)."""
    payload = _build_payload(system, bruker, modell, stream=True)
    svar_deler: list[str] = []

    # Tilstandsmaskin for å filtrere bort <think>-blokkar.
    tilstand = "undecided"  # "undecided" | "thinking" | "answering"
    buf = ""

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        async with klient.stream(
            "POST",
            _chat_url(),
            json=payload,
            headers=_headers(),
        ) as resp:
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
