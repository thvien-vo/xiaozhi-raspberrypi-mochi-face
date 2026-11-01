# Raspberry Pi 5 deployment

For a Raspberry Pi 5 running Raspberry Pi OS 64-bit, with a USB webcam, a Jabra USB
speakerphone and a Waveshare 7-inch HDMI display. All vision runs on the CPU. Speech
recognition, the LLM and speech synthesis run on the remote Xiaozhi server.

After setup, work through [HARDWARE_CHECKLIST.md](HARDWARE_CHECKLIST.md).

## 1. Check the OS version

```bash
grep VERSION_CODENAME /etc/os-release
ldd --version | head -1
```

Use **Trixie** (glibc 2.41). The ARM wheels of PySide6 6.10 and later need glibc 2.39, so
they do not install on Bookworm (glibc 2.36). On Bookworm, either upgrade the OS or edit
`gui` in `pyproject.toml` to `PySide6>=6.8,<6.9`, then run `uv lock`. 6.8.0.2 is the
newest version with a suitable ARM wheel. That combination has not been tested.

## 2. Install system packages

```bash
sudo apt update
sudo apt install -y git curl libportaudio2 libasound2-dev alsa-utils v4l-utils \
    libegl1 libgl1 libxkbcommon0 libxcb-cursor0 libdbus-1-3 fonts-noto-core fonts-noto-cjk
```

The Noto fonts matter: without them, text in Vietnamese or Chinese may show as empty
boxes. Opus does not need a package, `libs/libopus/linux/arm64/libopus.so` is bundled.

## 3. Install Python 3.12 and the app

Use uv's Python 3.12, not the system Python 3.13.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env

git clone --branch robot-face https://github.com/thvien-vo/Xiaozhi_RaspberryPi.git ~/py-xiaozhi
cd ~/py-xiaozhi

