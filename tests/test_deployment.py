"""Pi deployment files, config preset and audio device name handling."""

import configparser
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.apply_config_preset import flatten
from src.utils import audio_device
from src.utils.audio_device import AudioDeviceManager
from src.utils.config_manager import ConfigManager

ROOT = Path(__file__).resolve().parent.parent
PI_DIR = ROOT / "deploy" / "raspberry-pi"
PRESET = PI_DIR / "config.pi5.json"


def test_flatten_builds_config_paths():
    assert flatten({"A": {"B": {"C": 1}, "D": 2}, "E": None}) == {
        "A.B.C": 1,
        "A.D": 2,
        "E": None,
    }


def test_pi_preset_only_uses_known_settings():
    preset = json.loads(PRESET.read_text(encoding="utf-8"))
    defaults = ConfigManager.DEFAULT_CONFIG

    def walk(section, path=""):
        for key, value in section.items():
            current = f"{path}.{key}" if path else key
            node = defaults
            for part in current.split("."):
                node = node[part]
            if isinstance(value, dict):
                walk(value, current)

    walk(preset)


def test_pi_preset_matches_requirements():
    preset = json.loads(PRESET.read_text(encoding="utf-8"))
    assert preset["FACE"]["FULLSCREEN"] is True
    assert 1 <= preset["WAKE_WORD_OPTIONS"]["NUM_THREADS"] <= 2
    assert preset["VISION"]["NUM_THREADS"] == 2
    assert preset["AEC_OPTIONS"]["ENABLED"] is False


def run_preset(data_dir, *args):
    env = dict(os.environ, XIAOZHI_DATA_DIR=str(data_dir), PYTHONUTF8="1")
    return subprocess.run(
        [sys.executable, "scripts/apply_config_preset.py", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def read_config(data_dir):
    return json.loads((data_dir / "config" / "config.json").read_text(encoding="utf-8"))


def seed_config(data_dir, config):
    (data_dir / "config").mkdir()
    path = data_dir / "config" / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")


def test_apply_preset_keeps_unrelated_settings(tmp_path):
    seed_config(tmp_path, {"SYSTEM_OPTIONS": {"DEVICE_ID": "keep-me"}})
    result = run_preset(tmp_path, str(PRESET))
    assert result.returncode == 0, result.stderr
    config = read_config(tmp_path)
    assert config["FACE"]["FULLSCREEN"] is True
    assert config["SYSTEM_OPTIONS"]["DEVICE_ID"] == "keep-me"
    assert config["VISION"]["CONFIDENCE"] == 0.35  # Default kept


def test_dry_run_writes_nothing(tmp_path):
    seed_config(tmp_path, {})
    result = run_preset(tmp_path, str(PRESET), "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "FACE.FULLSCREEN" in result.stdout
    assert read_config(tmp_path).get("FACE", {}).get("FULLSCREEN") is not True


def test_missing_preset_fails_clearly(tmp_path):
    result = run_preset(tmp_path, str(tmp_path / "missing.json"))
    assert result.returncode == 1
    assert "Cannot read preset" in result.stderr


def test_service_file_is_valid_and_restarts_on_crash():
    parser = configparser.RawConfigParser()
    parser.optionxform = str
    parser.read_string((PI_DIR / "xiaozhi.service").read_text(encoding="utf-8"))
    service = parser["Service"]
    assert service["Restart"] == "on-failure"
    assert service["WorkingDirectory"] == "@APP_DIR@"
    assert "main.py --mode gui" in service["ExecStart"]
    # An unprivileged systemd user service cannot raise its priority and fails to start
    assert not service.get("Nice", "0").startswith("-")


def test_deployment_scripts_use_unix_line_endings():
    # A checkout with CRLF endings breaks bash and systemd on the Pi
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "deploy/raspberry-pi/** text eol=lf" in attributes


class StubConfig:
    def __init__(self, audio):
        self.audio = audio
        self.writes = {}

    def get_config(self, path, default=None):
        return self.audio if path == "AUDIO_DEVICES" else default

    def update_config(self, path, value):
        self.writes[path] = value
        return True


def device(name):
    return {"index": 3, "name": name, "sample_rate": 48000, "channels": 1}


def test_partial_device_name_survives_card_renumbering(monkeypatch):
    full_name = "Jabra SPEAK 510 USB: Audio (hw:2,0)"
    monkeypatch.setattr(
        audio_device, "find_device_by_name", lambda kind, name: device(full_name)
    )
    config = StubConfig({"input_device_name": "Jabra", "output_device_name": "Jabra"})
    AudioDeviceManager(config).load_or_detect_devices()
    assert "AUDIO_DEVICES.input_device_name" not in config.writes
    assert "AUDIO_DEVICES.output_device_name" not in config.writes


def test_configured_name_survives_boot_without_the_device(monkeypatch):
    monkeypatch.setattr(audio_device, "find_device_by_name", lambda kind, name: None)
    monkeypatch.setattr(
        audio_device, "select_audio_device", lambda kind: device("Built-in")
    )
    config = StubConfig({"input_device_name": "Jabra", "output_device_name": "Jabra"})
    AudioDeviceManager(config).load_or_detect_devices()
    assert "AUDIO_DEVICES.input_device_name" not in config.writes


def test_first_run_saves_detected_names(monkeypatch):
    monkeypatch.setattr(
        audio_device, "select_audio_device", lambda kind: device("Built-in")
    )
    config = StubConfig({})
    AudioDeviceManager(config).load_or_detect_devices()
    assert config.writes["AUDIO_DEVICES.input_device_name"] == "Built-in"
    assert config.writes["AUDIO_DEVICES.output_device_name"] == "Built-in"
