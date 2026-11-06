#!/usr/bin/env bash
# Qt needs libfontconfig1 and libfreetype6. If they are missing and you cannot use sudo,
# download the .deb files and unpack them under ~/.local/xiaozhi-libs. Nothing is installed
# system-wide. run-xiaozhi.sh picks the directory up automatically.
set -euo pipefail
if ldconfig -p | grep -q 'libfontconfig.so.1' && ldconfig -p | grep -q 'libfreetype.so.6'; then
    echo "System libraries present, nothing to do."; exit 0
fi
DEST="$HOME/.local/xiaozhi-libs"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cd "$TMP"
apt-get download libfontconfig1 libfreetype6 fontconfig-config
for deb in ./*.deb; do dpkg -x "$deb" "$DEST"; done
echo "Unpacked into $DEST. Remove it with: rm -rf $DEST"
