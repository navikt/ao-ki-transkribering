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
from frontend.routes.referat import router as referat_router, summary_jobs
from frontend.routes.sanntid import router as sanntid_router
from frontend.routes.recordings import router as recordings_router, cleanup_sessions
from frontend.routes.transkripsjon import router as transkripsjon_router
from shared.core.runtime import job_store, start_arbeider, stopp_arbeider
from shared.core.settings import JOB_RETENTION_SECONDS
from shared.diagnostics import configure_diagnostics
from frontend.monitoring import HttpMetricsMiddleware, start_metrics_server, stop_metrics_server

logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("faster_whisper").setLevel(logging.ERROR)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    metrics_server = start_metrics_server()
    prosess = None
    cleanup = None
    async def expire_results():
        while True:
            await asyncio.to_thread(job_store.cleanup_completed, JOB_RETENTION_SECONDS)
            await asyncio.to_thread(summary_jobs.store.cleanup_completed, JOB_RETENTION_SECONDS)
            cleanup_sessions()
            await asyncio.sleep(300)

    try:
        prosess = start_arbeider()
        cleanup = asyncio.create_task(expire_results())
        yield
    finally:
        try:
            await summary_jobs.shutdown()
            if cleanup is not None:
                cleanup.cancel()
                with suppress(asyncio.CancelledError):
                    await cleanup
            stopp_arbeider(prosess)
        finally:
            await stop_metrics_server(metrics_server)


def create_app() -> FastAPI:
    configure_diagnostics()
    app = FastAPI(title="Transkribering", lifespan=lifespan)
    app.add_middleware(EntraAuthMiddleware)

    _STATIC = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(_STATIC), html=True), name="static")

    @app.get("/", include_in_schema=False)
    def rot():
        return FileResponse(str(_STATIC / "index.html"))

    routers = (helse_router, transkripsjon_router, referat_router, sanntid_router, recordings_router)
    for router in routers:
        app.include_router(router)

    paths = [route.path for router in (app, *routers) for route in router.routes if hasattr(route, "path")]
    app.add_middleware(HttpMetricsMiddleware, routes=paths)

    return app


app = create_app()
