import multiprocessing
from typing import Optional

from core.settings import ARBEIDSMAPPE, MODELL_ID, START_LOKAL_WORKER
from services.jobs import JobStore
from worker.workers.transkripsjon import arbeider

mp_ctx = multiprocessing.get_context("spawn")

# Queue and Event require semaphore syscalls that are blocked in NAIS pods
# (restricted seccomp profile). Only create them when actually needed.
if START_LOKAL_WORKER:
    jobbkø: Optional[multiprocessing.Queue] = mp_ctx.Queue()
    arbeider_klar: Optional[multiprocessing.synchronize.Event] = mp_ctx.Event()
else:
    jobbkø = None
    arbeider_klar = None

job_store = JobStore(ARBEIDSMAPPE)
lokal_arbeider_aktiv = START_LOKAL_WORKER


def start_arbeider() -> multiprocessing.Process | None:
    if not START_LOKAL_WORKER:
        return None
    assert jobbkø is not None and arbeider_klar is not None
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
