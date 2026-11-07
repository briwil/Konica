#!/usr/bin/env bash
# Run on your computer: copy this repo to the Pi over SSH and restart the service.
#
#   ./deploy.sh              # sync code + restart
#   ./deploy.sh --install    # first time: sync + run deploy/install.sh on the Pi
#   ./deploy.sh --logs       # sync + restart + follow the log
#
# Override the target with KONICA_HOST / KONICA_USER / KONICA_DIR.
set -euo pipefail

HOST="${KONICA_HOST:-konica.local}"
USERNAME="${KONICA_USER:-pi}"
DEST="${KONICA_DIR:-konica-back}"
TARGET="$USERNAME@$HOST"

cd "$(dirname "$0")"
echo "==> Syncing to $TARGET:~/$DEST"
# config.toml and .venv on the Pi are left alone.
rsync -az --delete \
  --exclude '.git/' --exclude '.venv/' --exclude 'config.toml' \
  --exclude '__pycache__/' --exclude '*.pyc' \
  ./ "$TARGET:$DEST/"

if [ "${1:-}" = "--install" ]; then
  ssh -t "$TARGET" "cd $DEST && ./deploy/install.sh"
fi

echo "==> Restarting service"
ssh "$TARGET" "sudo systemctl restart konica-back"
if [ "${1:-}" = "--logs" ]; then
  ssh -t "$TARGET" "journalctl -u konica-back -f -n 30"
else
  sleep 3
  ssh "$TARGET" "journalctl -u konica-back -n 20 --no-pager"
fi
