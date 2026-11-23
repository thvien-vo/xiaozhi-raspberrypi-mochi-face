"""Local voice activity detection: send microphone audio only while someone speaks.

Without it the app streams every 20 ms microphone frame to the server while listening, and
the server's recognizer turns silence, room noise and music into invented sentences. The
gate sits between the Opus encoder and the network (src/plugins/audio.py):

* Every frame is still encoded (the encoder state stays continuous) and kept in a short
  ring buffer while the gate is closed.
* A frame counts as speech when Silero VAD (sherpa-onnx, already a dependency; model in
  models/vad/silero_vad.onnx) says so AND its level is at least ``energy_db`` dBFS.
  Without the model, the level alone decides (above both energy_db and noise floor + 12 dB).
* After ``min_speech_ms`` of speech (gaps of up to 60 ms allowed) the gate opens and first
  sends the ring: ``preroll_ms`` before the speech plus the speech itself, so the first word
  is not cut.
* It closes after ``hangover_ms`` without speech; those trailing frames are real silence the
  server needs to see the end of the sentence.

The session resets the gate (closed, ring emptied) whenever listening starts, so the tail of
the robot's own voice is never sent as the pre-roll of the next sentence.

Settings: VAD.* in the config, live-editable from the web panel. One shared instance
(get_mic_vad); push() runs on the audio input thread, the rest on any thread.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from src.logging import get_logger

logger = get_logger()

CONFIG_SECTION = "VAD"
SAMPLE_RATE = 16000
FRAME_MS = 20
GAP_FRAMES = 3  # Up to 60 ms of non-speech inside the opening run

RANGES = {
    "threshold": (0.1, 0.95),
    "min_speech_ms": (60, 2000),
    "preroll_ms": (0, 1000),
    "hangover_ms": (200, 3000),
    "energy_db": (-80.0, -10.0),
}


@dataclass(frozen=True)
class VadSettings:
    enabled: bool = True
    threshold: float = 0.5  # Silero speech probability (higher = stricter)
    min_speech_ms: int = 300
    preroll_ms: int = 300
    hangover_ms: int = 900
    energy_db: float = -50.0  # Quietest frame (dBFS) that can count as speech

    def clamped(self) -> VadSettings:
        values = asdict(self)
        for key, (lo, hi) in RANGES.items():
            v = float(values[key])
            if not math.isfinite(v):
                v = float(getattr(VadSettings, key))
            v = max(lo, min(hi, v))
            values[key] = int(round(v)) if key.endswith("_ms") else round(v, 3)
        values["enabled"] = bool(values["enabled"])
        return VadSettings(**values)

    def to_config(self) -> dict:
        return {k.upper(): v for k, v in asdict(self).items()}

    @classmethod
    def from_config(cls, config) -> VadSettings:
        base = cls()
        values = {}
        for key in asdict(base):
            raw = config.get_config(f"{CONFIG_SECTION}.{key.upper()}", None)
            if raw is None:
                continue
            if key == "enabled":
                values[key] = bool(raw)
            elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
                values[key] = raw
        return replace(base, **values).clamped()


def default_model_path() -> Path:
    from src.utils.resource_finder import get_models_dir

    return get_models_dir() / "vad" / "silero_vad.onnx"


class SileroDetector:
    """Silero VAD through sherpa-onnx; decides once per 512-sample window (32 ms)."""

    name = "silero"

    def __init__(self, model_path: Path, threshold: float) -> None:
        import sherpa_onnx

        self._sherpa = sherpa_onnx
        self._path = str(model_path)
        self._threshold = threshold
        self._model = self._create(threshold)
        self._window = self._model.window_size()
        self._buf = np.zeros(0, dtype=np.float32)
        self._last = False

    def _create(self, threshold: float):
        s = self._sherpa
        config = s.VadModelConfig(
            silero_vad=s.SileroVadModelConfig(model=self._path, threshold=float(threshold)),
            sample_rate=SAMPLE_RATE,
            num_threads=1,
        )
        return s.VadModel.create(config)

    def set_threshold(self, threshold: float) -> None:
        if abs(threshold - self._threshold) > 1e-6:
            self._threshold = threshold
            self._model = self._create(threshold)
            self._buf = np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        self._model.reset()
        self._buf = np.zeros(0, dtype=np.float32)
        self._last = False

    def is_speech(self, pcm: np.ndarray, level_db: float) -> bool:
        self._buf = np.concatenate([self._buf, pcm])
        while len(self._buf) >= self._window:
            window, self._buf = self._buf[: self._window], self._buf[self._window :]
            self._last = bool(self._model.is_speech(window))
        return self._last


class EnergyDetector:
    """Fallback without the model: loud enough and well above the room's noise floor."""

    name = "energy"

    def __init__(self) -> None:
        self.noise_db: float | None = None

    def set_threshold(self, threshold: float) -> None:
        pass

    def reset(self) -> None:
        pass

    def is_speech(self, pcm: np.ndarray, level_db: float) -> bool:
        if self.noise_db is None:
            self.noise_db = level_db  # The first frame is taken as the room
        speech = level_db > self.noise_db + 12.0
        # Noise floor: follows quiet frames quickly, loud ones very slowly
        rate = 0.002 if speech else 0.05
        self.noise_db += (level_db - self.noise_db) * rate
        return speech


