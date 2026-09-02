#!/usr/bin/env bash
# Last ned modeller fra HuggingFace og last opp til GCS.
# Krever: huggingface-cli (pip install huggingface_hub[cli]), gcloud CLI
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
for arg in "$@"; do
  case $arg in
    --kun-whisper)  KUN_WHISPER=true  ;;
    --kun-borealis) KUN_BOREALIS=true ;;
  esac
done

echo "▶ Sjekker innlogging..."
gcloud auth print-access-token >/dev/null 2>&1 || {
  echo "✗ Ikke logget inn. Kjør: gcloud auth login"
  exit 1
}

if ! $KUN_BOREALIS; then
  echo ""
  echo "━━━ nb-whisper-large (~3 GB) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Sjekk om allerede lastet opp
  if gcloud storage ls "gs://${BUCKET}/whisper/" >/dev/null 2>&1; then
    echo "✓ Allerede i GCS — hopper over (bruk --force for å overskrive)"
  else
    echo "▶ Laster ned fra HuggingFace..."
    huggingface-cli download NbAiLab/nb-whisper-large \
      --local-dir "${TMPDIR}/nb-whisper-large" \
      --local-dir-use-symlinks False

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
  if gcloud storage ls "gs://${BUCKET}/borealis-12b/" >/dev/null 2>&1; then
    echo "✓ Allerede i GCS — hopper over"
  else
    echo "▶ Laster ned fra HuggingFace (dette tar ~10 min avhengig av båndbredde)..."
    huggingface-cli download NbAiLab/borealis-12b \
      --local-dir "${TMPDIR}/borealis-12b" \
      --local-dir-use-symlinks False

    echo "▶ Laster opp til gs://${BUCKET}/borealis-12b/ ..."
    gcloud storage cp --recursive "${TMPDIR}/borealis-12b/" \
      "gs://${BUCKET}/borealis-12b/"
    echo "✓ Borealis-12b lastet opp"
  fi
fi

echo ""
echo "━━━ Ferdig ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
gcloud storage ls --recursive "gs://${BUCKET}/" | grep -E "^gs://"
