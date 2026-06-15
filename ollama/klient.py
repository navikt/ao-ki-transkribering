import json
import os

import httpx
from pydantic import BaseModel

from prompts.normalisering import normaliser_til_bokmal

URL       = os.getenv("OLLAMA_URL",        "http://localhost:11434")
MODELL    = os.getenv("OLLAMA_MODELL",     "qwen3:8b")
NUM_CTX   = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

_OPTIONS = {"temperature": 0.25, "num_ctx": NUM_CTX}


class OllamaForesporsel(BaseModel):
    transkripsjon: str
    modell: str | None = None


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_tokens(system: str, bruker: str, modell: str | None = None):
    """Async generator som gir (token, er_ferdig, full_normalisert_tekst).

    Siste yield har er_ferdig=True og normalisert sluttekst.
    Tenke-blokkar (<think>...</think>) frå qwen3-modellar vert filtrert bort.
    """
    valgt_modell = modell or MODELL
    deler: list[str] = []
    tenker = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        async with klient.stream(
            "POST",
            f"{URL}/api/generate",
            json={
                "model": valgt_modell,
                "system": system,
                "prompt": bruker,
                "stream": True,
                "think": False,
                "options": _OPTIONS,
            },
        ) as resp:
            resp.raise_for_status()
            async for linje in resp.aiter_lines():
                if not linje:
                    continue
                try:
                    chunk = json.loads(linje)
                except json.JSONDecodeError:
                    continue
                token = chunk.get("response", "")
                if token:
                    if "<think>" in token:
                        tenker = True
                    if not tenker:
                        deler.append(token)
                        yield token, False, ""
                    if "</think>" in token:
                        tenker = False
                if chunk.get("done"):
                    full = normaliser_til_bokmal("".join(deler).strip())
                    yield "", True, full
                    return


async def kall(system: str, bruker: str, modell: str | None = None) -> str:
    """Kaller Ollama /api/generate med streaming og returnerer svarteksten.

    Bruker streaming for å unngå timeout ved lange resonnementer (qwen3-tenking).
    Tenking (`think`) deaktiveres eksplisitt for raskere og mer forutsigbar respons.
    """
    valgt_modell = modell or MODELL
    deler: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        async with klient.stream(
            "POST",
            f"{URL}/api/generate",
            json={
                "model": valgt_modell,
                "system": system,
                "prompt": bruker,
                "stream": True,
                "think": False,
                "options": _OPTIONS,
            },
        ) as resp:
            resp.raise_for_status()
            async for linje in resp.aiter_lines():
                if not linje:
                    continue
                try:
                    chunk = json.loads(linje)
                except json.JSONDecodeError:
                    continue
                deler.append(chunk.get("response", ""))
                if chunk.get("done"):
                    break
    return "".join(deler).strip()
