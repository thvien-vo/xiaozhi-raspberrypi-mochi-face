#!/usr/bin/env bash
# Make the Jabra the default audio device before the app starts.
#
# Priority: 1) USB audio whose name contains "Jabra"   2) a connected Bluetooth Jabra.
# Nothing else is ever chosen: not another USB card (a webcam's microphone, HDMI), not a card
# number, not a generic id such as "USB". If there is no Jabra the script says so and removes
# the ALSA default it wrote earlier, so a stale choice cannot point at the wrong card.
# Waits up to JABRA_WAIT_S seconds (default 120) for a device, then exits 0 anyway so the
# app still starts. The result is written to ~/.local/state/xiaozhi/audio-selected.
#
# What it changes (all inside the user's home, nothing needs root):
#   ~/.config/alsa/asoundrc    ALSA default device -> the Jabra card (USB case)
#   Jabra mixer                speaker and microphone levels (JABRA_SPK_PCT / JABRA_MIC_PCT)
#   PipeWire/WirePlumber       wpctl set-default, only if wpctl is installed
set -u

WAIT_S="${JABRA_WAIT_S:-120}"
BT_GRACE_S="${JABRA_BT_GRACE_S:-20}"   # give USB this long before falling back to Bluetooth
PATTERN="${JABRA_PATTERN:-jabra}"
SPK_PCT="${JABRA_SPK_PCT:-75}"         # speaker volume set at every start
MIC_PCT="${JABRA_MIC_PCT:-70}"         # microphone gain set at every start
PROC_ASOUND="${JABRA_PROC_ASOUND:-/proc/asound}"   # overridable for tests
STATE_DIR="${JABRA_STATE_DIR:-$HOME/.local/state/xiaozhi}"
ALSA_RC="${JABRA_ALSA_RC:-$HOME/.config/alsa/asoundrc}"
mkdir -p "$STATE_DIR" "$(dirname "$ALSA_RC")"

log() { echo "jabra-audio: $*" >&2; }
record() { echo "$1" > "$STATE_DIR/audio-selected"; log "selected: $1"; }

# Prints "<index> <id>" of the USB card whose name matches PATTERN and that can both play and
# record, or nothing. Matching is on the card's long name ("Jabra SPEAK 510 USB"), never on
# the short id, which many USB devices share.
find_usb_card() {
    local line idx id
    while read -r line; do
        idx="$(echo "$line" | sed -nE 's/^ *([0-9]+) \[.*/\1/p')"
        id="$(echo "$line" | sed -nE 's/^ *[0-9]+ \[([^] ]+) *\].*/\1/p')"
        [ -n "$idx" ] && [ -n "$id" ] || continue
        echo "$line" | grep -qi "$PATTERN" || continue
        # Needs a playback and a capture stream (a webcam or HDMI card would not have both)
        ls "$PROC_ASOUND/card$idx"/pcm*p >/dev/null 2>&1 || continue
        ls "$PROC_ASOUND/card$idx"/pcm*c >/dev/null 2>&1 || continue
        echo "$idx $id"; return 0
    done < <(grep -E '^ *[0-9]+ \[' "$PROC_ASOUND/cards" 2>/dev/null)
    return 1
}

# The ALSA name for a card: its id when no other card has the same id, else its index.
alsa_name() {
    local idx="$1" id="$2" same
    same="$(cat "$PROC_ASOUND"/card*/id 2>/dev/null | grep -cx -- "$id")"
    if [ "$same" = 1 ]; then echo "CARD=$id"; else echo "$idx"; fi
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

# Set the Jabra's speaker and microphone to known levels and log what they ended up as.
set_levels() {
    local idx="$1" ctl caps
    command -v amixer >/dev/null 2>&1 || { log "amixer not installed, mixer levels left as they are"; return 0; }
    while read -r ctl; do
        [ -n "$ctl" ] || continue
        caps="$(amixer -c "$idx" sget "$ctl" 2>/dev/null)"
        if echo "$caps" | grep -q 'Capabilities:.*pvolume'; then
            amixer -c "$idx" -q sset "$ctl" "${SPK_PCT}%" unmute 2>/dev/null
            log "speaker '$ctl' -> $(amixer -c "$idx" sget "$ctl" 2>/dev/null | grep -oE '\[[0-9]+%\]' | head -n1) (wanted ${SPK_PCT}%)"
        elif echo "$caps" | grep -q 'Capabilities:.*cvolume'; then
            amixer -c "$idx" -q sset "$ctl" "${MIC_PCT}%" cap 2>/dev/null
            log "microphone '$ctl' -> $(amixer -c "$idx" sget "$ctl" 2>/dev/null | grep -oE '\[[0-9]+%\]' | head -n1) (wanted ${MIC_PCT}%)"
        fi
    done < <(amixer -c "$idx" scontrols 2>/dev/null | sed -nE "s/^Simple mixer control '(.*)',[0-9]+$/\1/p")
}

use_usb() {
    local idx="$1" id="$2" name
    name="$(alsa_name "$idx" "$id")"
    cat > "$ALSA_RC" <<RC
# Written by deploy/raspberry-pi/jabra-audio.sh. Remove this file to go back to defaults.
pcm.!default {
    type asym
    playback.pcm "plughw:$name"
    capture.pcm "plughw:$name"
}
ctl.!default {
    type hw
    card $idx
}
RC
    set_levels "$idx"
    if command -v wpctl >/dev/null 2>&1; then
        local wid
        wid="$(wpctl status 2>/dev/null | grep -i "$PATTERN" | grep -oE '[0-9]+\.' | head -n1 | tr -d .)"
        [ -n "$wid" ] && wpctl set-default "$wid" 2>/dev/null
    fi
    record "USB card=$id index=$idx alsa=plughw:$name (microphone and speaker)"
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
last_note=-10
while :; do
    elapsed=$((SECONDS - start))
    if found="$(find_usb_card)" && [ -n "$found" ]; then
        # shellcheck disable=SC2086
        use_usb $found; exit 0
    fi
    if [ "$elapsed" -ge "$BT_GRACE_S" ]; then
        if mac="$(find_bt_connected)" && [ -n "$mac" ]; then
            use_bluetooth "$mac" && exit 0
        fi
    fi
    if [ "$elapsed" -ge "$WAIT_S" ]; then
        rm -f "$ALSA_RC"   # a stale choice could now point at another card
        log "ERROR: no Jabra audio device after ${WAIT_S}s. Nothing else is used: the app will keep retrying"
        record "none (no Jabra after ${WAIT_S}s; no other device is used)"
        exit 0
    fi
    if [ $((elapsed - last_note)) -ge 10 ]; then
        last_note=$elapsed
        log "waiting for a Jabra audio device (${elapsed}s)"
    fi
    sleep 2
done
