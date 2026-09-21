#!/usr/bin/env bash
# Build and push the audio-capable vLLM image used by k8s/vllm-whisper.yaml.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-ao-ki-taskforce-prod-2472}"
REGION="${REGION:-europe-west4}"
REPOSITORY="${REPOSITORY:-vllm}"
IMAGE_NAME="${IMAGE_NAME:-vllm-whisper-audio}"
TAG="${TAG:-latest}"
PLATFORM="${PLATFORM:-linux/amd64}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${TAG}"

echo "▶ Building and pushing $IMAGE ($PLATFORM)"
docker buildx build \
  --platform "$PLATFORM" \
  -f images/vllm-whisper/Dockerfile \
  -t "$IMAGE" \
  --push \
  images/vllm-whisper

echo "$IMAGE"
