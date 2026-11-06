#!/usr/bin/env bash
# Launcher used by xiaozhi.service: wait for an HDMI display, pick the Qt backend, start the app.
#
#   XIAOZHI_QPA          force a Qt platform (for example "offscreen" for tests)
#   XIAOZHI_SKIP_DISPLAY_WAIT=1   do not wait for a display
set -u
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$APP_DIR" || exit 1

log() { echo "run-xiaozhi: $*" >&2; }

# The app rewrites config.json on every save, which can loosen its mode. It holds tokens.
chmod 600 "$HOME"/.local/share/py-xiaozhi/config/config.json "$HOME"/.local/share/py-xiaozhi/config/efuse.json 2>/dev/null

# Libraries installed by install-user-libs.sh when the system lacks them (no root needed)
LIBS="$HOME/.local/xiaozhi-libs"
if [ -d "$LIBS/usr/lib/aarch64-linux-gnu" ]; then
    export LD_LIBRARY_PATH="$LIBS/usr/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    [ -f "$LIBS/etc/fonts/fonts.conf" ] && [ ! -f /etc/fonts/fonts.conf ] && export FONTCONFIG_FILE="$LIBS/etc/fonts/fonts.conf"
fi

display_connected() {
    local s
    for s in /sys/class/drm/card*-HDMI-A-*/status; do
        [ -r "$s" ] && [ "$(cat "$s")" = connected ] && return 0
    done
    return 1
}

if [ "${XIAOZHI_SKIP_DISPLAY_WAIT:-0}" != 1 ] && [ -z "${XIAOZHI_QPA:-}" ]; then
    waited=0
    until display_connected; do
        [ "$waited" -eq 0 ] && log "waiting for an HDMI display"
        sleep 3; waited=$((waited + 3))
    done
    [ "$waited" -gt 0 ] && log "display found after ${waited}s"
    sleep 2   # let the kernel finish setting the mode and creating /dev/fb0
fi

# Platform: a Wayland desktop if there is one, otherwise straight onto the framebuffer
# (Raspberry Pi OS Lite has no compositor, and the PySide6 wheel has no KMS/eglfs support).
if [ -n "${XIAOZHI_QPA:-}" ]; then
    export QT_QPA_PLATFORM="$XIAOZHI_QPA"
elif [ -n "${WAYLAND_DISPLAY:-}" ] || ls "${XDG_RUNTIME_DIR:-/nonexistent}"/wayland-* >/dev/null 2>&1; then
    export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
else
    export QT_QPA_PLATFORM="linuxfb:fb=/dev/fb0"
    export QT_QUICK_BACKEND=software    # linuxfb has no OpenGL
    export QT_QPA_FB_HIDECURSOR=1
fi
log "Qt platform: ${QT_QPA_PLATFORM:-wayland}"

exec "$APP_DIR/.venv/bin/python" main.py --mode gui
