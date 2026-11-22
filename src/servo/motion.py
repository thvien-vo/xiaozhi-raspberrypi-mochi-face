"""Who may move the head, canned gestures, and recorded motions.

Priority: the web panel (someone dragging the head) beats voice commands, which beat
dancing to music, which beats person tracking. A source that moves the head claims it for a hold time; a lower-priority
source is refused until the claim runs out. Tracking takes part through
HeadTracker.hold(), which every granted claim extends.

Gestures and recordings are lists of keyframes (seconds, pan, tilt in head degrees)
played by MotionPlayer in its own thread, so they keep running when the browser that
started them closes. A recording is relative to nothing: it replays the exact angles.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.logging import get_logger

logger = get_logger()

SOURCE_TRACKING = "tracking"
SOURCE_MUSIC = "music"  # Dancing to music (src/music/dance.py)
SOURCE_VOICE = "voice"
SOURCE_WEB = "web"
PRIORITY = {SOURCE_TRACKING: 0, SOURCE_MUSIC: 1, SOURCE_VOICE: 2, SOURCE_WEB: 3}

PLAYER_TICK_S = 0.05  # 20 Hz, the controller's own send limit
MAX_MOTION_FRAMES = 4000
MAX_MOTION_SECONDS = 120.0
MAX_NAME_LENGTH = 40


class ControlArbiter:
    """Grants head control by priority; a claim lasts hold_sec after the last request."""

    def __init__(
        self,
        tracker=None,
        hold_sec: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tracker = tracker
        self.hold_sec = max(0.0, float(hold_sec))
        self._clock = clock
        self._lock = threading.Lock()
        self._owner: str | None = None
        self._until = 0.0

    def request(self, source: str, hold: float | None = None) -> bool:
        """Claim control for source; False while a higher-priority claim is active."""
        if source not in PRIORITY:
            raise ValueError(f"unknown control source {source!r}")
        hold = self.hold_sec if hold is None else max(0.0, float(hold))
        now = self._clock()
        with self._lock:
            owner = self._owner if now < self._until else None
            if owner is not None and PRIORITY[owner] > PRIORITY[source]:
                return False
            self._owner = source
            # Never shorten a claim that a longer request (a gesture) already made
            self._until = max(now + hold, self._until if owner == source else 0.0)
            until = self._until
        if self._tracker is not None:
            self._tracker.hold(max(0.0, until - now))
        return True

    def release(self, source: str) -> None:
        """Shorten source's claim to the usual hold from now (a motion ended early)."""
        now = self._clock()
        with self._lock:
            if self._owner != source or now >= self._until:
                return
            self._until = min(self._until, now + self.hold_sec)
            remaining = self._until - now
        if self._tracker is not None:
            self._tracker.hold(remaining)

    @property
    def owner(self) -> str | None:
        with self._lock:
            return self._owner if self._clock() < self._until else None

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self._until - self._clock()) if self._owner else 0.0


@dataclass(frozen=True)
class Keyframe:
    t: float
    pan: float
    tilt: float


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


# ----- Gestures (keyframes relative to the pose the head is in when they start) -----

GESTURE_LABELS = {
    "nod": "Gật đầu",
    "shake": "Lắc đầu",
    "look_around": "Nhìn quanh",
    "surprised": "Ngạc nhiên",
    "dance": "Nhảy",
}


def gesture_frames(name: str, pan0: float, tilt0: float, pan_limit: float, tilt_limit: float) -> list[Keyframe]:
    """Keyframes for a named gesture, starting and ending at (pan0, tilt0)."""
    rel: list[tuple[float, float, float]]
    if name == "nod":
        rel = [(0, 0, 0)]
        for i in range(3):
            base = 0.15 + i * 0.6
            rel += [(base + 0.15, 0, -14), (base + 0.45, 0, 6)]
        rel.append((2.1, 0, 0))
    elif name == "shake":
        rel = [(0, 0, 0)]
        for i in range(3):
            base = 0.1 + i * 0.7
            rel += [(base + 0.2, 22, 0), (base + 0.55, -22, 0)]
        rel.append((2.4, 0, 0))
    elif name == "look_around":
        rel = [(0, 0, 0), (1.0, 60, 8), (1.8, 60, 8), (3.0, -60, 8), (3.8, -60, 8), (4.6, 0, -6), (5.2, 0, 0)]
    elif name == "surprised":
        rel = [(0, 0, 0), (0.15, 0, 22), (0.9, 0, 22), (1.05, 0, 14), (1.2, 0, 22), (2.2, 0, 0)]
    elif name == "dance":
        rel = [(0, 0, 0)]
        beat = 0.42
        moves = [(25, 10), (-25, -8), (25, -8), (-25, 10), (35, 0), (-35, 0), (0, 15), (0, -10)]
        for rep in range(2):
            for i, (p, t) in enumerate(moves):
                rel.append(((rep * len(moves) + i + 1) * beat, p, t))
        rel.append(((2 * len(moves) + 1) * beat + 0.3, 0, 0))
    else:
        raise ValueError(f"unknown gesture {name!r}")
    # Shift the whole gesture inward if it would run past a limit, so its shape survives
    pans = [pan0 + p for _, p, _ in rel]
    tilts = [tilt0 + t for _, _, t in rel]
    dp = (pan_limit - max(pans)) if max(pans) > pan_limit else (-pan_limit - min(pans)) if min(pans) < -pan_limit else 0.0
    dt = (tilt_limit - max(tilts)) if max(tilts) > tilt_limit else (-tilt_limit - min(tilts)) if min(tilts) < -tilt_limit else 0.0
    frames = [Keyframe(t, _clamp(pan0 + p + dp, pan_limit), _clamp(tilt0 + tl + dt, tilt_limit)) for t, p, tl in rel]
    # Begin and end where the head is
    frames[0] = Keyframe(0.0, pan0, tilt0)
    frames[-1] = Keyframe(frames[-1].t, pan0, tilt0)
    return frames


