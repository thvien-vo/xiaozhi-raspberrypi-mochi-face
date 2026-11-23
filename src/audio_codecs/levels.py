"""Volume levels of everything that comes out of the robot's speaker.

Four channels, each 0-100 %: ``master`` (everything), ``voice`` (the robot talking,
notifications included), ``music`` and ``effects`` (sound effects; nothing plays on it yet).
What a source really gets is master x its own channel, plus one mute switch for all.

The levels are applied as software gain in the AudioCodec's mixer. That works the same with
ALSA alone (the Pi has no PulseAudio or PipeWire), needs no card number, reacts within one
20 ms chunk, and survives unplugging and replugging the USB speaker because the numbers live
here and not in the device. The hardware mixer is left to deploy/raspberry-pi/jabra-audio.sh.

One shared instance (get_audio_levels) is read and written from the app loop, the MCP tools
and the web server's thread, so everything is behind a lock; listeners run outside it.
Values are kept in the config (AUDIO_VOLUME.*), written a moment after the last change.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

from src.logging import get_logger

logger = get_logger()

CHANNELS = ("master", "voice", "music", "effects")
CHANNEL_LABELS = {"master": "Tổng", "voice": "Giọng robot", "music": "Nhạc", "effects": "Hiệu ứng"}
DEFAULT_LEVEL = 100
DEFAULT_MUSIC_LEVEL = 70  # What MUSIC.VOLUME was before the levels existed
STEP = 5
SAVE_DELAY_S = 0.6
CONFIG_SECTION = "AUDIO_VOLUME"


def taper(percent: float) -> float:
    """Linear gain for a slider position: squared, roughly how loudness is heard."""
    p = min(100.0, max(0.0, percent)) / 100.0
    return p * p


def clean_level(value) -> int:
    """A level as 0..100, or ValueError for something that is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("âm lượng phải là số")
    if not math.isfinite(value):
        raise ValueError("âm lượng không hợp lệ")
    return int(max(0, min(100, round(value))))


def clean_channel(name) -> str:
    if name not in CHANNELS:
        raise ValueError(f"kênh âm lượng không có: {name!r} (cần một trong {', '.join(CHANNELS)})")
    return name


