import multiprocessing

from settings import MODELL_ID
from transkribering.batch import arbeider

mp_ctx = multiprocessing.get_context("spawn")
jobbkø: multiprocessing.Queue = mp_ctx.Queue()
arbeider_klar = mp_ctx.Event()


def start_arbeider() -> multiprocessing.Process:
    prosess = mp_ctx.Process(
        target=arbeider,
        args=(jobbkø, MODELL_ID, arbeider_klar),
        daemon=True,
    )
    prosess.start()
    return prosess


def stopp_arbeider(prosess: multiprocessing.Process) -> None:
    jobbkø.put(None)
    prosess.join(timeout=5)
