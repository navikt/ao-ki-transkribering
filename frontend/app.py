import logging
import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from frontend.auth import EntraAuthMiddleware

from frontend.routes.helse import router as helse_router
from frontend.routes.referat import router as referat_router
from frontend.routes.sanntid import router as sanntid_router
from frontend.routes.transkripsjon import router as transkripsjon_router
from shared.core.runtime import job_store, start_arbeider, stopp_arbeider
from shared.core.settings import JOB_RETENTION_SECONDS
from shared.diagnostics import configure_diagnostics

logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("faster_whisper").setLevel(logging.ERROR)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    prosess = start_arbeider()
    async def expire_results():
        while True:
            await asyncio.to_thread(job_store.cleanup_completed, JOB_RETENTION_SECONDS)
            await asyncio.sleep(300)

    cleanup = asyncio.create_task(expire_results())
    try:
        yield
    finally:
        cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup
        stopp_arbeider(prosess)


def create_app() -> FastAPI:
    configure_diagnostics()
    app = FastAPI(title="Transkribering", lifespan=lifespan)
    app.add_middleware(EntraAuthMiddleware)

    _STATIC = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(_STATIC), html=True), name="static")

    @app.get("/", include_in_schema=False)
    def rot():
        return FileResponse(str(_STATIC / "index.html"))

    app.include_router(helse_router)
    app.include_router(transkripsjon_router)
    app.include_router(referat_router)
    app.include_router(sanntid_router)

    return app


app = create_app()
