import asyncio
import json
import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from worker.ollama.klient import MODELL as OLLAMA_MODELL, URL as OLLAMA_URL

router = APIRouter()
log = logging.getLogger(__name__)

ollama_modell_status: dict = {"tilgjengelig": None, "laster_ned": False}


async def sjekk_ollama_modell():
    """Sjekk om konfigurert Ollama-modell er tilgjengelig."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as klient:
            resp = await klient.get(f"{OLLAMA_URL}/api/tags")
            modeller_data = resp.json().get("models", [])
            modeller = [m["name"] for m in modeller_data]
            treff = next(
                (
                    m for m in modeller_data
                    if OLLAMA_MODELL == m["name"] or OLLAMA_MODELL == m["name"].split(":")[0]
                ),
                None,
            )
            tilgjengelig = treff is not None
            ollama_modell_status["tilgjengelig"] = tilgjengelig
            if tilgjengelig:
                storrelse_gb = treff["size"] / 1_073_741_824
                log.warning("LLM-modell:  %s  (%.1f GB)  klar", OLLAMA_MODELL, storrelse_gb)
            else:
                log.warning(
                    "LLM-modell:  %s  IKKE installert  -  tilgjengelige: %s",
                    OLLAMA_MODELL,
                    ", ".join(modeller) or "(ingen)",
                )
    except Exception as e:
        log.warning("LLM-modell:  %s  -  Ollama ikke tilgjengelig: %s", OLLAMA_MODELL, e)
        ollama_modell_status["tilgjengelig"] = False


@router.get("/modell/status")
async def modell_status():
    """Returnerer status for konfigurert Ollama-modell."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as klient:
            resp = await klient.get(f"{OLLAMA_URL}/api/tags")
            modeller = [m["name"] for m in resp.json().get("models", [])]
            tilgjengelig = any(OLLAMA_MODELL == m or OLLAMA_MODELL == m.split(":")[0] for m in modeller)
            ollama_modell_status["tilgjengelig"] = tilgjengelig
    except Exception:
        pass
    return {
        "modell": OLLAMA_MODELL,
        "tilgjengelig": ollama_modell_status.get("tilgjengelig"),
        "laster_ned": ollama_modell_status.get("laster_ned", False),
    }


@router.post("/modell/last-ned")
async def last_ned_modell():
    """Stream nedlasting av Ollama-modell som SSE. Bruker subprocess for fremdrift."""
    if ollama_modell_status.get("laster_ned"):
        return StreamingResponse(
            iter([b'data: {"feil":"Nedlasting pagaar allerede"}\n\n']),
            media_type="text/event-stream",
        )

    async def _stream():
        ollama_modell_status["laster_ned"] = True
        try:
            prosess = await asyncio.create_subprocess_exec(
                "ollama", "pull", OLLAMA_MODELL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert prosess.stdout is not None
            async for linje in prosess.stdout:
                tekst = linje.decode("utf-8", errors="replace").strip()
                if tekst:
                    melding = json.dumps({"linje": tekst}, ensure_ascii=False)
                    yield f"data: {melding}\n\n".encode()
            await prosess.wait()
            suksess = prosess.returncode == 0
            ollama_modell_status["tilgjengelig"] = suksess
            status_melding = json.dumps({"ferdig": True, "suksess": suksess})
            yield f"data: {status_melding}\n\n".encode()
        except Exception as e:
            feil_melding = json.dumps({"feil": str(e)})
            yield f"data: {feil_melding}\n\n".encode()
        finally:
            ollama_modell_status["laster_ned"] = False

    return StreamingResponse(_stream(), media_type="text/event-stream")
