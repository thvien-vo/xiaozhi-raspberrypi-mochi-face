#!/usr/bin/env bash
# Install the systemd user service and the desktop autostart entry for this checkout.
# Run as the desktop user, not with sudo:  bash deploy/raspberry-pi/install-autostart.sh
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as the desktop user, without sudo." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    echo "No virtual environment at $APP_DIR/.venv. Run uv sync first." >&2
    exit 1
fi

SERVICE_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$SERVICE_DIR" "$AUTOSTART_DIR"

sed "s|@APP_DIR@|$APP_DIR|g" "$SCRIPT_DIR/xiaozhi.service" > "$SERVICE_DIR/xiaozhi.service"
cp "$SCRIPT_DIR/xiaozhi-autostart.desktop" "$AUTOSTART_DIR/xiaozhi-autostart.desktop"

systemctl --user daemon-reload

echo "Installed. It starts at the next desktop login."
echo "Start it now:   systemctl --user start xiaozhi.service"
echo "Follow logs:    journalctl --user -u xiaozhi.service -f"
echo "Remove it:      rm $SERVICE_DIR/xiaozhi.service $AUTOSTART_DIR/xiaozhi-autostart.desktop"