def sample(frames: list[Keyframe], t: float) -> tuple[float, float]:
    """Linear interpolation between keyframes, smoothed with an ease-in-out."""
    if t <= frames[0].t:
        return frames[0].pan, frames[0].tilt
    for a, b in zip(frames, frames[1:]):
        if t <= b.t:
            span = b.t - a.t
            u = 0.0 if span <= 0 else (t - a.t) / span
            u = 0.5 - 0.5 * math.cos(math.pi * u)
            return a.pan + (b.pan - a.pan) * u, a.tilt + (b.tilt - a.tilt) * u
    return frames[-1].pan, frames[-1].tilt


def validate_frames(raw, pan_limit: float, tilt_limit: float) -> list[Keyframe]:
    """Keyframes from untrusted JSON: sorted, clamped, bounded in count and length."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("motion has no frames")
    frames: list[Keyframe] = []
    for item in raw[:MAX_MOTION_FRAMES]:
        if isinstance(item, dict):
            t, p, tl = item.get("t"), item.get("pan"), item.get("tilt")
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            t, p, tl = item
        else:
            raise ValueError("bad frame")
        values = [float(t), float(p), float(tl)]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("frame value is not a number")
        if values[0] < 0 or values[0] > MAX_MOTION_SECONDS:
            continue
        frames.append(Keyframe(values[0], _clamp(values[1], pan_limit), _clamp(values[2], tilt_limit)))
    if not frames:
        raise ValueError("motion has no usable frames")
    frames.sort(key=lambda f: f.t)
    return frames


# ----- Recording -----

class MotionRecorder:
    """Collects the angles the web panel sends while recording is on."""

    MIN_INTERVAL_S = 0.04

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._start: float | None = None
        self._frames: list[Keyframe] = []

    @property
    def recording(self) -> bool:
        return self._start is not None

    def elapsed(self) -> float:
        with self._lock:
            return 0.0 if self._start is None else self._clock() - self._start

    def start(self, pan: float, tilt: float) -> None:
        with self._lock:
            self._start = self._clock()
            self._frames = [Keyframe(0.0, pan, tilt)]

    def add(self, pan: float, tilt: float) -> None:
        with self._lock:
            if self._start is None:
                return
            t = self._clock() - self._start
            if t > MAX_MOTION_SECONDS or len(self._frames) >= MAX_MOTION_FRAMES:
                return
            if self._frames and t - self._frames[-1].t < self.MIN_INTERVAL_S:
                self._frames[-1] = Keyframe(self._frames[-1].t, pan, tilt)
            else:
                self._frames.append(Keyframe(t, pan, tilt))

    def stop(self) -> list[Keyframe]:
        """End recording; the last pose is held to the moment of stopping."""
        with self._lock:
            if self._start is None:
                return []
            frames = list(self._frames)
            end = min(MAX_MOTION_SECONDS, self._clock() - self._start)
            if frames and end > frames[-1].t:
                frames.append(Keyframe(end, frames[-1].pan, frames[-1].tilt))
            self._start = None
            self._frames = []
            return frames

    def cancel(self) -> None:
        with self._lock:
            self._start = None
            self._frames = []


_NAME_RE = re.compile(r"[^\w\- ]+", re.UNICODE)


def clean_name(name: str) -> str:
    """Motion name usable as a file name: letters (any script), digits, space, - and _."""
    text = _NAME_RE.sub("", str(name or "")).strip()[:MAX_NAME_LENGTH].strip()
    if not text or text in (".", ".."):
        raise ValueError("tên động tác không hợp lệ")
    return text


class MotionLibrary:
    """Recorded motions as JSON files in a directory (one per name)."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, name: str) -> Path:
        return self.directory / f"{clean_name(name)}.json"

    def save(self, name: str, frames: list[Keyframe]) -> str:
        if not frames:
            raise ValueError("động tác trống")
        name = clean_name(name)
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {
            "name": name,
            "version": 1,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration": round(frames[-1].t, 3),
            "frames": [[round(f.t, 3), round(f.pan, 2), round(f.tilt, 2)] for f in frames],
        }
        tmp = self._path(name).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(name))
        return name

    def load(self, name: str, pan_limit: float, tilt_limit: float) -> list[Keyframe]:
        path = self._path(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_frames(data.get("frames"), pan_limit, tilt_limit)

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if path.is_file():
            path.unlink()
            return True
        return False

    def list(self) -> list[dict]:
        if not self.directory.is_dir():
            return []
        items = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                frames = data.get("frames") or []
                items.append(
                    {
                        "name": path.stem,
                        "duration": float(data.get("duration") or (frames[-1][0] if frames else 0)),
                        "frames": len(frames),
                        "created": str(data.get("created", "")),
                    }
                )
            except (OSError, ValueError, TypeError, IndexError, KeyError):
                logger.warning("Skipping unreadable motion file %s", path)
        return items


# ----- Playback -----

class MotionPlayer:
    """Plays keyframes on the controller in a background thread, one motion at a time.

    Each tick re-claims control for the motion's source; if a higher-priority source
    takes over (someone drags the head on the web panel), the motion stops there.
    """

    def __init__(self, controller, arbiter: ControlArbiter, clock: Callable[[], float] = time.monotonic) -> None:
        self._controller = controller
        self._arbiter = arbiter
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._current: str | None = None
        self._source: str | None = None
        self._progress = 0.0
        self._duration = 0.0
        self.on_change: Callable[[], None] | None = None

    @property
    def playing(self) -> str | None:
        return self._current

    def status(self) -> dict:
        return {
            "playing": self._current,
            "source": self._source,
            "progress": round(self._progress, 2),
            "duration": round(self._duration, 2),
        }

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                logger.debug("Motion change callback failed", exc_info=True)

    def play(self, name: str, frames: list[Keyframe], source: str) -> bool:
        """Start a motion (stopping any other). False if source may not move the head."""
        if not frames:
            return False
        duration = frames[-1].t
        if not self._arbiter.request(source, hold=duration + self._arbiter.hold_sec):
            return False
        self.stop()
        with self._lock:
            stop = threading.Event()
            self._stop = stop
            self._current, self._source = name, source
            self._progress, self._duration = 0.0, duration
            self._thread = threading.Thread(
                target=self._run, args=(frames, source, stop), name="head-motion", daemon=True
            )
            self._thread.start()
        logger.info("Head motion %r started (%s, %.1fs)", name, source, duration)
        self._notify()
        return True

    def stop(self) -> None:
        with self._lock:
            thread, self._thread = self._thread, None
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)

    def _run(self, frames: list[Keyframe], source: str, stop: threading.Event) -> None:
        start = self._clock()
        duration = frames[-1].t
        try:
            while not stop.is_set():
                t = self._clock() - start
                if not self._arbiter.request(source, hold=max(0.0, duration - t) + self._arbiter.hold_sec):
                    logger.info("Head motion interrupted by a higher-priority control")
                    break
                pan, tilt = sample(frames, t)
                self._controller.set_angles(pan, tilt)
                self._progress = min(t, duration)
                if t >= duration:
                    break
                stop.wait(PLAYER_TICK_S)
        except Exception:
            logger.error("Head motion failed", exc_info=True)
        finally:
            self._arbiter.release(source)
            with self._lock:
                if self._stop is stop:
                    self._current = None
                    self._source = None
                    self._progress = 0.0
                    self._duration = 0.0
            self._notify()


def start_gesture(player: MotionPlayer, controller, name: str, source: str) -> tuple[bool, str]:
    """Play a named gesture from the head's current target pose."""
    if name not in GESTURE_LABELS:
        return False, f"không có cử chỉ '{name}'"
    s = controller.settings
    pan, tilt = controller.angles
    frames = gesture_frames(name, pan, tilt, s.pan_limit, s.tilt_limit)
    if not player.play(name, frames, source):
        return False, "đầu robot đang được điều khiển từ trang web"
    return True, f"Đang {GESTURE_LABELS[name].lower()}"
