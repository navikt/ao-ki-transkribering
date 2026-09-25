#!/usr/bin/env bash
# Start a GPU deployment by trying explicit per-zone GPU node pools in order.
#
# Examples:
#   ./scripts/start-gpu-deployment-with-fallback.sh vllm-whisper
#   ./scripts/start-gpu-deployment-with-fallback.sh vllm-borealis
set -euo pipefail

NAMESPACE="${NAMESPACE:-vllm}"
DEPLOYMENT="${1:-${DEPLOYMENT:-}}"
APP_LABEL="${APP_LABEL:-$DEPLOYMENT}"
POOLS="${POOLS:-gpu-l4-a gpu-l4-b gpu-l4-c}"
SCHEDULE_TIMEOUT_SECONDS="${SCHEDULE_TIMEOUT_SECONDS:-300}"
ROLLOUT_TIMEOUT_SECONDS="${ROLLOUT_TIMEOUT_SECONDS:-900}"
POLL_SECONDS="${POLL_SECONDS:-10}"
WAIT_ROLLOUT="${WAIT_ROLLOUT:-true}"
KEEP_PENDING="${KEEP_PENDING:-false}"

usage() {
  cat <<EOF
Usage: $0 <deployment> [--keep-pending]

Examples:
  $0 vllm-whisper
  $0 vllm-borealis

Options:
  --keep-pending  Leave the last attempted pod pending if no pool has capacity.

Environment:
  NAMESPACE                 Default: vllm
  APP_LABEL                 Default: <deployment>
  POOLS                     Default: "gpu-l4-a gpu-l4-b gpu-l4-c"
  SCHEDULE_TIMEOUT_SECONDS  Default: 300
  ROLLOUT_TIMEOUT_SECONDS   Default: 900
  POLL_SECONDS              Default: 10
  WAIT_ROLLOUT              Default: true
EOF
}

if [[ -z "$DEPLOYMENT" ]]; then
  usage >&2
  exit 2
fi

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-pending)
      KEEP_PENDING=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

pod_name() {
  kubectl -n "$NAMESPACE" get pods -l "app=$APP_LABEL" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

pod_node() {
  local pod="$1"
  kubectl -n "$NAMESPACE" get pod "$pod" \
    -o jsonpath='{.spec.nodeName}' 2>/dev/null || true
}

pod_phase() {
  local pod="$1"
  kubectl -n "$NAMESPACE" get pod "$pod" \
    -o jsonpath='{.status.phase}' 2>/dev/null || true
}

scale_down_and_wait() {
  kubectl -n "$NAMESPACE" scale "deployment/$DEPLOYMENT" --replicas=0 >/dev/null
  for _ in $(seq 1 60); do
    local count
    count=$(kubectl -n "$NAMESPACE" get pods -l "app=$APP_LABEL" --no-headers 2>/dev/null | wc -l | tr -d ' ')
    [[ "$count" == "0" ]] && return 0
    sleep 2
  done
}

try_pool() {
  local pool="$1"
  echo ""
  echo "▶ Trying $DEPLOYMENT on GPU pool: $pool"

  scale_down_and_wait

  kubectl -n "$NAMESPACE" patch "deployment/$DEPLOYMENT" --type=json \
    -p "[{\"op\":\"replace\",\"path\":\"/spec/template/spec/nodeSelector\",\"value\":{\"cloud.google.com/gke-nodepool\":\"$pool\"}}]" \
    >/dev/null
  kubectl -n "$NAMESPACE" scale "deployment/$DEPLOYMENT" --replicas=1 >/dev/null

  local deadline=$((SECONDS + SCHEDULE_TIMEOUT_SECONDS))
  local pod=""
  while (( SECONDS < deadline )); do
    pod=$(pod_name)
    if [[ -n "$pod" ]]; then
      local node
      node=$(pod_node "$pod")
      local phase
      phase=$(pod_phase "$pod")
      if [[ -n "$node" ]]; then
        echo "✓ Pod $pod scheduled on $node (phase: $phase)"
        if [[ "$WAIT_ROLLOUT" == "true" ]]; then
          echo "▶ Waiting for $DEPLOYMENT rollout..."
          kubectl -n "$NAMESPACE" rollout status "deployment/$DEPLOYMENT" --timeout="${ROLLOUT_TIMEOUT_SECONDS}s"
        fi
        return 0
      fi
    fi
    sleep "$POLL_SECONDS"
  done

  echo "✗ No schedulable GPU from pool $pool within ${SCHEDULE_TIMEOUT_SECONDS}s"
  if [[ -n "$pod" ]]; then
    kubectl -n "$NAMESPACE" describe pod "$pod" | tail -60 || true
  fi
  return 1
}

for pool in $POOLS; do
  if try_pool "$pool"; then
    echo ""
    echo "$DEPLOYMENT is running via pool $pool."
    exit 0
  fi
done

echo ""
echo "No fallback GPU pool could schedule $DEPLOYMENT."
if ! $KEEP_PENDING; then
  echo "Scaling $DEPLOYMENT back to 0. Re-run with --keep-pending to leave the last request open."
  scale_down_and_wait
else
  echo "Leaving the last $DEPLOYMENT pod pending."
fi
exit 1
