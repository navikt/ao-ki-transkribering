#!/usr/bin/env bash
# Manually escalate one or both vLLM deployments to A100 GPU pools.
#
# This is intentionally not used by working-hours automation. Use it only when
# L4 pods remain pending and the extra A100 cost is acceptable.
set -euo pipefail

DEPLOYMENTS=()
ASSUME_YES=false

usage() {
  cat <<EOF
Usage: $0 [--yes] [deployment...]

Examples:
  $0 --yes
  $0 --yes vllm-borealis
  $0 --yes vllm-whisper vllm-borealis

Options:
  --yes    Confirm that A100 cost is acceptable.

Environment:
  A100_POOLS                Default: "gpu-a100-a gpu-a100-b"
  SCHEDULE_TIMEOUT_SECONDS  Default: 300
  WAIT_ROLLOUT              Default: false
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)
      ASSUME_YES=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      DEPLOYMENTS+=("$1")
      shift
      ;;
  esac
done

if [[ "${#DEPLOYMENTS[@]}" -eq 0 ]]; then
  DEPLOYMENTS=(vllm-whisper vllm-borealis)
fi

if ! $ASSUME_YES; then
  cat >&2 <<EOF
This targets A100 GPU pools and may start expensive GPU nodes.
Re-run with --yes when that is intentional.
EOF
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
POOLS="${A100_POOLS:-gpu-a100-a gpu-a100-b}"
SCHEDULE_TIMEOUT_SECONDS="${SCHEDULE_TIMEOUT_SECONDS:-300}"
WAIT_ROLLOUT="${WAIT_ROLLOUT:-false}"

status=0
for deployment in "${DEPLOYMENTS[@]}"; do
  echo ""
  echo "Starting $deployment on manual A100 pools: $POOLS"
  POOLS="$POOLS" \
    KEEP_PENDING=true \
    WAIT_ROLLOUT="$WAIT_ROLLOUT" \
    SCHEDULE_TIMEOUT_SECONDS="$SCHEDULE_TIMEOUT_SECONDS" \
    "$SCRIPT_DIR/start-gpu-deployment-with-fallback.sh" "$deployment" || status=$?
done

exit "$status"
