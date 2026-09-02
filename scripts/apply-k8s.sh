#!/usr/bin/env bash
# Deploy vLLM manifests to GKE with IPs from Terraform outputs.
# Run AFTER terraform apply in terraform/gke/.
#
# Usage: ./scripts/apply-k8s.sh [--dry-run]
set -euo pipefail

TF_DIR="terraform/gke"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

echo "▶ Henter IPs fra Terraform..."
WHISPER_IP=$(terraform -chdir="$TF_DIR" output -raw vllm_whisper_ilb_ip)
BOREALIS_IP=$(terraform -chdir="$TF_DIR" output -raw vllm_borealis_ilb_ip)
LITELLM_URL=$(terraform -chdir="$TF_DIR" output -raw litellm_url)

echo "  vllm-whisper  ILB: $WHISPER_IP"
echo "  vllm-borealis ILB: $BOREALIS_IP"
echo "  LiteLLM URL:       $LITELLM_URL"

echo ""
echo "▶ Kobler til GKE-cluster..."
eval "$(terraform -chdir="$TF_DIR" output -raw kubeconfig_command)"

KUBECTL_CMD="kubectl apply"
$DRY_RUN && KUBECTL_CMD="kubectl apply --dry-run=client"

echo ""
echo "▶ Namespace og ServiceAccount..."
$KUBECTL_CMD -f k8s/namespace.yaml

echo ""
echo "▶ vLLM-whisper (Deployment + Internal LB: $WHISPER_IP)..."
sed "s/loadBalancerIP: \"\"/loadBalancerIP: \"$WHISPER_IP\"/" k8s/vllm-whisper.yaml \
  | $KUBECTL_CMD -f -

echo ""
echo "▶ vLLM-borealis (Deployment + Internal LB: $BOREALIS_IP)..."
sed "s/loadBalancerIP: \"\"/loadBalancerIP: \"$BOREALIS_IP\"/" k8s/vllm-borealis.yaml \
  | $KUBECTL_CMD -f -

echo ""
echo "━━━ Ferdig ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Neste steg:"
echo "  1. Vent til vLLM-podene er klare:"
echo "       kubectl get pods -n vllm -w"
echo ""
echo "  2. Sett TRANSKRIPSJON_SERVICE_URL i NAIS-secret til:"
echo "       $LITELLM_URL"
echo ""
echo "  3. Hent API-nøkkel for NAIS-secret:"
echo "       terraform -chdir=$TF_DIR output litellm_api_key"
