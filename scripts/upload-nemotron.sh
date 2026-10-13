#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON=${PYTHON:-.venv/bin/python}
REVISION=ea30d66debe3740a08b573244286791d423d6b3e
BUCKET=${MODEL_BUCKET:-ao-ki-taskforce-prod-2472-modeller}
CACHE=$("$PYTHON" -c 'from huggingface_hub import snapshot_download; import sys; print(snapshot_download("nvidia/nemotron-3.5-asr-streaming-0.6b", revision=sys.argv[1], allow_patterns=["config.json", "generation_config.json", "processor_config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"]))' "$REVISION")
FILES=()
for name in config.json generation_config.json processor_config.json tokenizer.json tokenizer_config.json model.safetensors; do
  FILES+=("$CACHE/$name")
done
gcloud storage cp "${FILES[@]}" "gs://$BUCKET/nemotron/$REVISION/"
