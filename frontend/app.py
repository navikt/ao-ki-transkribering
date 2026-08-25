import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pathlib import Path
from fastapi.staticfiles import StaticFiles

from frontend.routes.helse import router as helse_router
from frontend.routes.modell import router as modell_router, sjekk_ollama_modell
from frontend.routes.referat import router as referat_router
from frontend.routes.sanntid import router as sanntid_router
from frontend.routes.transkripsjon import router as transkripsjon_router
from shared.core.runtime import start_arbeider, stopp_arbeider
from shared.core.settings import TRANSKRIPSJON_BACKEND

logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("faster_whisper").setLevel(logging.ERROR)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    prosess = start_arbeider()
    if TRANSKRIPSJON_BACKEND == "local":
        await sjekk_ollama_modell()
    else:
        log.info("TRANSKRIPSJON_BACKEND=%s — hopper over Ollama-sjekk ved oppstart", TRANSKRIPSJON_BACKEND)
    yield
    stopp_arbeider(prosess)


def create_app() -> FastAPI:
    app = FastAPI(title="NB-Whisper transkribering", lifespan=lifespan)

    _STATIC = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(_STATIC), html=True), name="static")

    @app.get("/", include_in_schema=False)
    def rot():
        return FileResponse(str(_STATIC / "index.html"))

    app.include_router(helse_router)
    app.include_router(modell_router)
    app.include_router(transkripsjon_router)
    app.include_router(referat_router)
    app.include_router(sanntid_router)

    return app


app = create_app()
