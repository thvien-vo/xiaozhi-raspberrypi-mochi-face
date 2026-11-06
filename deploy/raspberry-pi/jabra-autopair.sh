#!/usr/bin/env bash
# Scan for, pair, trust and connect a Bluetooth device whose name contains "Jabra".
# Runs forever as the user service jabra-autopair.service. Uses bluetoothctl only.
# Note: audio over Bluetooth also needs PipeWire (or PulseAudio); see docs/raspberry-pi-5.md.
set -u
PATTERN="${JABRA_PATTERN:-jabra}"
INTERVAL_S="${JABRA_PAIR_INTERVAL_S:-30}"
SCAN_S="${JABRA_SCAN_S:-12}"

log() { echo "jabra-autopair: $*" >&2; }
bt() { timeout "${BT_TIMEOUT_S:-20}" bluetoothctl "$@" 2>/dev/null; }

command -v bluetoothctl >/dev/null 2>&1 || { log "bluetoothctl not found"; exit 1; }

bt power on >/dev/null
bt pairable on >/dev/null
bt agent NoInputNoOutput >/dev/null
bt default-agent >/dev/null

while :; do
    # A USB Jabra makes pairing pointless, but it is harmless: skip scanning while it is there.
    if grep -qi "$PATTERN" /proc/asound/cards 2>/dev/null && [ "${JABRA_PAIR_EVEN_WITH_USB:-0}" != 1 ]; then
        sleep "$INTERVAL_S"; continue
    fi

    # Known devices first, then a short scan to find new ones.
    mac="$(bt devices | grep -i "$PATTERN" | awk '{print $2}' | head -n1)"
    if [ -z "$mac" ]; then
        timeout "$SCAN_S" bluetoothctl --timeout "$SCAN_S" scan on >/dev/null 2>&1
        mac="$(bt devices | grep -i "$PATTERN" | awk '{print $2}' | head -n1)"
    fi

    if [ -n "$mac" ]; then
        info="$(bt info "$mac")"
        echo "$info" | grep -q 'Connected: yes' && { sleep "$INTERVAL_S"; continue; }
        echo "$info" | grep -q 'Paired: yes' || { log "pairing $mac"; bt pair "$mac" >/dev/null; }
        echo "$info" | grep -q 'Trusted: yes' || bt trust "$mac" >/dev/null
        log "connecting $mac"
        bt connect "$mac" >/dev/null
    fi
    sleep "$INTERVAL_S"
done
