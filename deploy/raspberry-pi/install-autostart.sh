#!/usr/bin/env bash
# Install and enable the systemd user services for this checkout.
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

chmod +x "$SCRIPT_DIR"/*.sh
bash "$SCRIPT_DIR/install-user-libs.sh"

SERVICE_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$SERVICE_DIR" "$AUTOSTART_DIR"

for unit in xiaozhi.service jabra-autopair.service; do
    sed "s|@APP_DIR@|$APP_DIR|g" "$SCRIPT_DIR/$unit" > "$SERVICE_DIR/$unit"
done
cp "$SCRIPT_DIR/xiaozhi-autostart.desktop" "$AUTOSTART_DIR/xiaozhi-autostart.desktop"

systemctl --user daemon-reload
systemctl --user enable xiaozhi.service jabra-autopair.service

# Lingering lets the user manager (and these services) start at boot without a login.
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]; then
    echo "Lingering is off. Enable it once with: sudo loginctl enable-linger $USER" >&2
fi

echo "Installed and enabled. They start at the next boot."
echo "Start now:    systemctl --user start xiaozhi.service"
echo "Follow logs:  journalctl --user -u xiaozhi.service -f   (and ~/.local/share/py-xiaozhi/logs/)"
echo "Remove:       systemctl --user disable --now xiaozhi.service jabra-autopair.service"
echo "              rm $SERVICE_DIR/xiaozhi.service $SERVICE_DIR/jabra-autopair.service $AUTOSTART_DIR/xiaozhi-autostart.desktop"
