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
SECURE=$(tofu -chdir="$TF_DIR" output -raw model_security_enabled)
GATEWAY_CIDR=$(tofu -chdir="$TF_DIR" output -raw model_gateway_subnet_cidr)
# TLS pods must be rolled out before switching LiteLLM to HTTPS.
[[ "${MODEL_SECURITY_PREPARE:-false}" == true ]] && SECURE=true
PYTHON=${PYTHON:-.venv/bin/python}
RENDER_ARGS=()
if [[ "$SECURE" == true ]]; then
  RENDER_ARGS=(--secure --gateway-cidr "$GATEWAY_CIDR")
fi

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
if [[ "$SECURE" == true ]]; then
  if ! $DRY_RUN; then
    kubectl get secret model-api-tls model-api-auth -n vllm >/dev/null
  fi
  kubectl create configmap model-api-security -n vllm --from-file=k8s/model_security.py \
    --dry-run=client -o yaml | $KUBECTL_CMD -f -
fi

echo ""
echo "▶ Kontinuerlig GPU-forsøk (CronJobs, automatisk stopp suspendert)..."
$KUBECTL_CMD -f k8s/working-hours-scaler.yaml

echo ""
echo "▶ vLLM-whisper (Deployment + Internal LB: $WHISPER_IP)..."
"$PYTHON" scripts/render-model-manifest.py k8s/vllm-whisper.yaml "$WHISPER_IP" "${RENDER_ARGS[@]}" \
  | $KUBECTL_CMD -f -

echo ""
echo "▶ vLLM-borealis (Deployment + Internal LB: $BOREALIS_IP)..."
"$PYTHON" scripts/render-model-manifest.py k8s/vllm-borealis.yaml "$BOREALIS_IP" "${RENDER_ARGS[@]}" \
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
echo "  4. Bruk separate NAIS-nøkler fra Secret Manager, aldri gatewayens master key."
echo "     Se docs/model-api-security.md for sikker overgang."
