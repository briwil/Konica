#!/usr/bin/env bash
# One-time setup, run ON THE PI (deploy.sh --install does this for you).
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user (it uses sudo where needed)." >&2
  exit 1
fi

DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  python3-picamera2 python3-gpiozero python3-lgpio python3-venv python3-pil \
  python3-numpy rsync

echo "==> Python environment"
python3 -m venv --system-site-packages "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade pillow-heif \
  || echo "!! pillow-heif didn't install; HEIF output will be skipped"

if [ ! -f "$DIR/config.toml" ]; then
  cp "$DIR/config.example.toml" "$DIR/config.toml"
  echo "==> Created config.toml"
fi
mkdir -p "$HOME/DCIM"

echo "==> systemd service"
sed "s|__USER__|$USER|g; s|__DIR__|$DIR|g" "$DIR/deploy/konica-back.service" \
  | sudo tee /etc/systemd/system/konica-back.service >/dev/null
sed "s|__USER__|$USER|g; s|__DIR__|$DIR|g" "$DIR/deploy/konica-update.service" \
  | sudo tee /etc/systemd/system/konica-update.service >/dev/null
chmod +x "$DIR/deploy/auto-update.sh"
sudo systemctl daemon-reload
sudo systemctl enable konica-back konica-update
if [ ! -d "$DIR/.git" ]; then
  echo "   (Not a git clone, so updates from GitHub at boot are off."
  echo "    Clone the repo onto the Pi to turn them on.)"
fi

echo "==> Checks"
BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
if ! grep -Eq '^\s*dtoverlay=imx477' "$BOOTCFG"; then
  echo "!! No 'dtoverlay=imx477' in $BOOTCFG. If the camera isn't detected, add:"
  echo "     camera_auto_detect=0"
  echo "     dtoverlay=imx477"
fi
CMA_MB=$(awk '/CmaTotal/ {print int($2/1024)}' /proc/meminfo)
echo "   CMA memory: ${CMA_MB} MB"
if [ "${CMA_MB:-0}" -lt 160 ]; then
  echo "!! Full-resolution capture needs more CMA. In $BOOTCFG change the line"
  echo "     dtoverlay=vc4-kms-v3d   to   dtoverlay=vc4-kms-v3d,cma-192"
  echo "   then reboot."
fi

echo
echo "Done. Next:"
echo "  .venv/bin/python -m konicaback info       # camera detected?"
echo "  .venv/bin/python -m konicaback buttons    # which GPIOs are the buttons?"
echo "  sudo systemctl start konica-back          # run it"
