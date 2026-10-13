#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${IMAGE:-europe-west4-docker.pkg.dev/ao-ki-taskforce-prod-2472/vllm/nemotron-asr:20261013}"
docker buildx build --platform linux/amd64 -f images/nemotron/Dockerfile -t "$IMAGE" --push .
echo "$IMAGE"
