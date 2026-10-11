#!/usr/bin/env bash
# Update through the supported NAIS secret API; Kubernetes secret reads are restricted.
set -euo pipefail
umask 077
ENVIRONMENT=${1:?Usage: sync-nais-model-key.sh dev|prod}
case "$ENVIRONMENT" in dev|prod) ;; *) echo "Expected dev or prod" >&2; exit 1 ;; esac
PROJECT=${PROJECT:-ao-ki-taskforce-prod-2472}
SECRET_NAME=${SECRET_NAME:-ao-ki-model-api}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
gcloud secrets versions access latest --secret="nais-$ENVIRONMENT-model-key" --project="$PROJECT" > "$TMP/key"
for key in AI_PROXY_API_KEY TRANSKRIPSJON_API_KEY; do
  nais secret set "$SECRET_NAME" --team ao-ki-taskforce \
    --environment "$ENVIRONMENT-gcp" --key "$key" --value-from-stdin < "$TMP/key"
done
echo "Updated $ENVIRONMENT NAIS model credentials. NAIS restarts workloads referencing the secret."
