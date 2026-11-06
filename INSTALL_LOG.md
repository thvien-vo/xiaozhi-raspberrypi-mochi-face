# Pi 5 install log

Everything added or changed outside `~/Xiaozhi_RaspberryPi` on the Pi (user `admin`),
with how to undo it. No file under `/boot`, `/etc`, or `/usr` was touched, nothing was
installed with apt, and the Pi was not rebooted. `sudo` needs a password on that Pi, so
nothing ran as root.

| What | Where | Undo |
| --- | --- | --- |
| Python 3.12 and uv cache | `~/.local/share/uv`, `~/.cache/uv` | `uv python uninstall 3.12; uv cache clean` |
| Virtual environment | `~/Xiaozhi_RaspberryPi/.venv` | `rm -rf ~/Xiaozhi_RaspberryPi/.venv` |
| Device identity and settings (mode 600), copied from the laptop | `~/.local/share/py-xiaozhi/config/{config.json,efuse.json}` | `rm -r ~/.local/share/py-xiaozhi` |
| App logs, limited to about 40 MB | `~/.local/share/py-xiaozhi/logs/` | same as above |
| YOLO models | `~/Xiaozhi_RaspberryPi/models/yolo/` | `rm -r` it |
| fontconfig and freetype `.deb` files, unpacked with `dpkg -x` | `~/.local/debs`, `~/.local/xiaozhi-libs` | `rm -rf ~/.local/debs ~/.local/xiaozhi-libs` |
| systemd user services, enabled | `~/.config/systemd/user/{xiaozhi,jabra-autopair}.service` | `systemctl --user disable --now xiaozhi.service jabra-autopair.service; rm ~/.config/systemd/user/{xiaozhi,jabra-autopair}.service` |
| Desktop autostart entry (only used if a desktop is installed later) | `~/.config/autostart/xiaozhi-autostart.desktop` | `rm` it |
| Audio choice (written at each start) | `~/.config/alsa/asoundrc`, `~/.local/state/xiaozhi/audio-selected` | `rm -r ~/.config/alsa/asoundrc ~/.local/state/xiaozhi` |
| Temporary test logs | `/tmp/offscreen_app.log`, `/tmp/fd*.log`, `~/uvsync.log` | `rm` them |

Already present before this work and left alone: lingering for `admin`
(`loginctl enable-linger`), which is what lets the user services start at boot.

The laptop and the Pi use the same device identity. Do not run both at once.
