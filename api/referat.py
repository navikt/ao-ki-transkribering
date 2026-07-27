import json

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ollama.klient import (
    MODELL as OLLAMA_MODELL,
    OllamaForesporsel,
    kall as kall_ollama,
    stream_tokens as stream_ollama_tokens,
)
from prompts import (
    BRUKER_REFERAT,
    BRUKER_RULLERENDE,
    BRUKER_SAMMENDRAG,
    SYSTEM_REFERAT,
    SYSTEM_RULLERENDE,
    SYSTEM_SAMMENDRAG,
    beregn_llm_estimat as beregn_llm_estimat_base,
    normaliser_til_bokmal,
)

router = APIRouter()


def beregn_llm_estimat(modell: str | None, transkripsjon: str) -> int:
    return beregn_llm_estimat_base(modell, transkripsjon, fallback=OLLAMA_MODELL)


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/sammendrag")
async def lag_sammendrag(foresporsel: OllamaForesporsel):
    """Genererer et løpende sammendrag av transkripsjon hittil (Prompt B)."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")
    try:
        transkripsjon_normalisert = normaliser_til_bokmal(foresporsel.transkripsjon)
        bruker_prompt = BRUKER_SAMMENDRAG.format(transkripsjon=transkripsjon_normalisert)
        tekst = await kall_ollama(SYSTEM_SAMMENDRAG, bruker_prompt, foresporsel.modell)
        tekst = normaliser_til_bokmal(tekst)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Kan ikke nå Ollama - er tjenesten startet?")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ollama svarte med feil: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feil ved generering av sammendrag: {e}")
    return {"tekst": tekst, "modell": foresporsel.modell or OLLAMA_MODELL}


@router.post("/referat")
async def lag_referat(foresporsel: OllamaForesporsel):
    """Genererer et fullt møtereferat fra transkripsjon (Prompt A)."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")
    try:
        transkripsjon_normalisert = normaliser_til_bokmal(foresporsel.transkripsjon)
        bruker_prompt = BRUKER_REFERAT.format(transkripsjon=transkripsjon_normalisert)
        tekst = await kall_ollama(SYSTEM_REFERAT, bruker_prompt, foresporsel.modell)
        tekst = normaliser_til_bokmal(tekst)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Kan ikke nå Ollama - er tjenesten startet?")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ollama svarte med feil: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feil ved generering av referat: {e}")
    return {"tekst": tekst, "modell": foresporsel.modell or OLLAMA_MODELL}


@router.post("/referat/stream")
async def lag_referat_stream(foresporsel: OllamaForesporsel):
    """Streaming SSE-versjon av /referat."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    estimat = beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or OLLAMA_MODELL
    transkripsjon_normalisert = normaliser_til_bokmal(foresporsel.transkripsjon)
    bruker_prompt = BRUKER_REFERAT.format(transkripsjon=transkripsjon_normalisert)

    async def generator():
        yield sse({"type": "start", "estimert_sek": estimat, "modell": valgt_modell})
        try:
            async for token, ferdig, full_tekst in stream_ollama_tokens(
                SYSTEM_REFERAT, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    yield sse({"type": "ferdig", "tekst": full_tekst, "modell": valgt_modell})
                elif token:
                    yield sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield sse({"type": "feil", "melding": "Kan ikke nå Ollama - er tjenesten startet?"})
        except Exception as e:
            yield sse({"type": "feil", "melding": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sammendrag/stream")
async def lag_sammendrag_stream(foresporsel: OllamaForesporsel):
    """Streaming SSE-versjon av /sammendrag."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    estimat = beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or OLLAMA_MODELL
    transkripsjon_normalisert = normaliser_til_bokmal(foresporsel.transkripsjon)
    bruker_prompt = BRUKER_SAMMENDRAG.format(transkripsjon=transkripsjon_normalisert)

    async def generator():
        yield sse({"type": "start", "estimert_sek": estimat, "modell": valgt_modell})
        try:
            async for token, ferdig, full_tekst in stream_ollama_tokens(
                SYSTEM_SAMMENDRAG, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    yield sse({"type": "ferdig", "tekst": full_tekst, "modell": valgt_modell})
                elif token:
                    yield sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield sse({"type": "feil", "melding": "Kan ikke nå Ollama - er tjenesten startet?"})
        except Exception as e:
            yield sse({"type": "feil", "melding": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/referat/rullerende/stream")
async def lag_rullerende_referat_stream(foresporsel: OllamaForesporsel):
    """Rullerende utkast-referat for pågående møte (sanntid-modus)."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    estimat = beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or OLLAMA_MODELL
    transkripsjon_normalisert = normaliser_til_bokmal(foresporsel.transkripsjon)
    bruker_prompt = BRUKER_RULLERENDE.format(transkripsjon=transkripsjon_normalisert)

    async def generator():
        yield sse({"type": "start", "estimert_sek": estimat, "modell": valgt_modell})
        try:
            async for token, ferdig, full_tekst in stream_ollama_tokens(
                SYSTEM_RULLERENDE, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    yield sse({
                        "type": "ferdig",
                        "tekst": normaliser_til_bokmal(full_tekst),
                        "modell": valgt_modell,
                    })
                elif token:
                    yield sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield sse({"type": "feil", "melding": "Kan ikke nå Ollama - er tjenesten startet?"})
        except Exception as e:
            yield sse({"type": "feil", "melding": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
