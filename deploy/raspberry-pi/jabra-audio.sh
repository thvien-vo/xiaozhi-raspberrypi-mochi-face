#!/usr/bin/env bash
# Make the Jabra the default audio device before the app starts.
#
# Priority: 1) USB audio whose name contains "Jabra"   2) a connected Bluetooth Jabra.
# Waits up to JABRA_WAIT_S seconds (default 120) for a device, then exits 0 anyway so the
# app still starts. The result is written to ~/.local/state/xiaozhi/audio-selected.
#
# What it changes (all inside the user's home, nothing needs root):
#   ~/.config/alsa/asoundrc    ALSA default device -> the Jabra card (USB case)
#   PipeWire/WirePlumber       wpctl set-default, only if wpctl is installed
set -u

WAIT_S="${JABRA_WAIT_S:-120}"
BT_GRACE_S="${JABRA_BT_GRACE_S:-20}"   # give USB this long before falling back to Bluetooth
PATTERN="${JABRA_PATTERN:-jabra}"
STATE_DIR="$HOME/.local/state/xiaozhi"
ALSA_RC="$HOME/.config/alsa/asoundrc"
mkdir -p "$STATE_DIR" "$(dirname "$ALSA_RC")"

log() { echo "jabra-audio: $*" >&2; }
record() { echo "$1" > "$STATE_DIR/audio-selected"; log "selected: $1"; }

# Card id (the short name ALSA uses) of a USB card matching PATTERN, or nothing.
find_usb_card() {
    local line id usb
    while read -r line; do
        id="$(echo "$line" | sed -nE 's/^ *[0-9]+ \[([^] ]+) *\].*/\1/p')"
        [ -n "$id" ] || continue
        if echo "$line" | grep -qi "$PATTERN"; then
            echo "$id"; return 0
        fi
    done < <(grep -E '^ *[0-9]+ \[' /proc/asound/cards 2>/dev/null)
    return 1
}

# Name of a connected Bluetooth device matching PATTERN, or nothing.
find_bt_connected() {
    command -v bluetoothctl >/dev/null 2>&1 || return 1
    local mac
    while read -r _ mac _; do
        if timeout 5 bluetoothctl info "$mac" 2>/dev/null | grep -qi 'Connected: yes'; then
            echo "$mac"; return 0
        fi
    done < <(timeout 5 bluetoothctl devices 2>/dev/null | grep -i "$PATTERN")
    return 1
}

use_usb() {
    local card="$1"
    cat > "$ALSA_RC" <<RC
# Written by deploy/raspberry-pi/jabra-audio.sh. Remove this file to go back to defaults.
pcm.!default {
    type asym
    playback.pcm "plughw:CARD=$card"
    capture.pcm "plughw:CARD=$card"
}
ctl.!default {
    type hw
    card $card
}
RC
    if command -v wpctl >/dev/null 2>&1; then
        local id
        id="$(wpctl status 2>/dev/null | grep -i "$PATTERN" | grep -oE '[0-9]+\.' | head -n1 | tr -d .)"
        [ -n "$id" ] && wpctl set-default "$id" 2>/dev/null
    fi
    record "USB card=$card"
}

use_bluetooth() {
    local mac="$1"
    if command -v wpctl >/dev/null 2>&1; then
        local id
        id="$(wpctl status 2>/dev/null | grep -i "$PATTERN" | grep -oE '[0-9]+\.' | head -n1 | tr -d .)"
        if [ -n "$id" ]; then
            wpctl set-default "$id" 2>/dev/null
            record "Bluetooth $mac (via PipeWire)"
            return 0
        fi
    fi
    log "Bluetooth Jabra $mac is connected but there is no PipeWire/PulseAudio to carry audio"
    record "Bluetooth $mac connected, NO audio server: unusable"
    return 1
}

start=$SECONDS
while :; do
    elapsed=$((SECONDS - start))
    if card="$(find_usb_card)" && [ -n "$card" ]; then
        use_usb "$card"; exit 0
    fi
    if [ "$elapsed" -ge "$BT_GRACE_S" ]; then
        if mac="$(find_bt_connected)" && [ -n "$mac" ]; then
            use_bluetooth "$mac" && exit 0
        fi
    fi
    if [ "$elapsed" -ge "$WAIT_S" ]; then
        record "none (no Jabra after ${WAIT_S}s; app falls back to the system default)"
        exit 0
    fi
    sleep 2
done
