#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PYTHON=${PYTHON:-"$ROOT/.venv/bin/python"}
RUNTIME="$ROOT/.cache/speech-runtime"

if [[ ! -x "$PYTHON" || ! -d "$RUNTIME/transformers" ]]; then
  echo "Set up the optional Nemotron runtime first (see README: Local Nemotron)." >&2
  exit 1
fi

export PYTHONPATH="$RUNTIME:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export LOKAL_ASR_BACKEND=nemotron
export TRANSKRIPSJON_BACKEND=local
export START_LOKAL_WORKER=true
export HF_HUB_OFFLINE=1
export ECAPA_CACHE="${ECAPA_CACHE:-$ROOT/.cache/speechbrain}"
export LLM_BACKEND="${LLM_BACKEND:-ollama}"
export LLM_MODELL="${LLM_MODELL:-borealis:12b}"

exec "$PYTHON" -m uvicorn frontend.app:app --host 127.0.0.1 --port "${PORT:-8765}" "$@"
