import multiprocessing

from core.settings import ARBEIDSMAPPE, MODELL_ID, START_LOKAL_WORKER
from services.jobs import JobStore
from workers.transkripsjon import arbeider

mp_ctx = multiprocessing.get_context("spawn")
jobbkø: multiprocessing.Queue = mp_ctx.Queue()
arbeider_klar = mp_ctx.Event()
job_store = JobStore(ARBEIDSMAPPE)
lokal_arbeider_aktiv = START_LOKAL_WORKER


def start_arbeider() -> multiprocessing.Process | None:
    if not START_LOKAL_WORKER:
        return None
    prosess = mp_ctx.Process(
        target=arbeider,
        args=(jobbkø, MODELL_ID, arbeider_klar),
        daemon=True,
    )
    prosess.start()
    return prosess


def stopp_arbeider(prosess: multiprocessing.Process | None) -> None:
    if prosess is None:
        return
    jobbkø.put(None)
    prosess.join(timeout=5)
