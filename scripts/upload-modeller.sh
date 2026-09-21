#!/usr/bin/env bash
# Last ned modeller fra HuggingFace og last opp til GCS.
# Krever: hf eller huggingface-cli (pip install -U "huggingface_hub[cli]"), gcloud CLI
#
# Bruk:
#   ./scripts/upload-modeller.sh
#   ./scripts/upload-modeller.sh --kun-whisper
#   ./scripts/upload-modeller.sh --kun-borealis
set -euo pipefail

BUCKET="ao-ki-taskforce-prod-2472-modeller"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

KUN_WHISPER=false
KUN_BOREALIS=false
FORCE=false
for arg in "$@"; do
  case $arg in
    --kun-whisper)  KUN_WHISPER=true  ;;
    --kun-borealis) KUN_BOREALIS=true ;;
    --force)        FORCE=true        ;;
  esac
done

last_ned_hf() {
  local repo="$1"
  local target="$2"

  if command -v hf >/dev/null 2>&1; then
    hf download "$repo" --local-dir "$target"
  elif command -v huggingface-cli >/dev/null 2>&1; then
    huggingface-cli download "$repo" \
      --local-dir "$target" \
      --local-dir-use-symlinks False
  else
    echo "✗ Mangler HuggingFace CLI. Installer med: pip install -U 'huggingface_hub[cli]'"
    exit 1
  fi
}

echo "▶ Sjekker innlogging..."
gcloud auth print-access-token >/dev/null 2>&1 || {
  echo "✗ Ikke logget inn. Kjør: gcloud auth login"
  exit 1
}

if ! $KUN_BOREALIS; then
  echo ""
  echo "━━━ nb-whisper-large (~3 GB) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Sjekk om allerede lastet opp
  if ! $FORCE && gcloud storage ls "gs://${BUCKET}/whisper/" >/dev/null 2>&1; then
    echo "✓ Allerede i GCS — hopper over (bruk --force for å overskrive)"
  else
    echo "▶ Laster ned fra HuggingFace..."
    last_ned_hf NbAiLab/nb-whisper-large "${TMPDIR}/nb-whisper-large"

    echo "▶ Laster opp til gs://${BUCKET}/whisper/ ..."
    gcloud storage cp --recursive "${TMPDIR}/nb-whisper-large/" \
      "gs://${BUCKET}/whisper/"
    echo "✓ nb-whisper-large lastet opp"
  fi
fi

if ! $KUN_WHISPER; then
  echo ""
  echo "━━━ Borealis-12b (~24 GB BF16) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Sjekk om allerede lastet opp
  if ! $FORCE && gcloud storage ls "gs://${BUCKET}/borealis-12b/" >/dev/null 2>&1; then
    echo "✓ Allerede i GCS — hopper over"
  else
    echo "▶ Laster ned fra HuggingFace (dette tar ~10 min avhengig av båndbredde)..."
    last_ned_hf NbAiLab/borealis-12b "${TMPDIR}/borealis-12b"

    echo "▶ Laster opp til gs://${BUCKET}/borealis-12b/ ..."
    gcloud storage cp --recursive "${TMPDIR}/borealis-12b/" \
      "gs://${BUCKET}/borealis-12b/"
    echo "✓ Borealis-12b lastet opp"
  fi
fi

echo ""
echo "━━━ Ferdig ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
gcloud storage ls --recursive "gs://${BUCKET}/" | grep -E "^gs://"
