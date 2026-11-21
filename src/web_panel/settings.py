"""WEB.* settings with defaults and range checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WebSettings:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8080
    password: str = ""
    camera_stream_fps: float = 8.0
    https_enabled: bool = False
    https_port: int = 8443
    # Highest number of commands per second one browser may send
    max_commands_per_s: float = 40.0


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def load_web_settings(config=None) -> WebSettings:
    from src.utils.config_manager import ConfigManager

    defaults = ConfigManager.DEFAULT_CONFIG["WEB"]

    def value(key: str, cast, lo=None, hi=None):
        raw = defaults[key] if config is None else config.get_config(f"WEB.{key}", defaults[key])
        try:
            result = cast(raw)
        except (TypeError, ValueError):
            result = cast(defaults[key])
        if lo is not None:
            result = max(lo, result)
        if hi is not None:
            result = min(hi, result)
        return result

    return WebSettings(
        enabled=value("ENABLED", _as_bool),
        host=str(value("HOST", lambda v: str(v or "0.0.0.0"))).strip() or "0.0.0.0",
        port=value("PORT", int, 1, 65535),
        password=str(value("PASSWORD", lambda v: "" if v is None else str(v))),
        camera_stream_fps=value("CAMERA_STREAM_FPS", float, 0.5, 30.0),
        https_enabled=value("HTTPS_ENABLED", _as_bool),
        https_port=value("HTTPS_PORT", int, 1, 65535),
    )
