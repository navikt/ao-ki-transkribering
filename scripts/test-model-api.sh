#!/usr/bin/env bash
# Smoke-test the model gateway and, optionally, the app API.
set -euo pipefail

TF_DIR="${TF_DIR:-terraform/gke}"
LITELLM_URL="${LITELLM_URL:-}"
LITELLM_KEY="${LITELLM_KEY:-}"
APP_URL="${APP_URL:-}"
TEST_AUDIO="${TEST_AUDIO:-testdata/audio/test002.wav}"
RUN_AUDIO="${RUN_AUDIO:-true}"
RUN_LLM_ACTION="${RUN_LLM_ACTION:-false}"

if [[ -z "$LITELLM_URL" ]]; then
  LITELLM_URL=$(tofu -chdir="$TF_DIR" output -raw litellm_url)
fi

if [[ -z "$LITELLM_KEY" ]]; then
  LITELLM_KEY=$(tofu -chdir="$TF_DIR" output -raw litellm_api_key)
fi

step() {
  echo ""
  echo "▶ $1"
}

require_http_ok() {
  local label="$1"
  local body="$2"
  local status
  status=$(tail -n1 "$body")
  if [[ "$status" != HTTP\ 2* ]]; then
    echo "✗ $label failed: $status" >&2
    sed '$d' "$body" >&2
    exit 1
  fi
  echo "✓ $label: $status"
}

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

step "LiteLLM models"
curl -sS \
  -H "Authorization: Bearer $LITELLM_KEY" \
  -w $'\nHTTP %{http_code}\n' \
  "$LITELLM_URL/v1/models" > "$tmp"
require_http_ok "models" "$tmp"
sed '$d' "$tmp"

if [[ "$RUN_AUDIO" == "true" ]]; then
  if [[ ! -f "$TEST_AUDIO" ]]; then
    echo "✗ Audio test file not found: $TEST_AUDIO" >&2
    exit 1
  fi

  step "Whisper transcription through LiteLLM"
  curl -sS \
    -H "Authorization: Bearer $LITELLM_KEY" \
    -H "Expect:" \
    -w $'\nHTTP %{http_code}\n' \
    -F "file=@${TEST_AUDIO}" \
    -F "model=nb-whisper-large" \
    -F "response_format=verbose_json" \
    -F "language=no" \
    "$LITELLM_URL/v1/audio/transcriptions" > "$tmp"
  require_http_ok "whisper transcription" "$tmp"
  sed '$d' "$tmp" | head -c 500
  echo ""
fi

if [[ -n "$APP_URL" ]]; then
  step "App LLM status"
  curl -sS \
    -w $'\nHTTP %{http_code}\n' \
    "$APP_URL/llm/status" > "$tmp"
  require_http_ok "app /llm/status" "$tmp"
  sed '$d' "$tmp"

  if [[ "$RUN_LLM_ACTION" == "true" ]]; then
    step "App LLM action"
    curl -sS \
      -H "Content-Type: application/json" \
      -w $'\nHTTP %{http_code}\n' \
      -d '{"transkripsjon":"Veileder: Hei. Bruker: Jeg trenger hjelp med arbeid og økonomi."}' \
      "$APP_URL/llm/handlinger/sammendrag" > "$tmp"
    require_http_ok "app LLM action" "$tmp"
    sed '$d' "$tmp"
  fi
fi

echo ""
echo "Model API smoke test completed."
