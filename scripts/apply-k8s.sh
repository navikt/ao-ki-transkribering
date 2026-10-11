#!/usr/bin/env bash
# Deploy vLLM manifests to GKE with IPs from OpenTofu outputs.
# Run AFTER tofu apply in terraform/gke/.
#
# Usage: ./scripts/apply-k8s.sh [--dry-run]
set -euo pipefail

TF_DIR="terraform/gke"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

echo "▶ Henter IPs fra OpenTofu..."
WHISPER_IP=$(tofu -chdir="$TF_DIR" output -raw vllm_whisper_ilb_ip)
BOREALIS_IP=$(tofu -chdir="$TF_DIR" output -raw vllm_borealis_ilb_ip)
LITELLM_URL=$(tofu -chdir="$TF_DIR" output -raw litellm_url)

echo "  vllm-whisper  ILB: $WHISPER_IP"
echo "  vllm-borealis ILB: $BOREALIS_IP"
echo "  LiteLLM URL:       $LITELLM_URL"

echo ""
echo "▶ Kobler til GKE-cluster..."
eval "$(tofu -chdir="$TF_DIR" output -raw kubeconfig_command)"

KUBECTL_CMD="kubectl apply"
$DRY_RUN && KUBECTL_CMD="kubectl apply --dry-run=client"

echo ""
echo "▶ Namespace og ServiceAccount..."
$KUBECTL_CMD -f k8s/namespace.yaml

echo ""
echo "▶ Kontinuerlig GPU-forsøk (CronJobs, automatisk stopp suspendert)..."
$KUBECTL_CMD -f k8s/working-hours-scaler.yaml

echo ""
echo "▶ vLLM-whisper (Deployment + Internal LB: $WHISPER_IP)..."
sed "s/loadBalancerIP: \"\"/loadBalancerIP: \"$WHISPER_IP\"/" k8s/vllm-whisper.yaml \
  | $KUBECTL_CMD -f -

echo ""
echo "▶ vLLM-borealis (Deployment + Internal LB: $BOREALIS_IP)..."
sed "s/loadBalancerIP: \"\"/loadBalancerIP: \"$BOREALIS_IP\"/" k8s/vllm-borealis.yaml \
  | $KUBECTL_CMD -f -

if ! $DRY_RUN; then
  echo ""
  echo "▶ Starter vLLM-modeller med GPU-sonefallback nå..."
  KEEP_PENDING=true WAIT_ROLLOUT=false ./scripts/start-gpu-deployment-with-fallback.sh vllm-whisper || true
  KEEP_PENDING=true WAIT_ROLLOUT=false ./scripts/start-gpu-deployment-with-fallback.sh vllm-borealis || true
fi

echo ""
echo "━━━ Ferdig ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Neste steg:"
echo "  1. vLLM-whisper og vLLM-borealis ber om én GPU hver, hele døgnet."
echo "     Ventende modeller prøver GPU-sonene på nytt hver time; tildelte noder beholdes."
echo "     Automatisk stopp er suspendert; tildelte GPU-er koster penger til de stoppes manuelt."
echo "     Start manuelt ved behov:"
echo "       ./scripts/start-gpu-deployment-with-fallback.sh vllm-whisper"
echo "       ./scripts/start-gpu-deployment-with-fallback.sh vllm-borealis"
echo ""
echo "  2. Følg oppstart:"
echo "       kubectl get pods -n vllm -w"
echo ""
echo "  3. Sett TRANSKRIPSJON_SERVICE_URL i NAIS-secret til:"
echo "       $LITELLM_URL"
echo ""
echo "  4. Hent API-nøkkel for NAIS-secret:"
echo "       tofu -chdir=$TF_DIR output litellm_api_key"
