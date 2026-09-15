#!/usr/bin/env bash
# One-step deploy for the GPU stack:
#   1. OpenTofu creates/updates GCP resources.
#   2. Kubernetes manifests add vLLM services and working-hours scaling.
#
# Usage:
#   ./scripts/deploy-gpu-stack.sh
#   ./scripts/deploy-gpu-stack.sh -auto-approve
set -euo pipefail

TF_DIR="terraform/gke"

echo "▶ Initialiserer OpenTofu..."
tofu -chdir="$TF_DIR" init

echo ""
echo "▶ Kjører OpenTofu apply..."
tofu -chdir="$TF_DIR" apply "$@"

echo ""
echo "▶ Legger vLLM og arbeidstid-skalering i GKE..."
./scripts/apply-k8s.sh
