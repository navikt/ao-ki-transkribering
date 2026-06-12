"""
Transkriberings-webapp – FastAPI backend
Kjør: uvicorn server:app

Arkitektur:
  - Batch-modus:    Transformers pipeline i separat prosess (MPS/CUDA, spawn).
  - Sanntidsmodus:  faster-whisper (CTranslate2) lastet direkte, kjøres via
                    asyncio.to_thread – CTranslate2 slipper GIL under inferens
                    og blokkerer ikke event loop.
"""

import os
os.environ["HF_HUB_OFFLINE"] = "1"

import asyncio
import json
import logging
import multiprocessing
import re
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import numpy as np

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from transkribering.batch import arbeider as _arbeider, estimert_total_s as _estimert_total_s
from transkribering.sanntid import hent_fw_modell as _hent_fw_modell, transkriber_pcm as _transkriber_pcm, VadBuffer as _VadBuffer

logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("faster_whisper").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Konfigurasjon
# ---------------------------------------------------------------------------

MODELL_ID      = os.getenv("WHISPER_MODELL",         "NbAiLab/nb-whisper-medium")
CT2_MODELL_STI = os.getenv("WHISPER_SANNTID_MODELL", "modeller/nb-whisper-medium")
ARBEIDSMAPPE   = Path(tempfile.mkdtemp(prefix="transkribering_"))

# Ollama-konfigurasjon
OLLAMA_URL            = os.getenv("OLLAMA_URL",            "http://localhost:11434")
OLLAMA_MODELL         = os.getenv("OLLAMA_MODELL",         "qwen3.6:35b")
# Begrenser KV-cache-allokering. Modeller med stort standardvindauge (f.eks. qwen3.5: 128k)
# bruker ellers sekunder på allokering alene. 8192 er nok for alle §14a-referater.
OLLAMA_NUM_CTX        = int(os.getenv("OLLAMA_NUM_CTX",    "8192"))

# ---------------------------------------------------------------------------
# LLM-prompts og hjelpefunksjoner – importert fra prompts/
# ---------------------------------------------------------------------------

from prompts import (
    SYSTEM_REFERAT as _SYSTEM_REFERAT,
    BRUKER_REFERAT as _BRUKER_REFERAT,
    SYSTEM_SAMMENDRAG as _SYSTEM_SAMMENDRAG,
    BRUKER_SAMMENDRAG as _BRUKER_SAMMENDRAG,
    SYSTEM_RULLERENDE as _SYSTEM_RULLERENDE,
    BRUKER_RULLERENDE as _BRUKER_RULLERENDE,
    normaliser_til_bokmal as _normaliser_til_bokmal,
    beregn_llm_estimat as _beregn_llm_estimat_base,
)


def _beregn_llm_estimat(modell: str | None, transkripsjon: str) -> int:
    return _beregn_llm_estimat_base(modell, transkripsjon, fallback=OLLAMA_MODELL)


