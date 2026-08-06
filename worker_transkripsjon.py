"""Standalone transcription worker entrypoint.

Run with:
  python -m worker_transkripsjon
"""

import signal

from core.runtime import arbeider_klar, jobbkø
from core.settings import MODELL_ID
from workers.transkripsjon import arbeider


def main() -> None:
    def _request_stop(signum, frame):
        jobbkø.put(None)

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    arbeider(jobbkø, MODELL_ID, arbeider_klar)


if __name__ == "__main__":
    main()
