#!/usr/bin/env bash
# Create credentials once and synchronize GKE secrets. Never prints secret values.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PROJECT=${PROJECT:-ao-ki-taskforce-prod-2472}
RENEW=false
if [[ "${1:-}" == --renew ]]; then
  RENEW=true
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--renew]" >&2; exit 1
fi
TF_DIR=terraform/gke
for tool in gcloud kubectl tofu openssl jq; do
  command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done
CLUSTER=$(tofu -chdir="$TF_DIR" output -raw cluster_name)
CONTEXT="gke_${PROJECT}_europe-west4_${CLUSTER}"
kubectl --context "$CONTEXT" get namespace vllm >/dev/null
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
secrets=(nais-dev-model-key nais-prod-model-key vllm-api-key vllm-ca vllm-tls-bundle)
count=0
for secret in "${secrets[@]}"; do
  gcloud secrets describe "$secret" --project="$PROJECT" >/dev/null
  versions=$(gcloud secrets versions list "$secret" --project="$PROJECT" --filter='state=ENABLED' --limit=1 --format='value(name)')
  [[ -z "$versions" ]] || count=$((count + 1))
done
if [[ "$count" != 0 && "$count" != 5 ]]; then
  echo "Partially initialized secrets: stop and recover explicitly; no credentials overwritten." >&2
  exit 1
fi
if [[ "$count" == 0 ]]; then
  WHISPER_IP=$(tofu -chdir="$TF_DIR" output -raw vllm_whisper_ilb_ip)
  BOREALIS_IP=$(tofu -chdir="$TF_DIR" output -raw vllm_borealis_ilb_ip)
  export WHISPER_IP BOREALIS_IP
  openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
    -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -subj '/CN=ao-ki-model-ca' -keyout "$TMP/ca.key" -out "$TMP/ca.crt"
  openssl req -newkey rsa:3072 -nodes -subj '/CN=ao-ki-model-server' \
    -keyout "$TMP/tls.key" -out "$TMP/server.csr"
  openssl x509 -req -in "$TMP/server.csr" -CA "$TMP/ca.crt" -CAkey "$TMP/ca.key" \
    -CAcreateserial -days 90 -sha256 -extfile scripts/model-tls.cnf -out "$TMP/tls.crt"
  jq -n --rawfile cert "$TMP/tls.crt" --rawfile key "$TMP/tls.key" --rawfile ca "$TMP/ca.crt" --rawfile ca_key "$TMP/ca.key" \
    '{cert: $cert, key: $key, ca: $ca, ca_key: $ca_key}' > "$TMP/bundle"
  for secret in nais-dev-model-key nais-prod-model-key vllm-api-key; do
    printf 'sk-%s' "$(openssl rand -hex 32)" > "$TMP/$secret"
    gcloud secrets versions add "$secret" --project="$PROJECT" --data-file="$TMP/$secret" >/dev/null
  done
  gcloud secrets versions add vllm-ca --project="$PROJECT" --data-file="$TMP/ca.crt" >/dev/null
  gcloud secrets versions add vllm-tls-bundle --project="$PROJECT" --data-file="$TMP/bundle" >/dev/null
else
  gcloud secrets versions access latest --secret=vllm-tls-bundle --project="$PROJECT" > "$TMP/bundle"
  jq -r '.cert' "$TMP/bundle" > "$TMP/tls.crt"
  jq -r '.key' "$TMP/bundle" > "$TMP/tls.key"
  jq -r '.ca' "$TMP/bundle" > "$TMP/ca.crt"
  jq -er '.ca_key' "$TMP/bundle" > "$TMP/ca.key"
  gcloud secrets versions access latest --secret=vllm-api-key --project="$PROJECT" > "$TMP/vllm-api-key"
fi
if $RENEW; then
  WHISPER_IP=$(tofu -chdir="$TF_DIR" output -raw vllm_whisper_ilb_ip)
  BOREALIS_IP=$(tofu -chdir="$TF_DIR" output -raw vllm_borealis_ilb_ip)
  export WHISPER_IP BOREALIS_IP
  openssl req -newkey rsa:3072 -nodes -subj '/CN=ao-ki-model-server' \
    -keyout "$TMP/tls.key" -out "$TMP/server.csr"
  openssl x509 -req -in "$TMP/server.csr" -CA "$TMP/ca.crt" -CAkey "$TMP/ca.key" \
    -CAcreateserial -days 90 -sha256 -extfile scripts/model-tls.cnf -out "$TMP/tls.crt"
  jq -n --rawfile cert "$TMP/tls.crt" --rawfile key "$TMP/tls.key" --rawfile ca "$TMP/ca.crt" --rawfile ca_key "$TMP/ca.key" \
    '{cert: $cert, key: $key, ca: $ca, ca_key: $ca_key}' > "$TMP/bundle"
  gcloud secrets versions add vllm-tls-bundle --project="$PROJECT" --data-file="$TMP/bundle" >/dev/null
fi
openssl verify -x509_strict -purpose sslserver -CAfile "$TMP/ca.crt" "$TMP/tls.crt"
for output in vllm_whisper_ilb_ip vllm_borealis_ilb_ip; do
  address=$(tofu -chdir="$TF_DIR" output -raw "$output")
  openssl verify -x509_strict -purpose sslserver -CAfile "$TMP/ca.crt" -verify_ip "$address" "$TMP/tls.crt"
done
openssl x509 -checkend 604800 -noout -in "$TMP/tls.crt" || {
  echo "Certificate expires within 7 days; renew before proceeding." >&2; exit 1;
}
kubectl --context "$CONTEXT" create secret tls model-api-tls -n vllm \
  --cert="$TMP/tls.crt" --key="$TMP/tls.key" --dry-run=client -o yaml \
  | kubectl --context "$CONTEXT" apply -f - >/dev/null
kubectl --context "$CONTEXT" create secret generic model-api-auth -n vllm \
  --from-file="api-key=$TMP/vllm-api-key" --dry-run=client -o yaml \
  | kubectl --context "$CONTEXT" apply -f - >/dev/null
echo "GKE TLS/auth secrets synchronized. Application credentials remain in Secret Manager."
echo "Certificate lifetime: 90 days; arrange renewal before enabling production traffic."
