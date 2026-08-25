import os
import tempfile
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = os.getenv("HF_HUB_OFFLINE", "1")

MODELL_ID = os.getenv("WHISPER_MODELL", "NbAiLab/nb-whisper-medium")
_arbeidsmappe = os.getenv("ARBEIDSMAPPE")
ARBEIDSMAPPE = Path(_arbeidsmappe) if _arbeidsmappe else Path(tempfile.mkdtemp(prefix="transkribering_"))
TRANSKRIPSJON_BACKEND = os.getenv("TRANSKRIPSJON_BACKEND", "local")  # "local" | "remote"
TRANSKRIPSJON_SERVICE_URL = os.getenv("TRANSKRIPSJON_SERVICE_URL", "http://127.0.0.1:9000")
_start_lokal_worker_default = "true" if TRANSKRIPSJON_BACKEND == "local" else "false"
START_LOKAL_WORKER = os.getenv("START_LOKAL_WORKER", _start_lokal_worker_default).lower() in {
    "1",
    "true",
    "ja",
    "yes",
}
