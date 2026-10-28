import json
import os

import httpx

from worker.prompts.normalisering import normaliser_til_bokmal

URL       = os.getenv("OLLAMA_URL",        "http://localhost:11434")
MODELL    = os.getenv("OLLAMA_MODELL", os.getenv("LLM_MODELL", "qwen3:8b"))
NUM_CTX   = int(os.getenv("LLM_CONTEXT_TOKENS", os.getenv("LLM_NUM_CTX", os.getenv("OLLAMA_NUM_CTX", "32768"))))

_OPTIONS = {"temperature": 0.25, "num_ctx": NUM_CTX, "repeat_penalty": 1.3, "num_predict": 600}


async def stream_tokens(system: str, bruker: str, modell: str | None = None, *, max_tokens: int | None = None):
    """Async generator som gir (token, er_ferdig, full_normalisert_tekst).

    Siste yield har er_ferdig=True og normalisert sluttekst.
    Brukar ein tilstandsmaskin for å filtrere bort <think>-blokkar:
      - UNDECIDED: buffer inntil vi veit om det kjem <think> eller ikkje
      - THINKING:  inne i <think>, ingenting vert yieldta
      - ANSWERING: forbi tenking, tokens vert yieldta direkte
    """
    valgt_modell = modell or MODELL
    svar_deler: list[str] = []  # berre svar-tokens (utan thinking)

    # Tilstandsmaskin
    tilstand = "undecided"  # "undecided" | "thinking" | "answering"
    buf = ""                # buffer brukt i undecided/thinking

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
                "options": _OPTIONS if max_tokens is None else {**_OPTIONS, "num_predict": max_tokens},
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
                    if tilstand == "undecided":
                        buf += token
                        if "<think>" in buf:
                            tilstand = "thinking"
                            buf = buf[buf.index("<think>"):]
                        elif len(buf) > 20:
                            # Ingen <think> etter 20 teikn — yield bufferet
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
                    else:  # answering
                        svar_deler.append(token)
                        yield token, False, ""
                if chunk.get("done"):
                    # Fallback: flush remaining buffer if we never left thinking
                    if tilstand in ("undecided", "thinking") and buf:
                        flushed = normaliser_til_bokmal(buf)
                        # If still inside unclosed <think>, drop it; otherwise flush
                        if "<think>" in flushed:
                            flushed = ""
                        flushed = flushed.strip()
                        if flushed:
                            svar_deler.append(flushed)
                    full = normaliser_til_bokmal("".join(svar_deler).strip())
                    yield "", True, full
                    return


async def kall(system: str, bruker: str, modell: str | None = None, *, max_tokens: int | None = None,
               raw: bool = False, json_mode: bool = False, response_schema: dict | None = None) -> str:
    """Kaller Ollama /api/generate med streaming og returnerer svarteksten.

    Bruker streaming for å unngå timeout ved lange svar.
    Brukar think=False + buffer-basert stripping av <think>-blokkar (støtte for reasoning-modellar).
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
                **({"format": response_schema or "json"} if json_mode else {}),
                "options": {**_OPTIONS, **({"num_predict": max_tokens} if max_tokens is not None else {}),
                            **({"temperature": 0} if json_mode else {})},
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
                    if chunk.get("done_reason") == "length":
                        from shared.services.llm_proxy_client import IncompleteModelResponse
                        raise IncompleteModelResponse("Modellen nådde svargrensen før svaret var ferdig.")
                    break
    rå = "".join(deler)
    return rå if raw else normaliser_til_bokmal(rå)
