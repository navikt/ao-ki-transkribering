import os
import tempfile
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"

MODELL_ID = os.getenv("WHISPER_MODELL", "NbAiLab/nb-whisper-medium")
ARBEIDSMAPPE = Path(tempfile.mkdtemp(prefix="transkribering_"))
STT_BACKEND = os.getenv("STT_BACKEND", "lokal")  # "lokal" | "soniox"
