import os
import tempfile
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = os.getenv("HF_HUB_OFFLINE", "1")

MODELL_ID = os.getenv("WHISPER_MODELL", "NbAiLab/nb-whisper-medium")
_arbeidsmappe = os.getenv("ARBEIDSMAPPE")
ARBEIDSMAPPE = Path(_arbeidsmappe) if _arbeidsmappe else Path(tempfile.mkdtemp(prefix="transkribering_"))
STT_BACKEND = os.getenv("STT_BACKEND", "lokal")  # "lokal" | "soniox"
START_LOKAL_WORKER = os.getenv("START_LOKAL_WORKER", "true").lower() in {
    "1",
    "true",
    "ja",
    "yes",
}
