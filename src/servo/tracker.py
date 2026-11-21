"""Turn the head toward the largest person the YOLO detector sees.

Reads the detector's latest result (no extra inference, no extra frame copies) and nudges
the servo controller with a proportional step, a dead band, a step limit and a moving
average of the offset. The camera sits on the head, so the loop is closed: every step
moves the person toward the frame center. Without a person for LOST_TIMEOUT_SEC the head
drifts back to center.

Detections come in the coordinates of the published frame, which CAMERA.flip may have
mirrored for display; boxes are mapped back to the camera's own image here.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from src.logging import get_logger
from src.servo.settings import ServoSettings

logger = get_logger()

TRACK_LABEL = "person"
# Aim at the head, not the middle of the body
HEAD_HEIGHT_FRACTION = 0.2
# A result older than this describes where the head used to point
MAX_RESULT_AGE_S = 1.0
RETURN_DEG_PER_S = 10.0
_TICK_S = 0.05


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def target_offset(result, flip: str = "none") -> tuple[float, float] | None:
    """Normalized offset (-1..1) of the largest person from the camera's frame center.

    x > 0: person is right of center in the unmirrored camera image; y > 0: below center.
    """
    best = None
    best_area = 0.0
    for det in result.detections:
        if det.label != TRACK_LABEL:
            continue
        x1, y1, x2, y2 = det.box
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area > best_area:
            best, best_area = det, area
    if best is None or result.frame_width <= 0 or result.frame_height <= 0:
        return None
    x1, y1, x2, y2 = best.box
    half_w, half_h = result.frame_width / 2, result.frame_height / 2
    x = ((x1 + x2) / 2 - half_w) / half_w
    y = ((y1 + HEAD_HEIGHT_FRACTION * (y2 - y1)) - half_h) / half_h
    if flip in ("horizontal", "both"):
        x = -x
    if flip in ("vertical", "both"):
        y = -y
    return max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y))


class HeadTracker:
    def __init__(
        self,
        controller,
        latest_result: Callable[[], object],
        settings: ServoSettings,
        flip: str | Callable[[], str] = "none",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._controller = controller
        self._latest = latest_result
        self.settings = settings
        self._flip = flip
        self._clock = clock
        self._enabled = settings.tracking_enabled
        self._hold_until = 0.0
        self._last_seen = clock()
        self._last_result_ts: float | None = None
        self._last_tick = clock()
        self._ema: tuple[float, float] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_enable: Callable[[], None] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def holding(self) -> bool:
        return self._clock() < self._hold_until

    def set_on_enable(self, callback: Callable[[], None] | None) -> None:
        """Called when tracking gets switched on (to make sure detection runs)."""
        self._on_enable = callback

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._ema = None
        self._hold_until = 0.0
        self._last_seen = self._clock()
        logger.info("Head tracking %s", "on" if enabled else "off")
        if enabled and self._on_enable is not None:
            try:
                self._on_enable()
            except Exception as e:
                logger.warning("Could not start detection for tracking: %s", e)

    def hold(self, seconds: float | None = None) -> None:
        """Leave the head where a manual command put it for a while."""
        seconds = self.settings.manual_hold_sec if seconds is None else seconds
        now = self._clock()
        self._hold_until = now + max(0.0, seconds)
        # Count the lost-target timeout from the end of the hold
        self._last_seen = self._hold_until
        self._ema = None

    # ----- Control step (separate from the thread so tests can drive it) -----

    def tick(self) -> None:
        now = self._clock()
        dt = min(0.5, max(0.0, now - self._last_tick))
        self._last_tick = now
        if not self._enabled or now < self._hold_until:
            return

        result = self._latest()
        if result is not None and now - result.timestamp <= MAX_RESULT_AGE_S:
            if result.timestamp == self._last_result_ts:
                return  # Already acted on this one; wait for the next inference
            self._last_result_ts = result.timestamp
            flip = self._flip() if callable(self._flip) else self._flip
            offset = target_offset(result, flip)
            if offset is not None:
                self._last_seen = now
                self._follow(offset)
                return

        if now - self._last_seen >= self.settings.lost_timeout_sec:
            self._ema = None
            self._drift_to_center(dt)

    def _follow(self, offset: tuple[float, float]) -> None:
        s = self.settings
        a = s.ema_alpha
        if self._ema is None:
            self._ema = offset
        else:
            self._ema = (
                a * offset[0] + (1 - a) * self._ema[0],
                a * offset[1] + (1 - a) * self._ema[1],
            )
        ex, ey = self._ema
        # Person right of center -> turn right (pan -); below center -> look down (tilt -)
        dpan = 0.0 if abs(ex) < s.deadband else _clamp(-s.kp_pan * ex, s.max_step_deg)
        dtilt = 0.0 if abs(ey) < s.deadband else _clamp(-s.kp_tilt * ey, s.max_step_deg)
        if dpan or dtilt:
            self._controller.nudge(dpan, dtilt)

    def _drift_to_center(self, dt: float) -> None:
        pan, tilt = self._controller.angles
        if pan == 0.0 and tilt == 0.0:
            return
        step = RETURN_DEG_PER_S * dt
        new_pan = 0.0 if abs(pan) <= step else pan - step * (1 if pan > 0 else -1)
        new_tilt = 0.0 if abs(tilt) <= step else tilt - step * (1 if tilt > 0 else -1)
        self._controller.set_angles(new_pan, new_tilt)

    # ----- Thread -----

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._last_tick = self._clock()
        self._last_seen = self._clock()
        self._thread = threading.Thread(target=self._run, name="head-tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.error("Head tracking step failed", exc_info=True)
                self._stop.wait(1.0)
            self._stop.wait(_TICK_S)
