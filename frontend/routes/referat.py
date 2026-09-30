import json
import logging

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from shared.core.settings import LLM_MODELL
from shared.services.llm_proxy_client import (
    LlmForesporsel,
    LlmStatus,
    hent_status as hent_llm_status,
    kall as kall_llm,
    stream_tokens as stream_llm_tokens,
)
from worker.prompts import (
    beregn_llm_estimat as beregn_llm_estimat_base,
    hent_handling,
    hent_referat_scenario,
    list_handlinger,
    list_referat_scenarier,
    normaliser_til_bokmal,
)
from worker.prompts.handlinger import LlmHandling

router = APIRouter()
log = logging.getLogger(__name__)


def beregn_llm_estimat(modell: str | None, transkripsjon: str) -> int:
    return beregn_llm_estimat_base(modell, transkripsjon, fallback=LLM_MODELL)


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _hent_eller_404(handling_id: str) -> LlmHandling:
    handling = hent_handling(handling_id)
    if handling is None:
        raise HTTPException(status_code=404, detail=f"Ukjent LLM-handling: {handling_id}")
    return handling


def _bygg_prompt(handling: LlmHandling, transkripsjon: str) -> str:
    tekst = normaliser_til_bokmal(transkripsjon) if handling.normaliser_input else transkripsjon
    return handling.bygg_bruker_prompt(tekst)


def _bygg_referat_prompt(foresporsel: LlmForesporsel) -> tuple[str, str, str]:
    scenario = hent_referat_scenario(foresporsel.scenario)
    if scenario is None:
        raise HTTPException(status_code=400, detail=f"Ukjent referat-scenario: {foresporsel.scenario}")
    tekst = normaliser_til_bokmal(foresporsel.transkripsjon)
    return scenario.system_prompt, scenario.bygg_bruker_prompt(tekst), scenario.id


def _er_modell_utilgjengelig(exc: httpx.HTTPStatusError) -> bool:
    status = exc.response.status_code
    if status in {404, 429, 503}:
        return True

    tekst = exc.response.text.lower()
    return "no deployments available" in tekst or "does not exist" in tekst


def _http_error_for_llm(exc: httpx.HTTPStatusError) -> HTTPException:
    if _er_modell_utilgjengelig(exc):
        return HTTPException(status_code=503, detail="AI-modellen er ikke tilgjengelig nå")
    return HTTPException(status_code=502, detail=f"AI-proxy svarte med feil: {exc.response.status_code}")


def _sse_melding_for_llm(exc: httpx.HTTPStatusError) -> str:
    if _er_modell_utilgjengelig(exc):
        return "AI-modellen er ikke tilgjengelig nå"
    return f"AI-proxy svarte med feil: {exc.response.status_code}"


async def _kjor_handling(handling_id: str, foresporsel: LlmForesporsel):
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")
    handling = _hent_eller_404(handling_id)
    scenario_id = None
    try:
        if handling.id == "referat":
            system_prompt, bruker_prompt, scenario_id = _bygg_referat_prompt(foresporsel)
        else:
            system_prompt = handling.system_prompt
            bruker_prompt = _bygg_prompt(handling, foresporsel.transkripsjon)
        tekst = await kall_llm(system_prompt, bruker_prompt, foresporsel.modell)
        if handling.normaliser_output:
            tekst = normaliser_til_bokmal(tekst)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Kan ikke nå AI-proxyen")
    except httpx.HTTPStatusError as e:
        raise _http_error_for_llm(e)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Feil ved generering av LLM-handling %s", handling.id)
        raise HTTPException(status_code=500, detail=f"Feil ved generering av {handling.tittel}") from exc
    svar = {"tekst": tekst, "modell": foresporsel.modell or LLM_MODELL, "handling": handling.id}
    if scenario_id:
        svar["scenario"] = scenario_id
    return svar


