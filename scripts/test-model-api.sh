#!/usr/bin/env bash
# Smoke-test the model gateway and, optionally, the app API.
set -euo pipefail

TF_DIR="${TF_DIR:-terraform/gke}"
LITELLM_URL="${LITELLM_URL:-}"
LITELLM_KEY="${LITELLM_KEY:-}"
LITELLM_ID_TOKEN="${LITELLM_ID_TOKEN:-}"
APP_URL="${APP_URL:-}"
TEST_AUDIO="${TEST_AUDIO:-testdata/audio/test002.wav}"
RUN_AUDIO="${RUN_AUDIO:-true}"
RUN_LLM_ACTION="${RUN_LLM_ACTION:-false}"

if [[ -z "$LITELLM_URL" ]]; then
  LITELLM_URL=$(tofu -chdir="$TF_DIR" output -raw litellm_url)
fi

if [[ -z "$LITELLM_KEY" ]]; then
  echo "Set LITELLM_KEY to a restricted application key; master-key fallback is disabled." >&2
  exit 1
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
    exit 1
  fi
  echo "✓ $label: $status"
}

umask 077
tmp_dir=$(mktemp -d)
tmp="$tmp_dir/body"
trap 'rm -rf "$tmp_dir"' EXIT
printf 'Authorization: Bearer %s\n' "$LITELLM_KEY" > "$tmp_dir/headers"
if [[ -n "$LITELLM_ID_TOKEN" ]]; then
  printf 'X-Serverless-Authorization: Bearer %s\n' "$LITELLM_ID_TOKEN" >> "$tmp_dir/headers"
fi

step "LiteLLM models"
curl -sS \
  -H "@$tmp_dir/headers" \
  -w $'\nHTTP %{http_code}\n' \
  "$LITELLM_URL/v1/models" > "$tmp"
require_http_ok "models" "$tmp"

if [[ "$RUN_AUDIO" == "true" ]]; then
  if [[ ! -f "$TEST_AUDIO" ]]; then
    echo "✗ Audio test file not found: $TEST_AUDIO" >&2
    exit 1
  fi

  step "Whisper transcription through LiteLLM"
  curl -sS \
    -H "@$tmp_dir/headers" \
    -H "Expect:" \
    -w $'\nHTTP %{http_code}\n' \
    -F "file=@${TEST_AUDIO}" \
    -F "model=nb-whisper-large" \
    -F "response_format=verbose_json" \
    -F "language=no" \
    "$LITELLM_URL/v1/audio/transcriptions" > "$tmp"
  require_http_ok "whisper transcription" "$tmp"
fi

if [[ -n "$APP_URL" ]]; then
  step "App LLM status"
  curl -sS \
    -w $'\nHTTP %{http_code}\n' \
    "$APP_URL/llm/status" > "$tmp"
  require_http_ok "app /llm/status" "$tmp"

  if [[ "$RUN_LLM_ACTION" == "true" ]]; then
    step "App LLM action"
    curl -sS \
      -H "Content-Type: application/json" \
      -w $'\nHTTP %{http_code}\n' \
      -d '{"transkripsjon":"Veileder: Hei. Bruker: Jeg trenger hjelp med arbeid og økonomi."}' \
      "$APP_URL/llm/handlinger/sammendrag" > "$tmp"
    require_http_ok "app LLM action" "$tmp"
  fi
fi

echo ""
echo "Model API smoke test completed."
