"""Jabra selection (script + app) and camera isolation for the Pi kiosk."""

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from src.utils import audio_device
from src.utils.audio_device import AudioDeviceManager
from src.vision.camera_service import CameraService

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "raspberry-pi" / "jabra-audio.sh"

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


# ---------- jabra-audio.sh ----------


def fake_asound(root: Path, cards: list[tuple[int, str, str, str]]) -> Path:
    """cards: (index, id, long name, 'pc' for play+capture / 'c' / 'p')."""
    root.mkdir(parents=True, exist_ok=True)
    lines = []
    for idx, cid, name, caps in cards:
        lines.append(f" {idx} [{cid:<14}]: USB-Audio - {name}\n")
        lines.append(f"                      {name} at usb-xhci-hcd.0-2, full speed\n")
        card = root / f"card{idx}"
        card.mkdir()
        (card / "id").write_text(cid + "\n")
        if "p" in caps:
            (card / "pcm0p").mkdir()
        if "c" in caps:
            (card / "pcm0c").mkdir()
    (root / "cards").write_text("".join(lines))
    return root


def run_script(tmp_path: Path, cards, **env):
    asound = fake_asound(tmp_path / "asound", cards)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "amixer.log"
    amixer = bin_dir / "amixer"
    # Jabra-like controls: PCM playback and a capture-only "Headset"
    amixer.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log.as_posix()}"\n'
        'card=""; [ "$1" = -c ] && { card="$2"; shift 2; }\n'
        'case "$1" in\n'
        "  scontrols) echo \"Simple mixer control 'PCM',0\"; echo \"Simple mixer control 'Headset',0\";;\n"
        "  sget) case \"$2\" in\n"
        "    PCM) echo '  Capabilities: pvolume pvolume-joined pswitch'; echo '  Mono: Playback 8 [75%] [on]';;\n"
        "    Headset) echo '  Capabilities: cvolume cvolume-joined cswitch'; echo '  Mono: Capture 5 [70%] [on]';;\n"
        "  esac;;\n"
        "esac\n"
    )
    amixer.chmod(0o755)
    state = tmp_path / "state"
    rc = tmp_path / "asoundrc"
    rc.write_text("pcm.!default { stale }\n")  # What an earlier run left behind
    full_env = {
        **os.environ,
        "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}",
        "JABRA_PROC_ASOUND": asound.as_posix(),
        "JABRA_STATE_DIR": state.as_posix(),
        "JABRA_ALSA_RC": rc.as_posix(),
        "JABRA_WAIT_S": "0",
        "JABRA_BT_GRACE_S": "9999",
        **env,
    }
    result = subprocess.run(
        [BASH, SCRIPT.as_posix()], capture_output=True, text=True, env=full_env, timeout=60
    )
    selected = (state / "audio-selected").read_text().strip()
    amixer_log = log.read_text() if log.exists() else ""
    return result, selected, rc, amixer_log


WEBCAM = (0, "U2K", "UGREEN Camera 2K", "c")
JABRA = (1, "USB", "Jabra SPEAK 510 USB", "pc")
HDMI = (2, "vc4hdmi0", "vc4-hdmi-0", "p")


@needs_bash
def test_selects_the_jabra_not_the_webcam_microphone(tmp_path):
    result, selected, rc, amixer = run_script(tmp_path, [WEBCAM, JABRA, HDMI])
    assert "index=1" in selected and "card=USB" in selected
    text = rc.read_text()
    assert 'plughw:CARD=USB"' in text and "card 1" in text
    assert "U2K" not in text and "card 0" not in text
    # Levels were set on the Jabra only, never on the webcam card
    assert "-c 0" not in amixer
    assert "-c 1" in amixer
    assert "75%" in amixer and "70%" in amixer
    assert "speaker 'PCM'" in result.stderr and "microphone 'Headset'" in result.stderr


