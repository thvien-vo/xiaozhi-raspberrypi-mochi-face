"""TUI settings: configuration field definitions and read/write (connected to ConfigManager + CONFIG_CHANGED)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()


@dataclass(frozen=True)
class SettingField:
    """An editable configuration item."""

    path: str
    label: str
    kind: str = "str"  # str | int | bool | choice
    choices: tuple[str, ...] = ()
    help: str = ""


# Phase 1: System / Audio / Camera / Wake Word
SETTING_SECTIONS: list[tuple[str, list[SettingField]]] = [
    (
        "System",
        [
            SettingField(
                "SYSTEM_OPTIONS.NETWORK.OTA_VERSION_URL",
                "OTA URL",
                help="OTA / activation configuration URL",
            ),
            SettingField(
                "SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL",
                "WebSocket URL",
                help="WebSocket server URL",
            ),
            SettingField(
                "SYSTEM_OPTIONS.DEVICE_ID",
                "Device ID",
                help="Device-Id (usually MAC address)",
            ),
            SettingField(
                "SYSTEM_OPTIONS.CLIENT_ID",
                "Client ID",
                help="Client-Id",
            ),
        ],
    ),
    (
        "Audio",
        [
            SettingField(
                "AUDIO_DEVICES.input_device_name",
                "Input Device Name",
                help="Match microphone by name (ID may change after hot-plug)",
            ),
            SettingField(
                "AUDIO_DEVICES.output_device_name",
                "Output Device Name",
                help="Match speaker/headphones by name",
            ),
            SettingField(
                "AUDIO_DEVICES.opus_output_sample_rate",
                "Opus Output Sample Rate",
                kind="choice",
                choices=("24000", "16000"),
                help="Official 24000 / third-party often 16000",
            ),
            SettingField(
                "AUDIO_DEVICES.frame_duration",
                "Frame Duration ms",
                kind="choice",
                choices=("20", "40", "60"),
                help="20 low latency / 60 low CPU",
            ),
        ],
    ),
    (
        "Camera",
        [
            SettingField(
                "CAMERA.backend",
                "Capture Backend",
                kind="choice",
                choices=("auto", "opencv", "picamera2"),
                help="auto tries OpenCV first, then falls back to Pi CSI",
            ),
            SettingField(
                "CAMERA.device",
                "Device Path",
                help="e.g. /dev/video0; takes precedence over index when non-empty",
            ),
            SettingField(
                "CAMERA.camera_index",
                "Device Index",
                kind="int",
                help="OpenCV numeric index",
            ),
            SettingField(
                "CAMERA.frame_width",
                "Width",
                kind="int",
            ),
            SettingField(
                "CAMERA.frame_height",
                "Height",
                kind="int",
            ),
        ],
    ),
    (
        "Wake Word",
        [
            SettingField(
                "WAKE_WORD_OPTIONS.USE_WAKE_WORD",
                "Enable Wake Word",
                kind="bool",
            ),
            SettingField(
                "WAKE_WORD_OPTIONS.WAKE_WORD",
                "Wake Word",
                help="e.g. Hello Xiaozhi",
            ),
            SettingField(
                "WAKE_WORD_OPTIONS.WAKE_WORD_LANG",
                "Language",
                kind="choice",
                choices=("zh", "en"),
            ),
        ],
    ),
]


def load_setting_values() -> dict[str, str]:
    """Read current configuration as a string mapping (path -> display value)."""
    cfg = get_config()
    values: dict[str, str] = {}
    for _section, fields in SETTING_SECTIONS:
        for f in fields:
            raw = cfg.get_config(f.path, "")
            if f.kind == "bool":
                values[f.path] = "true" if bool(raw) else "false"
            elif raw is None:
                values[f.path] = ""
            else:
                values[f.path] = str(raw)
    return values


def parse_field_value(field: SettingField, text: str) -> Any:
    """Convert input box string into a configuration value."""
    s = (text or "").strip()
    if field.kind == "int":
        if s == "":
            return 0
        return int(s)
    if field.kind == "bool":
        return s.lower() in ("1", "true", "yes", "on", "y")
    if field.kind == "choice":
        if field.choices and s not in field.choices:
            # Still write user value, validated/prompted by upper layer
            return s
        if field.path.endswith("opus_output_sample_rate") or field.path.endswith(
            "frame_duration"
        ):
            try:
                return int(s)
            except ValueError:
                return s
        return s
    return s


def save_settings(values: dict[str, str]) -> tuple[bool, str]:
    """Batch write configuration and persist to disk.

    Returns:
        (ok, message)
    """
    cfg = get_config()
    updates: dict[str, Any] = {}
    try:
        for _section, fields in SETTING_SECTIONS:
            for f in fields:
                if f.path not in values:
                    continue
                updates[f.path] = parse_field_value(f, values[f.path])
        if not updates:
            return True, "No changes"
        ok = cfg.update_configs(updates)
        if not ok:
            return False, "Save failed (disk write error)"
        logger.info(f"TUI saved {len(updates)} configuration items")
        return True, f"Saved {len(updates)} items"
    except Exception as e:
        logger.error(f"TUI failed to save configuration: {e}", exc_info=True)
        return False, f"Save failed: {e}"