def level_dbfs(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float32)))))
    if pcm.dtype == np.int16:
        rms /= 32768.0
    return 20.0 * math.log10(max(rms, 1e-6))


class MicVadGate:
    def __init__(
        self,
        settings: VadSettings | None = None,
        detector=None,
        save: Callable[[dict], None] | None = None,
        model_path: Path | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._settings = (settings or VadSettings()).clamped()
        self._save = save
        self._model_path = model_path
        self._detector = detector
        self._detector_error = ""
        self._listeners: list[Callable[[], None]] = []
        self._ring: deque = deque()
        self._open = False
        self._run = 0
        self._span = 0
        self._gap = 0
        self._silence = 0
        self._reset_pending = False
        self._open_frames = 0
        self._open_peak = -120.0
        # Statistics
        self.frames_in = 0
        self.frames_out = 0
        self.opens = 0
        self.noise_db = -60.0
        self.last_speech_db = None
        self.last_open_ms = 0

    @classmethod
    def from_config(cls, config) -> MicVadGate:
        def save(updates: dict) -> None:
            paths = {f"{CONFIG_SECTION}.{k}": v for k, v in updates.items()}
            if hasattr(config, "update_configs"):
                config.update_configs(paths)
            else:
                for path, value in paths.items():
                    config.update_config(path, value)

        return cls(VadSettings.from_config(config), save=save)

    # ----- Detector (created lazily on the first frame: loading takes ~50 ms) -----

    def _ensure_detector(self):
        if self._detector is not None:
            return self._detector
        path = self._model_path or default_model_path()
        try:
            self._detector = SileroDetector(path, self._settings.threshold)
            logger.info(f"VAD: Silero model loaded ({path})")
        except Exception as e:
            self._detector_error = str(e)
            logger.warning(f"VAD: Silero unavailable ({e}); using the level-only detector")
            self._detector = EnergyDetector()
        return self._detector

    @property
    def backend(self) -> str:
        return getattr(self._detector, "name", "chưa chạy") if self._detector is not None else "chưa chạy"

    # ----- Settings -----

    @property
    def settings(self) -> VadSettings:
        with self._lock:
            return self._settings

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def update(self, save: bool = True, **values) -> VadSettings:
        """Change some settings (unknown keys are ignored, values clamped)."""
        known = {k: v for k, v in values.items() if k in VadSettings.__dataclass_fields__ and v is not None}
        with self._lock:
            new = replace(self._settings, **known).clamped()
            changed = new != self._settings
            self._settings = new
            if changed:
                self._reset_pending = True
        if save and self._save is not None:
            try:
                self._save(new.to_config())
            except Exception:
                logger.warning("Cannot save VAD settings", exc_info=True)
        if changed:
            logger.info(f"VAD: settings {new}")
            self._notify()
        return new

    def reset(self) -> None:
        """Close the gate and forget the ring (applied on the next frame)."""
        with self._lock:
            self._reset_pending = True

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    # ----- Audio thread -----

    def push(self, pcm: np.ndarray, packet: bytes) -> list[bytes]:
        """One 20 ms frame in; the packets to send now out (maybe none, maybe a burst)."""
        with self._lock:
            settings = self._settings
            if self._reset_pending:
                self._reset_pending = False
                self._close_locked(log=False)
                self._ring.clear()
                if self._detector is not None:
                    self._detector.set_threshold(settings.threshold)
                    self._detector.reset()
        self.frames_in += 1
        if not settings.enabled:
            self.frames_out += 1
            return [packet]
        detector = self._ensure_detector()
        level = level_dbfs(pcm)
        try:
            speech = detector.is_speech(np.asarray(pcm, dtype=np.float32).reshape(-1), level)
        except Exception:
            logger.warning("VAD: detector failed, using the level-only detector", exc_info=True)
            self._detector = EnergyDetector()
            speech = False
        speech = speech and level >= settings.energy_db
        preroll = settings.preroll_ms // FRAME_MS
        need = max(1, settings.min_speech_ms // FRAME_MS)
        hang = max(1, settings.hangover_ms // FRAME_MS)
        out: list[bytes] = []
        with self._lock:
            if not self._open:
                self._ring.append(packet)
                while len(self._ring) > preroll + need + GAP_FRAMES:
                    self._ring.popleft()
                if self._run:
                    self._span += 1  # Frames since the run's first speech frame
                if speech:
                    if not self._run:
                        self._span = 1
                    self._run += 1
                    self._gap = 0
                    self._open_peak = max(self._open_peak, level)
                else:
                    self._gap += 1
                    if self._gap > GAP_FRAMES:
                        self._run = 0
                        self._span = 0
                        self._open_peak = -120.0
                    self.noise_db += (level - self.noise_db) * 0.02
                if self._run >= need:
                    self._open = True
                    self._silence = 0
                    out = list(self._ring)[-(preroll + self._span):]
                    self._open_frames = len(out)
                    self.opens += 1
                    self._ring.clear()
                    logger.info(
                        f"VAD: speech, sending (level {level:.0f} dBFS, noise {self.noise_db:.0f} dBFS)"
                    )
            else:
                out = [packet]
                self._open_frames += 1
                if speech:
                    self._silence = 0
                    self._open_peak = max(self._open_peak, level)
                else:
                    self._silence += 1
                    if self._silence >= hang:
                        self._close_locked(log=True)
            self.frames_out += len(out)
        if out and len(out) > 1:
            self._notify()
        return out

    def _close_locked(self, log: bool) -> None:
        if self._open:
            self.last_open_ms = self._open_frames * FRAME_MS
            self.last_speech_db = round(self._open_peak, 1)
            if log:
                logger.info(
                    f"VAD: silence, stopped sending ({self.last_open_ms} ms sent, peak {self._open_peak:.0f} dBFS)"
                )
        self._open = False
        self._run = 0
        self._span = 0
        self._gap = 0
        self._silence = 0
        self._open_frames = 0
        self._open_peak = -120.0

    # ----- Listeners and snapshot -----

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                logger.debug("VAD listener failed", exc_info=True)

    def snapshot(self) -> dict:
        with self._lock:
            s = self._settings
            sent = self.frames_out / self.frames_in if self.frames_in else None
            return {
                **asdict(s),
                "backend": self.backend,
                "backend_error": self._detector_error,
                "open": self._open,
                "opens": self.opens,
                "sent_ratio": None if sent is None else round(sent, 2),
                "noise_db": round(self.noise_db),
                "last_speech_db": None if self.last_speech_db is None else round(self.last_speech_db),
                "last_open_ms": self.last_open_ms,
                "ranges": {k: list(v) for k, v in RANGES.items()},
            }


_shared: MicVadGate | None = None
_shared_lock = threading.Lock()


def get_mic_vad(config=None) -> MicVadGate:
    """The process-wide gate, created from the config on first use."""
    global _shared
    with _shared_lock:
        if _shared is None:
            if config is None:
                from src.utils.config_manager import get_config

                try:
                    config = get_config()
                except RuntimeError:
                    config = None
            _shared = MicVadGate.from_config(config) if config is not None else MicVadGate()
        return _shared


def reset_mic_vad() -> None:
    """Forget the shared gate (tests only)."""
    global _shared
    with _shared_lock:
        _shared = None
