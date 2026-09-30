#!/usr/bin/env bash
# GPU-free smoke test for the app API and static configuration endpoints.
set -euo pipefail

APP_URL="${APP_URL:-http://127.0.0.1:8765}"
RUN_LLM_STATUS="${RUN_LLM_STATUS:-false}"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

step() {
  echo ""
  echo "▶ $1"
}

request() {
  local label="$1"
  local path="$2"
  curl -sS \
    -w $'\nHTTP %{http_code}\n' \
    "${APP_URL%/}${path}" > "$tmp"

  local status
  status=$(tail -n1 "$tmp")
  if [[ "$status" != HTTP\ 2* ]]; then
    echo "✗ $label failed: $status" >&2
    sed '$d' "$tmp" >&2
    exit 1
  fi
  echo "✓ $label: $status"
  sed '$d' "$tmp"
}

assert_json() {
  local label="$1"
  local expression="$2"
  sed '$d' "$tmp" | python3 -c "
import json
import sys

data = json.load(sys.stdin)
if not ($expression):
    raise SystemExit('$label failed')
"
}

step "API liveness"
request "/isAlive" "/isAlive"
assert_json "/isAlive" "data.get('status') == 'ok'"

step "API readiness"
request "/isReady" "/isReady"
assert_json "/isReady" "data.get('status') in {'ok', 'ekstern arbeider'}"

step "System configuration"
request "/system/info" "/system/info"
assert_json "/system/info" "data.get('asr', {}).get('backend') in {'local', 'remote'} and data.get('llm', {}).get('modell')"

step "Referat scenarios"
request "/llm/referat-scenarier" "/llm/referat-scenarier"
assert_json "/llm/referat-scenarier" "len(data.get('scenarier', [])) >= 3 and any(s.get('id') == 'veiledermote' for s in data.get('scenarier', []))"

if [[ "$RUN_LLM_STATUS" == "true" ]]; then
  step "LLM status"
  request "/llm/status" "/llm/status"
  assert_json "/llm/status" "data.get('tilgjengelig') is True and data.get('standard_modell')"
fi

echo ""
echo "App smoke test completed for ${APP_URL%/}."