@needs_bash
def test_jabra_found_by_name_whatever_the_card_number(tmp_path):
    jabra_first = (0, "USB", "Jabra SPEAK 510 USB", "pc")
    webcam_second = (1, "U2K", "UGREEN Camera 2K", "c")
    _, selected, rc, _ = run_script(tmp_path, [jabra_first, webcam_second])
    assert "index=0" in selected
    assert "card 0" in rc.read_text()


@needs_bash
def test_generic_id_shared_with_the_webcam_falls_back_to_the_card_index(tmp_path):
    # Both cards call themselves "USB": the id is ambiguous, so the index must be used
    webcam = (0, "USB", "UGREEN Camera 2K", "c")
    jabra = (1, "USB", "Jabra SPEAK 510 USB", "pc")
    _, selected, rc, _ = run_script(tmp_path, [webcam, jabra])
    assert "plughw:1" in selected
    assert "CARD=USB" not in rc.read_text()


@needs_bash
def test_no_jabra_means_no_device_and_a_clear_log_line(tmp_path):
    result, selected, rc, amixer = run_script(tmp_path, [WEBCAM, HDMI])
    assert selected.startswith("none")
    assert "ERROR: no Jabra" in result.stderr
    assert not rc.exists()  # The stale default is removed, nothing else is selected
    assert amixer == ""  # No mixer was touched
    assert result.returncode == 0  # The app must still start


@needs_bash
def test_a_jabra_name_without_playback_and_capture_is_not_chosen(tmp_path):
    capture_only = (1, "USB", "Jabra SPEAK 510 USB", "c")
    _, selected, _, _ = run_script(tmp_path, [WEBCAM, capture_only])
    assert selected.startswith("none")


# ---------- app side ----------


class StubConfig:
    def __init__(self, audio):
        self.audio = audio

    def get_config(self, path, default=None):
        return self.audio if path == "AUDIO_DEVICES" else default

    def update_config(self, path, value):
        return True


def info(name):
    return {"index": 3, "name": name, "sample_rate": 48000, "channels": 1}


def test_strict_names_never_fall_back_to_another_device(monkeypatch):
    # The Jabra is missing and a webcam microphone is the system default
    monkeypatch.setattr(audio_device, "find_device_by_name", lambda kind, name: None)
    monkeypatch.setattr(
        audio_device, "select_audio_device", lambda kind: info("UGREEN Camera 2K: USB Audio")
    )
    config = StubConfig(
        {"input_device_name": "Jabra", "output_device_name": "Jabra", "strict_names": True}
    )
    with pytest.raises(RuntimeError, match="Jabra"):
        AudioDeviceManager(config).load_or_detect_devices()


def test_strict_names_off_keeps_the_old_fallback(monkeypatch):
    monkeypatch.setattr(audio_device, "find_device_by_name", lambda kind, name: None)
    monkeypatch.setattr(audio_device, "select_audio_device", lambda kind: info("Built-in"))
    config = StubConfig({"input_device_name": "Jabra", "output_device_name": "Jabra"})
    assert AudioDeviceManager(config).load_or_detect_devices().input_device_id == 3


def test_strict_names_pick_the_jabra_when_a_webcam_microphone_is_also_present(monkeypatch):
    devices = {"jabra": info("Jabra SPEAK 510 USB: Audio (hw:1,0)")}
    monkeypatch.setattr(audio_device, "find_device_by_name", lambda kind, name: devices["jabra"])
    config = StubConfig(
        {"input_device_name": "Jabra", "output_device_name": "Jabra", "strict_names": True}
    )
    result = AudioDeviceManager(config).load_or_detect_devices()
    assert result.input_device_id == 3 and result.output_device_id == 3