uv python install 3.12
uv sync --python 3.12 --no-dev --extra gui --extra vision
```

The `vision` extra installs ncnn and onnxruntime only, not PyTorch or Ultralytics. Check
that the environment is right:

```bash
.venv/bin/python --version
.venv/bin/python -c "import PySide6, cv2, ncnn, onnxruntime; print('OK', PySide6.__version__)"
```

## 4. Get the YOLO model

Models are not in the repository (see [DECISIONS.md](DECISIONS.md)). Export them once on
the laptop, in a separate environment so PyTorch stays out of the app:

```powershell
python -m venv .venv-export
.venv-export\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv-export\Scripts\pip install ultralytics onnx onnxslim pnnx ncnn
.venv-export\Scripts\python scripts\export_yolo.py
```

Copy the result to the Pi, replacing the host name with yours:

```powershell
ssh pi@raspberrypi.local "mkdir -p ~/py-xiaozhi/models"
scp -r models\yolo pi@raspberrypi.local:~/py-xiaozhi/models/
```

If a model is missing, the app still runs: the face works, the preview stays hidden, and
`detect_objects` tells the assistant what is wrong.

## 5. Apply the Pi settings

```bash
.venv/bin/python scripts/apply_config_preset.py deploy/raspberry-pi/config.pi5.json --dry-run
.venv/bin/python scripts/apply_config_preset.py deploy/raspberry-pi/config.pi5.json
```

This merges `deploy/raspberry-pi/config.pi5.json` into
`~/.local/share/py-xiaozhi/config/config.json`: kiosk fullscreen, wake word
`NUM_THREADS` 2, vision threads 2, software AEC off, Jabra as the audio device. Other
settings, including the device identity, stay as they are.

## 6. Select the Jabra

Plug it in and list the devices:

```bash
arecord -l
aplay -l
.venv/bin/python scripts/py_audio_scanner.py
```

The preset sets `AUDIO_DEVICES.input_device_name` and `output_device_name` to `Jabra`,
which matches any device with that word in its name. Keep the short name rather than the
full one: the full name contains the ALSA card number (such as `hw:2,0`), which can change
between boots. If the name is not found at startup the app logs a warning and uses the
system default device for that run, then looks for it again on the next start.

`AEC_OPTIONS.ENABLED` stays `false` because the Jabra cancels echo in hardware.

Test it:

```bash
speaker-test -t wav -c 2 -l 1
arecord -d 3 -f S16_LE -r 16000 /tmp/mic.wav && aplay /tmp/mic.wav
```

If PipeWire holds the device, make the Jabra the default in the desktop sound settings,
or with `wpctl status` and `wpctl set-default <id>`, and set both names to `default`.

## 7. Select the camera

```bash
v4l2-ctl --list-devices
.venv/bin/python scripts/camera_scanner.py
.venv/bin/python scripts/camera_scanner.py --select 0 --test
```

`/dev/video*` numbers can change when USB devices are added. If that happens, set
`CAMERA.device` to the stable path from `ls /dev/v4l/by-id/`, ending in `-index0`:

```bash
.venv/bin/python scripts/camera_scanner.py --device /dev/v4l/by-id/usb-YOUR_CAMERA-video-index0
```

If the image is mirrored, set `CAMERA.flip` to `horizontal` (or `vertical`, or `both`
for an upside-down camera). The default is `none`, and the Pi preset leaves it alone
because the camera may be mounted differently. The flip is applied once where frames are
read, so the preview, detection and `take_photo` all show the corrected image, and "left"
and "right" in `detect_objects` follow it. Use `main.py --face-demo --demo-camera real` to
check, and hold up something with writing on it.

## 8. Set the screen resolution

The kiosk window fills whatever resolution the screen reports, and the face scales to fit.
To force 1024x600, add this to the single line in `/boot/firmware/cmdline.txt`, then reboot:

```text
video=HDMI-A-1:1024x600@60D
```

Use `HDMI-A-2` if the screen is on the second port. For another panel, change
`FACE.WINDOW_WIDTH` and `FACE.WINDOW_HEIGHT` as well as this line. Check the result with
`wlr-randr`.

## 9. First run

Activate the device from a terminal on the Pi's desktop, before enabling autostart:

```bash
cd ~/py-xiaozhi
.venv/bin/python main.py --mode gui
```

Esc quits. Check the face without a server, then measure vision performance:

```bash
.venv/bin/python main.py --face-demo --demo-camera real
.venv/bin/python scripts/benchmark_vision.py
.venv/bin/python scripts/benchmark_vision.py --backend onnxruntime
```

If the second benchmark is faster, set `VISION.BACKEND` to `onnxruntime`.

## 10. Start on boot

Log in to the desktop automatically, then install the service:

```bash
sudo raspi-config nonint do_boot_behaviour B4
bash deploy/raspberry-pi/install-autostart.sh
sudo reboot
```

The desktop session starts `xiaozhi.service`, which restarts the app after a crash. Pressing
Esc is a deliberate exit, so it stays closed until the next boot or until you start it:

```bash
systemctl --user start xiaozhi.service
systemctl --user stop xiaozhi.service
journalctl --user -u xiaozhi.service -f
```

Application logs are in `~/.local/share/py-xiaozhi/logs/`.

## Tuning

| If | Change |
| --- | --- |
| Audio stutters or the face drops frames | Lower `VISION.INFERENCE_RATE`, then `VISION.PREVIEW.FPS` |
| Detections miss small objects | `VISION.INPUT_SIZE` 640, which uses about 4 times the CPU |
| Too many false detections | Raise `VISION.CONFIDENCE` |
| The wake word is unreliable | Check `WAKE_WORD_OPTIONS.MODEL_PATH` and `WAKE_WORD` for your language |
| The preview covers something | `VISION.PREVIEW.CORNER`, `WIDTH` or `ENABLED` |
| You want a frame rate readout | `FACE.SHOW_FPS` true |
| You want the old emoji face | `FACE.STYLE` `gif` |

Changes to `FACE.*` and `VISION.*` take effect after a restart. `CAMERA.*` changes made in
Settings apply straight away.