def _stream_handling(handling_id: str, foresporsel: LlmForesporsel):
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    handling = _hent_eller_404(handling_id)
    scenario_id = None
    estimat = beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or LLM_MODELL
    if handling.id == "referat":
        system_prompt, bruker_prompt, scenario_id = _bygg_referat_prompt(foresporsel)
    else:
        system_prompt = handling.system_prompt
        bruker_prompt = _bygg_prompt(handling, foresporsel.transkripsjon)

    async def generator():
        yield sse({
            "type": "start",
            "estimert_sek": estimat,
            "modell": valgt_modell,
            "handling": handling.id,
            "scenario": scenario_id,
        })
        try:
            async for token, ferdig, full_tekst in stream_llm_tokens(
                system_prompt, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    tekst = normaliser_til_bokmal(full_tekst) if handling.normaliser_output else full_tekst
                    yield sse({
                        "type": "ferdig",
                        "tekst": tekst,
                        "modell": valgt_modell,
                        "handling": handling.id,
                        "scenario": scenario_id,
                    })
                elif token:
                    yield sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield sse({"type": "feil", "melding": "Kan ikke nå AI-proxyen"})
        except httpx.HTTPStatusError as e:
            yield sse({"type": "feil", "melding": _sse_melding_for_llm(e)})
        except Exception:
            log.exception("Feil ved streaming av LLM-handling %s", handling.id)
            yield sse({"type": "feil", "melding": f"Feil ved generering av {handling.tittel}"})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/llm/handlinger")
async def hent_llm_handlinger():
    """Lister faste LLM-handlinger som kan kjøres fra frontend/API."""
    return {"handlinger": list_handlinger()}


@router.get("/llm/referat-scenarier")
async def hent_referat_scenarier():
    """Lister faste referat-scenarier som velger serverdefinerte prompts."""
    return {"scenarier": list_referat_scenarier()}


@router.get("/llm/status", response_model=LlmStatus)
async def llm_status():
    """Sjekker om modell-API-et kan nås og hvilke modeller gatewayen eksponerer."""
    try:
        return await hent_llm_status()
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail="Kan ikke nå AI-proxyen") from exc
    except httpx.HTTPStatusError as exc:
        raise _http_error_for_llm(exc)


@router.post("/llm/handlinger/{handling_id}")
async def kjor_llm_handling(handling_id: str, foresporsel: LlmForesporsel):
    """Kjører en fast, serverdefinert LLM-handling."""
    return await _kjor_handling(handling_id, foresporsel)


@router.post("/llm/handlinger/{handling_id}/stream")
async def stream_llm_handling(handling_id: str, foresporsel: LlmForesporsel):
    """Streaming SSE-versjon av en fast, serverdefinert LLM-handling."""
    return _stream_handling(handling_id, foresporsel)


@router.post("/sammendrag")
async def lag_sammendrag(foresporsel: LlmForesporsel):
    """Genererer et løpende sammendrag av transkripsjon hittil."""
    return await _kjor_handling("sammendrag", foresporsel)


@router.post("/referat")
async def lag_referat(foresporsel: LlmForesporsel):
    """Genererer et fullt møtereferat fra transkripsjon."""
    return await _kjor_handling("referat", foresporsel)


@router.post("/referat/stream")
async def lag_referat_stream(foresporsel: LlmForesporsel):
    """Streaming SSE-versjon av /referat."""
    return _stream_handling("referat", foresporsel)


@router.post("/sammendrag/stream")
async def lag_sammendrag_stream(foresporsel: LlmForesporsel):
    """Streaming SSE-versjon av /sammendrag."""
    return _stream_handling("sammendrag", foresporsel)


@router.post("/referat/rullerende/stream")
async def lag_rullerende_referat_stream(foresporsel: LlmForesporsel):
    """Rullerende utkast-referat for pågående møte (sanntid-modus)."""
    return _stream_handling("rullerende_referat", foresporsel)