async def _stream_ollama_tokens(system: str, bruker: str, modell: str | None = None):
    """Async generator som gir (token, er_ferdig, full_normalisert_tekst).

    Brukes av streaming-endepunktene. Siste yield har er_ferdig=True og full tekst.
    """
    valgt_modell = modell or OLLAMA_MODELL
    deler: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        async with klient.stream(
            "POST",
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": valgt_modell,
                "system": system,
                "prompt": bruker,
                "stream": True,
                "think": False,
                "options": {"temperature": 0.25, "num_ctx": OLLAMA_NUM_CTX},
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
                    deler.append(token)
                    yield token, False, ""
                if chunk.get("done"):
                    full = _normaliser_til_bokmal("".join(deler).strip())
                    yield "", True, full
                    return


_mp_ctx = multiprocessing.get_context("spawn")
_jobbkø: multiprocessing.Queue = _mp_ctx.Queue()

# ---------------------------------------------------------------------------
# FastAPI-app med lifespan (starter/stopper worker-prosessen)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    prosess = _mp_ctx.Process(target=_arbeider, args=(_jobbkø, MODELL_ID), daemon=True)
    prosess.start()
    yield
    _jobbkø.put(None)  # Signal til worker om å avslutte
    prosess.join(timeout=5)


app = FastAPI(title="NB-Whisper transkribering", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static", html=True), name="static")


@app.get("/", include_in_schema=False)
def rot():
    return FileResponse("static/index.html")


@app.get("/isAlive", include_in_schema=False)
def is_alive():
    return {"status": "ok"}


@app.get("/isReady", include_in_schema=False)
def is_ready():
    """Klar når arbeiderprosessen er startet og modellen er lastet."""
    if not _arbeider_klar.is_set():
        from fastapi import Response
        return Response(content='{"status":"laster modell"}', status_code=503,
                        media_type="application/json")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Batch-endepunkter
# ---------------------------------------------------------------------------

@app.post("/transkriber")
async def start_transkribering(
    lydfil: UploadFile = File(...),
    n_talere: int = Form(0),
):
    """Mottar lydfil, sender til arbeiderprosess, returnerer jobb-ID.

    n_talere: 0 = auto-deteksjon, 2/3/4 = eksakt antall talere.
    """
    suffix = Path(lydfil.filename or "opptak.webm").suffix or ".webm"
    jobb_id = str(uuid.uuid4())

    lydfil_sti = ARBEIDSMAPPE / f"{jobb_id}{suffix}"
    resultat_sti = ARBEIDSMAPPE / f"{jobb_id}.json"

    with lydfil_sti.open("wb") as f:
        shutil.copyfileobj(lydfil.file, f)

    resultat_sti.write_text(json.dumps({"status": "venter"}))
    _jobbkø.put((jobb_id, str(lydfil_sti), str(resultat_sti), n_talere))

    return {"jobb_id": jobb_id}


@app.get("/status/{jobb_id}")
async def sjekk_status(jobb_id: str):
    """Returnerer status, fremdrift og elapsed tid for en transkriberingsjobb."""
    resultat_sti = ARBEIDSMAPPE / f"{jobb_id}.json"
    if not resultat_sti.exists():
        raise HTTPException(status_code=404, detail="Ukjent jobb-ID")
    data = json.loads(resultat_sti.read_text())

    svar: dict = {"jobb_id": jobb_id, "status": data["status"]}

    if data["status"] == "transkriberer":
        start_tid = data.get("start_tid")
        lyd_s     = data.get("lyd_varighet_s")
        modell_id = data.get("modell_id", MODELL_ID)
        enhet     = data.get("enhet", "cpu")
        fase      = data.get("fase", "transkriberer")

        if start_tid:
            elapsed = time.time() - start_tid
            svar["elapsed_s"] = round(elapsed, 1)
            svar["fase"] = fase

            if lyd_s:
                estimert = _estimert_total_s(modell_id, lyd_s, enhet)
                svar["estimert_total_s"] = round(estimert, 1)
                svar["lyd_varighet_s"]   = round(lyd_s, 1)
                # Diarisering er siste 15 % av estimert tid
                if fase == "diariserer":
                    fremdrift = 0.85 + 0.10 * min(elapsed / estimert, 1.0)
                else:
                    fremdrift = min(elapsed / estimert * 0.85, 0.84)
                svar["fremdrift"] = round(fremdrift, 3)

    return svar


@app.get("/resultat/{jobb_id}")
async def hent_resultat(jobb_id: str):
    """Returnerer ferdig transkripsjon."""
    resultat_sti = ARBEIDSMAPPE / f"{jobb_id}.json"
    if not resultat_sti.exists():
        raise HTTPException(status_code=404, detail="Ukjent jobb-ID")
    data = json.loads(resultat_sti.read_text())
    if data["status"] == "feil":
        raise HTTPException(status_code=500, detail=data.get("feilmelding", "Ukjent feil"))
    if data["status"] != "ferdig":
        raise HTTPException(status_code=409, detail=f"Jobb ikke ferdig (status: {data['status']})")
    return {"jobb_id": jobb_id, "tekst": data["tekst"], "segmenter": data["segmenter"]}


# ---------------------------------------------------------------------------
# Møtereferat og sammendrag – Ollama-integrasjon
# ---------------------------------------------------------------------------

class _OllamaForesporsel(BaseModel):
    transkripsjon: str
    modell: str | None = None


async def _kall_ollama(system: str, bruker: str, modell: str | None = None) -> str:
    """Kaller Ollama /api/generate med streaming og returnerer svarteksten.

    Bruker streaming for å unngå timeout ved lange resonnementer (qwen3-tenking).
    Tenking (`think`) deaktiveres eksplisitt for raskere og mer forutsigbar respons.
    """
    valgt_modell = modell or OLLAMA_MODELL
    deler: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as klient:
        async with klient.stream(
            "POST",
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": valgt_modell,
                "system": system,
                "prompt": bruker,
                "stream": True,
                "think": False,
                "options": {"temperature": 0.25, "num_ctx": OLLAMA_NUM_CTX},
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


@app.post("/sammendrag")
async def lag_sammendrag(foresporsel: _OllamaForesporsel):
    """Genererer et løpende sammendrag av transkripsjon hittil (Prompt B)."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")
    try:
        # Normaliser transkripsjonens nynorsk-ord FØR sending – reduserer speiling
        transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
        bruker_prompt = _BRUKER_SAMMENDRAG.format(transkripsjon=transkripsjon_normalisert)
        tekst = await _kall_ollama(_SYSTEM_SAMMENDRAG, bruker_prompt, foresporsel.modell)
        tekst = _normaliser_til_bokmal(tekst)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Kan ikke nå Ollama – er tjenesten startet?")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ollama svarte med feil: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feil ved generering av sammendrag: {e}")
    return {"tekst": tekst, "modell": foresporsel.modell or OLLAMA_MODELL}


@app.post("/referat")
async def lag_referat(foresporsel: _OllamaForesporsel):
    """Genererer et fullt møtereferat fra transkripsjon (Prompt A)."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")
    try:
        # Normaliser transkripsjonens nynorsk-ord FØR sending – reduserer speiling
        transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
        bruker_prompt = _BRUKER_REFERAT.format(transkripsjon=transkripsjon_normalisert)
        tekst = await _kall_ollama(_SYSTEM_REFERAT, bruker_prompt, foresporsel.modell)
        tekst = _normaliser_til_bokmal(tekst)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Kan ikke nå Ollama – er tjenesten startet?")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ollama svarte med feil: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feil ved generering av referat: {e}")
    return {"tekst": tekst, "modell": foresporsel.modell or OLLAMA_MODELL}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/referat/stream")
async def lag_referat_stream(foresporsel: _OllamaForesporsel):
    """Streaming SSE-versjon av /referat.

    Sender:
      {"type":"start",  "estimert_sek": N, "modell": "..."}
      {"type":"token",  "tekst": "..."}          (én per token)
      {"type":"ferdig", "tekst": "..."}          (normalisert sluttekst)
    eller:
      {"type":"feil",   "melding": "..."}
    """
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    estimat = _beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or OLLAMA_MODELL
    transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
    bruker_prompt = _BRUKER_REFERAT.format(transkripsjon=transkripsjon_normalisert)

    async def generator():
        yield _sse({"type": "start", "estimert_sek": estimat, "modell": valgt_modell})
        try:
            async for token, ferdig, full_tekst in _stream_ollama_tokens(
                _SYSTEM_REFERAT, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    yield _sse({"type": "ferdig", "tekst": full_tekst, "modell": valgt_modell})
                elif token:
                    yield _sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield _sse({"type": "feil", "melding": "Kan ikke nå Ollama – er tjenesten startet?"})
        except Exception as e:
            yield _sse({"type": "feil", "melding": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/sammendrag/stream")
async def lag_sammendrag_stream(foresporsel: _OllamaForesporsel):
    """Streaming SSE-versjon av /sammendrag."""
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    estimat = _beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or OLLAMA_MODELL
    transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
    bruker_prompt = _BRUKER_SAMMENDRAG.format(transkripsjon=transkripsjon_normalisert)

    async def generator():
        yield _sse({"type": "start", "estimert_sek": estimat, "modell": valgt_modell})
        try:
            async for token, ferdig, full_tekst in _stream_ollama_tokens(
                _SYSTEM_SAMMENDRAG, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    yield _sse({"type": "ferdig", "tekst": full_tekst, "modell": valgt_modell})
                elif token:
                    yield _sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield _sse({"type": "feil", "melding": "Kan ikke nå Ollama – er tjenesten startet?"})
        except Exception as e:
            yield _sse({"type": "feil", "melding": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/referat/rullerende/stream")
async def lag_rullerende_referat_stream(foresporsel: _OllamaForesporsel):
    """Rullerende utkast-referat for pågående møte (sanntid-modus).

    Kalt automatisk fra frontend hvert ~150. nye ord. Bruker en kortere prompt
    enn fullversjonen slik at svaret er raskere.

    Sender:
      {"type":"start",  "estimert_sek": N, "modell": "..."}
      {"type":"token",  "tekst": "..."}
      {"type":"ferdig", "tekst": "..."}
    eller:
      {"type":"feil",   "melding": "..."}
    """
    if not foresporsel.transkripsjon.strip():
        raise HTTPException(status_code=400, detail="Transkripsjon mangler")

    estimat = _beregn_llm_estimat(foresporsel.modell, foresporsel.transkripsjon)
    valgt_modell = foresporsel.modell or OLLAMA_MODELL
    transkripsjon_normalisert = _normaliser_til_bokmal(foresporsel.transkripsjon)
    bruker_prompt = _BRUKER_RULLERENDE.format(transkripsjon=transkripsjon_normalisert)

    async def generator():
        yield _sse({"type": "start", "estimert_sek": estimat, "modell": valgt_modell})
        try:
            async for token, ferdig, full_tekst in _stream_ollama_tokens(
                _SYSTEM_RULLERENDE, bruker_prompt, foresporsel.modell
            ):
                if ferdig:
                    yield _sse({"type": "ferdig", "tekst": _normaliser_til_bokmal(full_tekst), "modell": valgt_modell})
                elif token:
                    yield _sse({"type": "token", "tekst": token})
        except httpx.ConnectError:
            yield _sse({"type": "feil", "melding": "Kan ikke nå Ollama – er tjenesten startet?"})
        except Exception as e:
            yield _sse({"type": "feil", "melding": str(e)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


#
# Protokoll (ny):
#   Browser → Server: binære meldinger = raw float32 LE PCM, 16 kHz, mono
#   Browser → Server: JSON {"type": "stopp"} for å avslutte
#   Server → Client:  JSON {"type": "segment", "tekst": "...", "segmenter": [...]}
#
# Server-side VAD-logikk:
#   - Buffer innkommende PCM-frames
#   - Regn ut RMS-energi per 160-sample frame (10 ms ved 16 kHz)
#   - Detekter tale / stillhet med terskel
#   - Flush buffer til Whisper ved:
#       a) Stillhetsvarighet ≥ STILLHET_TERSKEL_S etter tale (naturlig pause)
#       b) Total bufferlengde ≥ MAKS_BUFFER_S (sikkerhetsnett)
#   - Sendt buffer inneholder kun talesegmenter (stille frames fjernes ikke,
#     men flush skjer ved naturlige pauser)
# ---------------------------------------------------------------------------

@app.websocket("/ws/sanntid")
async def sanntid_ws(websocket: WebSocket):
    """
    WebSocket-endpoint for sanntidstranskribering med server-side VAD og diarisering.

    Protokoll:
      Client → Server: binær melding = raw float32 LE PCM, 16 kHz, mono
      Client → Server: JSON {"type": "stopp"}  (avslutt og flush)
      Server → Client: JSON {"type": "segment", "tekst": "...", "segmenter": [{..., "taler": "SPEAKER_XX"}]}
    """
    await websocket.accept()

    # Sjekk at sanntidsmodellen finnes – send feilmelding og lukk om ikke
    try:
        _hent_fw_modell()
    except FileNotFoundError as e:
        await websocket.send_json({"type": "feil", "melding": str(e)})
        await websocket.close()
        return

    buf = _VadBuffer()
    transkriber_kø: asyncio.Queue = asyncio.Queue(maxsize=4)
    # Prototype-state deles mellom worker-kall for å holde konsistent taler-ID
    prototyper_state: list[np.ndarray | None] = [None]

    async def transkriber_worker():
        """Konsumerer PCM-bufre fra kø, kjører Whisper + diarisering sekvensielt."""
        while True:
            pcm = await transkriber_kø.get()
            if pcm is None:
                break
            resultat, ny_proto = await asyncio.to_thread(
                _transkriber_pcm, pcm, prototyper_state[0]
            )
            prototyper_state[0] = ny_proto
            if resultat and resultat.get("tekst"):
                try:
                    await websocket.send_json({
                        "type": "segment",
                        "tekst": resultat["tekst"],
                        "segmenter": resultat["segmenter"],
                    })
                except Exception:
                    pass
            transkriber_kø.task_done()

    worker_task = asyncio.create_task(transkriber_worker())

    async def send_til_whisper(pcm: np.ndarray):
        try:
            await transkriber_kø.put(pcm)
        except asyncio.QueueFull:
            print("[sanntid] Kø full – dropper segment", flush=True)

    try:
        while True:
            melding = await websocket.receive()

            if "text" in melding:
                data = json.loads(melding["text"])
                if data.get("type") == "stopp":
                    rest = buf.flush_alt()
                    if rest is not None:
                        await send_til_whisper(rest)
                    break

            elif "bytes" in melding:
                raw = melding["bytes"]
                if not raw:
                    continue

                n_samples = len(raw) // 4
                if n_samples == 0:
                    continue
                samples = np.frombuffer(raw, dtype="<f4").copy()

                pcm_klar = buf.legg_til(samples)
                if pcm_klar is not None:
                    await send_til_whisper(pcm_klar)

    except WebSocketDisconnect:
        pass
    finally:
        await transkriber_kø.put(None)
        await worker_task