class AudioLevels:
    def __init__(
        self,
        values: dict | None = None,
        muted: bool = False,
        save: Callable[[dict], None] | None = None,
        save_delay: float = SAVE_DELAY_S,
    ) -> None:
        self._lock = threading.Lock()
        self._values = {c: DEFAULT_LEVEL for c in CHANNELS}
        for key, value in (values or {}).items():
            if key in self._values:
                try:
                    self._values[key] = clean_level(value)
                except ValueError:
                    pass
        self._muted = bool(muted)
        self._save = save
        self._save_delay = save_delay
        self._timer: threading.Timer | None = None
        self._listeners: list[Callable[[], None]] = []
        self._codec = None

    @classmethod
    def from_config(cls, config) -> AudioLevels:
        """Read AUDIO_VOLUME.*; the music level falls back to the old MUSIC.VOLUME."""
        values = {}
        for channel in CHANNELS:
            values[channel] = config.get_config(f"{CONFIG_SECTION}.{channel.upper()}", None)
        if values["music"] is None:
            values["music"] = config.get_config("MUSIC.VOLUME", DEFAULT_MUSIC_LEVEL)
        values = {k: v for k, v in values.items() if v is not None}

        def save(updates: dict) -> None:
            paths = {f"{CONFIG_SECTION}.{k}": v for k, v in updates.items()}
            if hasattr(config, "update_configs"):
                config.update_configs(paths)
            else:
                for path, value in paths.items():
                    config.update_config(path, value)

        return cls(values, bool(config.get_config(f"{CONFIG_SECTION}.MUTED", False)), save)

    # ----- Reading -----

    def get(self, channel: str) -> int:
        with self._lock:
            return self._values[clean_channel(channel)]

    @property
    def muted(self) -> bool:
        with self._lock:
            return self._muted

    def gain(self, channel: str) -> float:
        """Linear gain for a source channel: master x channel, 0 while muted."""
        with self._lock:
            return self._gain_locked(clean_channel(channel))

    def _gain_locked(self, channel: str) -> float:
        if self._muted:
            return 0.0
        if channel == "master":
            return taper(self._values["master"])
        return taper(self._values["master"]) * taper(self._values[channel])

    @property
    def available(self) -> bool:
        """A speaker is attached and its stream is running."""
        codec = self._codec
        return codec is not None and getattr(codec, "output_running", True) is not False

    def snapshot(self) -> dict:
        with self._lock:
            values = dict(self._values)
            muted = self._muted
        codec = self._codec
        device = str(getattr(codec, "output_device_name", "") or "") if codec is not None else ""
        available = self.available
        message = ""
        if codec is None:
            message = "Chưa có loa (không có thiết bị âm thanh): mức vẫn được lưu và áp dụng khi có loa"
        elif not available:
            message = "Loa không phát được lúc này: mức vẫn được lưu"
        getter = getattr(codec, "output_gains", None)
        applied = getter() if callable(getter) else None  # What the mixer really uses, for checking
        return {
            **values, "muted": muted, "available": available, "device": device, "message": message,
            "step": STEP, "applied": applied,
        }

    # ----- Changing -----

    def set(self, channel: str, value, persist: bool = True) -> int:
        channel = clean_channel(channel)
        level = clean_level(value)
        with self._lock:
            self._values[channel] = level
            # Turning the master up is a clear wish to hear something again
            if channel == "master" and level > 0:
                self._muted = False
        self._changed(persist)
        return level

    def change(self, channel: str, delta, persist: bool = True) -> int:
        channel = clean_channel(channel)
        if isinstance(delta, bool) or not isinstance(delta, (int, float)) or not math.isfinite(delta):
            raise ValueError("mức chỉnh phải là số")
        with self._lock:
            level = clean_level(self._values[channel] + delta)
            self._values[channel] = level
            if channel == "master" and delta > 0:
                self._muted = False
        self._changed(persist)
        return level

    def set_muted(self, muted: bool, persist: bool = True) -> bool:
        with self._lock:
            self._muted = bool(muted)
            result = self._muted
        self._changed(persist)
        return result

    def toggle_muted(self, persist: bool = True) -> bool:
        with self._lock:
            self._muted = not self._muted
            result = self._muted
        self._changed(persist)
        return result

    # ----- Speaker and listeners -----

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def attach(self, codec) -> None:
        """Use these levels for this AudioCodec (None: no speaker); applied at once."""
        self._codec = codec
        self._apply()
        self._notify()

    def _apply(self) -> None:
        codec = self._codec
        if codec is None:
            return
        with self._lock:
            gains = {c: self._gain_locked(c) for c in ("voice", "music", "effects")}
        try:
            setter = getattr(codec, "set_output_gains", None)
            if setter is not None:
                setter(**gains)
            elif hasattr(codec, "set_music_gain"):
                codec.set_music_gain(gains["music"])
        except Exception:
            logger.warning("Cannot apply the volume levels to the speaker", exc_info=True)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                logger.debug("Volume listener failed", exc_info=True)

    def _changed(self, persist: bool) -> None:
        self._apply()
        if persist:
            self._schedule_save()
        self._notify()

    # ----- Saving -----

    def _schedule_save(self) -> None:
        if self._save is None:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            if self._save_delay <= 0:
                self._timer = None
            else:
                self._timer = threading.Timer(self._save_delay, self.flush)
                self._timer.daemon = True
                self._timer.start()
                return
        self.flush()

    def flush(self) -> None:
        """Write the levels to the config now."""
        if self._save is None:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            updates = {c.upper(): v for c, v in self._values.items()}
            updates["MUTED"] = self._muted
        try:
            self._save(updates)
        except Exception:
            logger.warning("Cannot save the volume levels", exc_info=True)


_shared: AudioLevels | None = None
_shared_lock = threading.Lock()


def get_audio_levels(config=None) -> AudioLevels:
    """The process-wide levels, created from the config on first use."""
    global _shared
    with _shared_lock:
        if _shared is None:
            if config is None:
                from src.utils.config_manager import get_config

                try:
                    config = get_config()
                except RuntimeError:
                    config = None
            _shared = AudioLevels.from_config(config) if config is not None else AudioLevels()
        return _shared


def reset_audio_levels() -> None:
    """Forget the shared levels (tests only)."""
    global _shared
    with _shared_lock:
        if _shared is not None and _shared._timer is not None:
            _shared._timer.cancel()
        _shared = None
