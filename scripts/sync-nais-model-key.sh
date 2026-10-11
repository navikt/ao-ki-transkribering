#!/usr/bin/env bash
# Merge only the model keys into the existing NAIS secret; preserve other entries.
set -euo pipefail
umask 077
ENVIRONMENT=${1:?Usage: sync-nais-model-key.sh dev|prod}
case "$ENVIRONMENT" in dev|prod) ;; *) echo "Expected dev or prod" >&2; exit 1 ;; esac
PROJECT=${PROJECT:-ao-ki-taskforce-prod-2472}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
gcloud secrets versions access latest --secret="nais-$ENVIRONMENT-model-key" --project="$PROJECT" > "$TMP/key"
jq -n --rawfile key "$TMP/key" '{data: {AI_PROXY_API_KEY: ($key|@base64), TRANSKRIPSJON_API_KEY: ($key|@base64)}}' > "$TMP/patch"
kubectl --context "$ENVIRONMENT-gcp" -n ao-ki-taskforce patch secret ao-ki-transkribering-secrets \
  --type=merge --patch-file="$TMP/patch" >/dev/null
echo "Updated $ENVIRONMENT NAIS model credentials. Restart/redeploy the application to read them."
