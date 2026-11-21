"""SERVO.* settings with defaults and range checks."""

from dataclasses import dataclass

from src.utils.config_manager import ConfigManager

DEFAULT_PORT = "/dev/yolo_uno"
ESPRESSIF_VID = 0x303A

# Mechanics: pan servo 1:1 (servo 0..180 = head -90..+90), tilt servo 1:2 (head -45..+45)
PAN_MAX_DEG = 90.0
TILT_MAX_DEG = 45.0
TILT_GEAR_RATIO = 2.0


@dataclass
class ServoSettings:
    enabled: bool = False
    port: str = ""
    baud: int = 115200
    pan_invert: bool = False
    tilt_invert: bool = False
    pan_offset: float = 0.0
    tilt_offset: float = 0.0
    pan_limit: float = PAN_MAX_DEG
    tilt_limit: float = TILT_MAX_DEG
    tracking_enabled: bool = True
    kp_pan: float = 12.0
    kp_tilt: float = 8.0
    deadband: float = 0.08
    max_step_deg: float = 6.0
    ema_alpha: float = 0.5
    lost_timeout_sec: float = 3.0
    manual_hold_sec: float = 5.0


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def load_servo_settings(config=None) -> ServoSettings:
    defaults = ConfigManager.DEFAULT_CONFIG["SERVO"]

    def value(key: str, cast, lo=None, hi=None):
        raw = defaults[key] if config is None else config.get_config(f"SERVO.{key}", defaults[key])
        try:
            result = cast(raw)
        except (TypeError, ValueError):
            result = cast(defaults[key])
        if lo is not None:
            result = max(lo, result)
        if hi is not None:
            result = min(hi, result)
        return result

    return ServoSettings(
        enabled=value("ENABLED", _as_bool),
        port=str(value("PORT", lambda v: "" if v is None else str(v))).strip(),
        baud=value("BAUD", int, 1200),
        pan_invert=value("PAN_INVERT", _as_bool),
        tilt_invert=value("TILT_INVERT", _as_bool),
        pan_offset=value("PAN_OFFSET", float, -45.0, 45.0),
        tilt_offset=value("TILT_OFFSET", float, -45.0, 45.0),
        pan_limit=value("PAN_LIMIT", float, 0.0, PAN_MAX_DEG),
        tilt_limit=value("TILT_LIMIT", float, 0.0, TILT_MAX_DEG),
        tracking_enabled=value("TRACKING_ENABLED", _as_bool),
        kp_pan=value("KP_PAN", float, 0.0),
        kp_tilt=value("KP_TILT", float, 0.0),
        deadband=value("DEADBAND", float, 0.0, 0.9),
        max_step_deg=value("MAX_STEP_DEG", float, 0.1, 45.0),
        ema_alpha=value("EMA_ALPHA", float, 0.05, 1.0),
        lost_timeout_sec=value("LOST_TIMEOUT_SEC", float, 0.5),
        manual_hold_sec=value("MANUAL_HOLD_SEC", float, 0.0),
    )
