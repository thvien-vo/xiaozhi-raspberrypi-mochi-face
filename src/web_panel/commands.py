"""Parsing and validation of WebSocket commands from the browser.

Everything a browser sends is untrusted: each message becomes a Command with checked
types and clamped values, or a CommandError with a message for the page. Angles are
clamped to the configured limits again by the servo controller.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from src.audio_codecs.levels import clean_channel
from src.music.library import MAX_SECONDS, valid_track_id
from src.music.play_queue import REPEAT_MODES
from src.servo.motion import GESTURE_LABELS, clean_name
from src.ui.face_color import normalize_hex, parse_mode

MAX_MESSAGE_BYTES = 4096
MAX_TEXT_CHARS = 300

# Ranges for live tracking tuning (wider than sensible, narrower than harmful)
KP_RANGE = (0.0, 40.0)
DEADBAND_RANGE = (0.0, 0.5)


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    type: str
    args: dict = field(default_factory=dict)


def _number(data: dict, key: str, lo: float, hi: float, required: bool = True) -> float | None:
    if key not in data or data[key] is None:
        if required:
            raise CommandError(f"thiếu '{key}'")
        return None
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandError(f"'{key}' phải là số")
    value = float(value)
    if not math.isfinite(value):
        raise CommandError(f"'{key}' không hợp lệ")
    return max(lo, min(hi, value))


def _bool(data: dict, key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise CommandError(f"'{key}' phải là true/false")
    return value


def _text(data: dict, key: str, limit: int) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise CommandError(f"'{key}' phải là chuỗi")
    value = value.strip()
    if not value:
        raise CommandError(f"'{key}' trống")
    return value[:limit]


def _track_id(data: dict) -> str:
    value = data.get("track_id")
    if not valid_track_id(value):
        raise CommandError("'track_id' không hợp lệ")
    return value


def _uid(data: dict) -> int:
    value = data.get("uid")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**31:
        raise CommandError("'uid' không hợp lệ")
    return value


def _parse_music(kind: str, data: dict) -> Command:
    if kind in ("music_pause", "music_stop", "music_next", "music_previous", "music_queue_clear"):
        return Command(kind)
    if kind == "music_play":
        # With track_id: play it and queue the library; without: resume or start
        return Command(kind, {"track_id": _track_id(data) if data.get("track_id") is not None else None})
    if kind == "music_seek":
        return Command(kind, {"position": _number(data, "position", 0.0, float(MAX_SECONDS))})
    if kind == "music_volume":
        return Command(kind, {"volume": _number(data, "volume", 0.0, 100.0), "save": data.get("save") is not False})
    if kind == "music_mode":
        repeat, shuffle = data.get("repeat"), data.get("shuffle")
        if repeat is not None and repeat not in REPEAT_MODES:
            raise CommandError("'repeat' phải là " + "/".join(REPEAT_MODES))
        if shuffle is not None:
            shuffle = _bool(data, "shuffle")
        if repeat is None and shuffle is None:
            raise CommandError("cần 'repeat' hoặc 'shuffle'")
        return Command(kind, {"repeat": repeat, "shuffle": shuffle})
    if kind == "music_enqueue":
        return Command(kind, {"track_id": _track_id(data), "play_next": data.get("play_next") is True})
    if kind in ("music_dequeue", "music_queue_play"):
        return Command(kind, {"uid": _uid(data)})
    if kind == "music_delete":
        return Command(kind, {"track_id": _track_id(data)})
    if kind == "music_dance":
        return Command(kind, {"enabled": _bool(data, "enabled")})
    raise CommandError(f"lệnh không biết: {kind}")


def _parse_volume(kind: str, data: dict) -> Command:
    try:
        channel = clean_channel(data.get("channel", "master"))
    except ValueError as e:
        raise CommandError(str(e)) from e
    if kind == "volume":
        return Command(kind, {"channel": channel, "volume": _number(data, "volume", 0.0, 100.0), "save": data.get("save") is not False})
    if kind == "volume_step":
        return Command(kind, {"channel": channel, "delta": _number(data, "delta", -100.0, 100.0)})
    # "mute": muted true/false, or absent to toggle
    muted = None if data.get("muted") is None else _bool(data, "muted")
    return Command(kind, {"muted": muted})


def parse_command(
    raw: str | bytes,
    pan_limit: float,
    tilt_limit: float,
    emotions: tuple[str, ...] | list[str] = (),
) -> Command:
    """Turn one WebSocket message into a validated Command."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise CommandError("tin nhắn quá dài")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise CommandError("không phải JSON") from e
    if not isinstance(data, dict):
        raise CommandError("tin nhắn phải là đối tượng JSON")
    kind = data.get("type")
    if not isinstance(kind, str):
        raise CommandError("thiếu 'type'")

    if kind == "set_angles":
        pan = _number(data, "pan", -pan_limit, pan_limit, required=False)
        tilt = _number(data, "tilt", -tilt_limit, tilt_limit, required=False)
        if pan is None and tilt is None:
            raise CommandError("cần 'pan' hoặc 'tilt'")
        return Command(kind, {"pan": pan, "tilt": tilt})
    if kind in ("center", "stop_motion", "record_cancel", "ping", "face_wake", "release"):
        return Command(kind)
    if kind == "tracking":
        return Command(kind, {"enabled": _bool(data, "enabled")})
    if kind == "tracking_params":
        args = {
            "kp_pan": _number(data, "kp_pan", *KP_RANGE, required=False),
            "kp_tilt": _number(data, "kp_tilt", *KP_RANGE, required=False),
            "deadband": _number(data, "deadband", *DEADBAND_RANGE, required=False),
            "save": bool(data.get("save") is True),
        }
        if all(args[k] is None for k in ("kp_pan", "kp_tilt", "deadband")):
            raise CommandError("không có tham số nào")
        return Command(kind, args)
    if kind == "emotion":
        name = _text(data, "name", 32).lower()
        if emotions and name not in emotions:
            raise CommandError(f"biểu cảm không có: {name}")
        return Command(kind, {"name": name})
    if kind == "auto_mode":
        return Command(kind, {"enabled": _bool(data, "enabled")})
    if kind == "eye_color":
        # {"color": "#rrggbb"} and/or {"mode": "fixed"|"rainbow"|"emotion"}; "save": false
        # for live previews while a color picker is dragged
        color = mode = None
        try:
            if data.get("color") is not None:
                color = normalize_hex(_text(data, "color", 16))
            if data.get("mode") is not None:
                mode = parse_mode(_text(data, "mode", 16))
        except ValueError as e:
            raise CommandError(str(e)) from e
        if color is None and mode is None:
            raise CommandError("cần 'color' hoặc 'mode'")
        return Command(kind, {"color": color, "mode": mode, "save": data.get("save") is not False})
    if kind == "face_stream":
        return Command(kind, {"enabled": _bool(data, "enabled")})
    if kind == "gesture":
        name = _text(data, "name", 32)
        if name not in GESTURE_LABELS:
            raise CommandError(f"cử chỉ không có: {name}")
        return Command(kind, {"name": name})
    if kind == "record_start":
        return Command(kind)
    if kind in ("record_stop", "play_motion", "delete_motion"):
        try:
            name = clean_name(_text(data, "name", 80))
        except ValueError as e:
            raise CommandError(str(e)) from e
        return Command(kind, {"name": name})
    if kind.startswith("music_"):
        return _parse_music(kind, data)
    if kind in ("volume", "volume_step", "mute"):
        return _parse_volume(kind, data)
    if kind == "send_text":
        return Command(kind, {"text": _text(data, "text", MAX_TEXT_CHARS)})
    raise CommandError(f"lệnh không biết: {kind}")


class RateLimiter:
    """Token bucket: rate tokens per second, bursts up to capacity."""

    def __init__(self, rate: float, capacity: float | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = max(0.1, float(rate))
        self.capacity = float(capacity if capacity is not None else rate)
        self._tokens = self.capacity
        self._clock = clock
        self._last = clock()

    def allow(self, cost: float = 1.0) -> bool:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False
