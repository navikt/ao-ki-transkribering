import os
import tempfile
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = os.getenv("HF_HUB_OFFLINE", "1")

MODELL_ID = os.getenv("WHISPER_MODELL", "NbAiLab/nb-whisper-medium")
LLM_BACKEND = os.getenv("LLM_BACKEND", "openai").lower()
_standard_llm_modell = "qwen3:8b" if LLM_BACKEND == "ollama" else "borealis-12b"
LLM_MODELL = os.getenv("LLM_MODELL", os.getenv("OLLAMA_MODELL", _standard_llm_modell))
_arbeidsmappe = os.getenv("ARBEIDSMAPPE")
ARBEIDSMAPPE = Path(_arbeidsmappe) if _arbeidsmappe else Path(tempfile.mkdtemp(prefix="transkribering_"))
TRANSKRIPSJON_BACKEND = os.getenv("TRANSKRIPSJON_BACKEND", "local")  # "local" | "remote"
AI_PROXY_URL = os.getenv("AI_PROXY_URL", os.getenv("TRANSKRIPSJON_SERVICE_URL", "http://127.0.0.1:9000"))
AI_PROXY_API_KEY = os.getenv("AI_PROXY_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
TRANSKRIPSJON_SERVICE_URL = os.getenv("TRANSKRIPSJON_SERVICE_URL", AI_PROXY_URL)
TRANSKRIPSJON_API_KEY = os.getenv("TRANSKRIPSJON_API_KEY", AI_PROXY_API_KEY)
TRANSKRIPSJON_API_PATH = os.getenv("TRANSKRIPSJON_API_PATH", "/transkriber")
_start_lokal_worker_default = "true" if TRANSKRIPSJON_BACKEND == "local" else "false"
START_LOKAL_WORKER = os.getenv("START_LOKAL_WORKER", _start_lokal_worker_default).lower() in {
    "1",
    "true",
    "ja",
    "yes",
}