async def test_audio_plugin_keeps_retrying_in_the_background(monkeypatch):
    import src.plugins.audio as audio_mod

    attempts = {"n": 0}

    class FakeCodec:
        async def initialize(self):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("Configured input audio device 'Jabra' not found")

        def set_encoded_callback(self, cb):
            pass

    monkeypatch.setattr(audio_mod, "AudioCodec", FakeCodec)
    monkeypatch.setattr(audio_mod, "AUDIO_RETRY_INTERVAL_S", 0.01)
    monkeypatch.setattr("src.utils.audio_utils.refresh_portaudio_devices", lambda **k: None)

    class Bus:
        def on(self, *_):
            pass

        async def emit(self, *_):
            pass

    class Config:
        def get_config(self, path, default=None):
            return 5 if path == "AUDIO_DEVICES.init_retry_s" else default

    class Ctx:
        event_bus = Bus()

        def get_config(self):
            return Config()

    import asyncio

    class Cmd:
        def spawn(self, coro, name):
            return asyncio.ensure_future(coro)

    plugin = audio_mod.AudioPlugin()
    await plugin.setup(Ctx(), Cmd())  # Does not raise, does not block the app
    assert plugin.codec is None and not plugin.audio_ready and not plugin.failed
    for _ in range(100):
        if plugin.audio_ready:
            break
        await asyncio.sleep(0.01)
    assert plugin.audio_ready
    assert attempts["n"] == 3


# ---------- camera isolation ----------


class HangingSource:
    """open() blocks like a stuck USB driver call."""

    def __init__(self):
        self.release = threading.Event()

    def open(self):
        self.release.wait(10)
        return False

    def read(self):
        return None

    def close(self):
        pass


class Cfg:
    warm_up_frames = 0
    flip = "none"
    device = ""
    camera_index = 0
    backend = "opencv"


def test_camera_that_hangs_on_open_never_blocks_the_caller():
    source = HangingSource()
    service = CameraService(config_loader=lambda: Cfg(), source_factory=lambda cfg: source)
    client = object()
    started = time.monotonic()
    service.acquire(client)
    assert time.monotonic() - started < 0.5  # Returns at once; opening happens in a thread
    assert service.latest_frame() is None
    source.release.set()
    service.release(client)


def test_camera_missing_keeps_retrying_without_raising():
    class Missing:
        opens = 0

        def open(self):
            Missing.opens += 1
            return False

        def read(self):
            return None

        def close(self):
            pass

    service = CameraService(config_loader=lambda: Cfg(), source_factory=lambda cfg: Missing())
    client = object()
    service.acquire(client)
    time.sleep(0.3)
    assert service.state in ("opening", "reconnecting")
    assert service.latest_frame() is None
    assert service.capture_jpeg(320, timeout=0.2) is None  # Photo request fails cleanly
    service.release(client)


def test_camera_read_errors_reconnect_instead_of_crashing():
    class Flaky:
        def open(self):
            return True

        def read(self):
            raise OSError("USB device vanished")

        def close(self):
            pass

    service = CameraService(config_loader=lambda: Cfg(), source_factory=lambda cfg: Flaky())
    client = object()
    service.acquire(client)
    time.sleep(0.3)
    assert service.state == "reconnecting"
    service.release(client)


def test_fourcc_is_validated_and_applied():
    from src.mcp.tools.camera.capture_backend import apply_fourcc, normalize_fourcc

    assert normalize_fourcc("mjpg") == "MJPG"
    assert normalize_fourcc("") == ""
    assert normalize_fourcc("toolong") == ""
    assert normalize_fourcc(None) == ""

    calls = []

    class Capture:
        def set(self, prop, value):
            calls.append((prop, value))

    apply_fourcc(Capture(), "")
    assert calls == []
    apply_fourcc(Capture(), "MJPG")
    assert len(calls) == 1


def test_pi_preset_asks_the_webcam_for_small_mjpeg():
    import json

    preset = json.loads(
        (ROOT / "deploy/raspberry-pi/config.pi5.json").read_text(encoding="utf-8")
    )
    camera = preset["CAMERA"]
    assert camera["fourcc"] == "MJPG"
    assert (camera["frame_width"], camera["frame_height"]) == (640, 480)
    audio = preset["AUDIO_DEVICES"]
    assert audio["strict_names"] is True and audio["input_device_name"] == "Jabra"
    assert audio["output_device_name"] == "Jabra"
