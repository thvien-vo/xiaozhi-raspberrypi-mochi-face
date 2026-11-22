"""Head dancing to the music: small sways and nods on the beat.

Runs in its own thread while enabled. It claims the head as the "music" source, above
person tracking and below voice commands and the web panel, so dragging the head or a
spoken command always wins; dancing resumes once they let go. Moves are small (a fraction
of the configured limits) and slow enough for the servos.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

from src.logging import get_logger
from src.servo.motion import SOURCE_MUSIC

logger = get_logger()

TICK_S = 0.05
CLAIM_S = 0.8
PAN_AMP, TILT_AMP = 16.0, 7.0
MAX_SHARE = 0.4  # Of the configured limit
EASE_IN_S = 1.5


def dance_bpm(bpm: float) -> float:
    """Halve or double into a range the head can follow."""
    bpm = bpm if math.isfinite(bpm) and bpm > 0 else 100.0
    while bpm >= 125:
        bpm /= 2
    while bpm < 62.5:
        bpm *= 2
    return bpm


def dance_offset(beat: float) -> tuple[float, float]:
    """(pan, tilt) in -1..1 at a beat position; the move changes every 8 beats."""
    figure = int(beat // 8) % 4
    nod = -math.cos(2 * math.pi * beat)  # Down on the beat
    sway = math.sin(math.pi * beat)
    if figure == 0:
        return 0.9 * sway, 0.7 * nod
    if figure == 1:
        return 0.35 * sway, nod
    if figure == 2:
        return sway, 0.6 * math.sin(math.pi * beat / 2)
    return 0.6 * math.sin(math.pi * beat / 2), 0.8 * nod


class MusicDancer:
    """playing(): (position_s, bpm, beat_offset_s) while music sounds, else None."""

    def __init__(
        self,
        controller,
        arbiter,
        playing: Callable[[], tuple[float, float, float] | None],
        player=None,
        enabled: bool = False,
    ) -> None:
        self._controller = controller
        self._arbiter = arbiter
        self._playing = playing
        self._player = player  # MotionPlayer: a gesture in progress is left alone
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.dancing = False
        self.enabled = False
        if enabled:
            self.set_enabled(True)

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            if on == self.enabled:
                return
            self.enabled = on
            if on:
                self._stop = threading.Event()
                self._thread = threading.Thread(target=self._run, args=(self._stop,), name="music-dance", daemon=True)
                self._thread.start()
            else:
                self._stop.set()
        logger.info("Music dance %s", "on" if on else "off")

    def shutdown(self) -> None:
        self.set_enabled(False)
        if self._thread is not None:
            self._thread.join(1.0)

    def target(self, base: tuple[float, float], beat: float, ease: float = 1.0) -> tuple[float, float]:
        """Head angles for a beat, kept inside a narrowed range around the base pose."""
        s = self._controller.settings
        result = []
        for centre, offset, amp, limit in zip(
            base, dance_offset(beat), (PAN_AMP, TILT_AMP), (s.pan_limit, s.tilt_limit)
        ):
            amp = min(amp, MAX_SHARE * limit)
            inner = max(-(limit - amp), min(limit - amp, centre))
            # Ease from the pose itself, so a head at its limit does not jump inward
            result.append(max(-limit, min(limit, centre + ease * (inner - centre + amp * offset))))
        return result[0], result[1]

    def _run(self, stop: threading.Event) -> None:
        base: tuple[float, float] | None = None
        started = 0.0
        while not stop.is_set():
            try:
                now = self._playing()
                busy = self._player is not None and bool(self._player.playing)
                if now is None or busy or not self._arbiter.request(SOURCE_MUSIC, hold=CLAIM_S):
                    if base is not None and now is None and not busy:
                        self._rest(base)
                    base, self.dancing = None, False
                    stop.wait(0.2)
                    continue
                if base is None:
                    base, started = self._controller.angles, time.monotonic()
                position, bpm, offset = now
                beat = max(0.0, position - offset) * dance_bpm(bpm) / 60.0
                ease = min(1.0, (time.monotonic() - started) / EASE_IN_S)
                self._controller.set_angles(*self.target(base, beat, ease))
                self.dancing = True
            except Exception:
                logger.error("Music dance step failed", exc_info=True)
                stop.wait(1.0)
            stop.wait(TICK_S)
        if base is not None:
            self._rest(base)
        self.dancing = False

    def _rest(self, base: tuple[float, float]) -> None:
        """Back to where the head was before dancing, unless someone else has it now."""
        if self._arbiter.request(SOURCE_MUSIC, hold=0.2):
            self._controller.set_angles(*base)
