import os
import subprocess
import sys


def test_api_can_start_without_local_worker_flag():
    env = os.environ.copy()
    env["START_LOKAL_WORKER"] = "false"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from frontend.app import app; from shared.core.runtime import lokal_arbeider_aktiv; "
            "assert app.title; assert lokal_arbeider_aktiv is False",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0


def test_llm_backend_defaults_to_openai_proxy():
    env = os.environ.copy()
    env.pop("LLM_BACKEND", None)
    env.pop("LLM_MODELL", None)
    env.pop("OLLAMA_MODELL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from shared.core.settings import LLM_BACKEND, LLM_MODELL; "
            "assert LLM_BACKEND == 'openai'; assert LLM_MODELL == 'borealis-12b'",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0


def test_llm_backend_ollama_gets_local_default_model():
    env = os.environ.copy()
    env["LLM_BACKEND"] = "ollama"
    env.pop("LLM_MODELL", None)
    env.pop("OLLAMA_MODELL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from shared.core.settings import LLM_BACKEND, LLM_MODELL, OLLAMA_URL; "
            "assert LLM_BACKEND == 'ollama'; assert LLM_MODELL == 'qwen3:8b'; "
            "assert OLLAMA_URL == 'http://localhost:11434'",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
