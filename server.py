"""
Transkriberings-webapp - FastAPI backend.

Kjor: uvicorn server:app
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.helse import router as helse_router
from api.modell import router as modell_router, sjekk_ollama_modell
from api.referat import router as referat_router
from api.sanntid import router as sanntid_router
from api.transkripsjon import router as transkripsjon_router
from runtime import start_arbeider, stopp_arbeider

logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("faster_whisper").setLevel(logging.ERROR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    prosess = start_arbeider()
    await sjekk_ollama_modell()
    yield
    stopp_arbeider(prosess)


app = FastAPI(title="NB-Whisper transkribering", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static", html=True), name="static")


@app.get("/", include_in_schema=False)
def rot():
    return FileResponse("static/index.html")


app.include_router(helse_router)
app.include_router(modell_router)
app.include_router(transkripsjon_router)
app.include_router(referat_router)
app.include_router(sanntid_router)
