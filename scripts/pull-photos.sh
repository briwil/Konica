#!/usr/bin/env bash
# Run on your computer: copy new photos from the Pi into ./photos (or $1).
set -euo pipefail
HOST="${KONICA_HOST:-konica.local}"
USERNAME="${KONICA_USER:-pi}"
OUT="${1:-photos}"
mkdir -p "$OUT"
rsync -av --exclude '.counter' --exclude '*.part' "$USERNAME@$HOST:DCIM/" "$OUT/"
