"""Robot face state machine (no Qt dependency, driven by explicit calls and tick())."""

import time
from collections.abc import Callable

VISUAL_IDLE = "idle"
VISUAL_LISTENING = "listening"
VISUAL_THINKING = "thinking"
VISUAL_SPEAKING = "speaking"
VISUAL_NOTIFYING = "notifying"

ACTIVITY_AWAKE = "awake"
ACTIVITY_SLEEPY = "sleepy"
ACTIVITY_ASLEEP = "asleep"

_DEVICE_STATES = {
    "idle": VISUAL_IDLE,
    "listening": VISUAL_LISTENING,
    "speaking": VISUAL_SPEAKING,
    "notifying": VISUAL_NOTIFYING,
}


class FaceStateMachine:
    """Tracks what the face should express.

    ``thinking`` is not a device state: the device stays in LISTENING between the
    final recognized sentence and the first TTS frame, so it is derived here.
    Inactivity only accumulates while idle; any interaction wakes the face.
    """

    def __init__(
        self,
        sleepy_after_s: float = 90,
        asleep_after_s: float = 240,
        thinking_timeout_s: float = 15,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sleepy_after_s = sleepy_after_s
        self._asleep_after_s = asleep_after_s
        self._thinking_timeout_s = thinking_timeout_s
        self._clock = clock
        self._device_state = VISUAL_IDLE
        self._thinking_since: float | None = None
        self._last_activity = clock()
        self._activity = ACTIVITY_AWAKE
        self._resting = False

    @property
    def resting(self) -> bool:
        return self._resting

    def set_resting(self, resting: bool) -> None:
        """Rest mode: asleep (eyes closed, zzz) until woken; touches and sounds do not wake it."""
        self._resting = bool(resting)
        self._thinking_since = None
        if self._resting:
            self._activity = ACTIVITY_ASLEEP
        else:
            self._last_activity = self._clock()
            self._activity = ACTIVITY_AWAKE

    @property
    def activity(self) -> str:
        return self._activity

    @property
    def visual_state(self) -> str:
        return VISUAL_THINKING if self._thinking_since is not None else self._device_state

    def set_device_state(self, state: str) -> None:
        self._device_state = _DEVICE_STATES.get(str(state).lower(), VISUAL_IDLE)
        self._thinking_since = None
        self.touch()

    def on_user_text(self) -> None:
        """Final recognized user sentence received: wait for the reply."""
        if self._device_state == VISUAL_LISTENING and not self._resting:
            self._thinking_since = self._clock()
        self.touch()

    def touch(self) -> None:
        """Record an interaction and wake up (not while resting)."""
        if self._resting:
            return
        self._last_activity = self._clock()
        self._activity = ACTIVITY_AWAKE

    def tick(self) -> None:
        """Advance time-based transitions."""
        if self._resting:
            self._thinking_since = None
            self._activity = ACTIVITY_ASLEEP
            return
        now = self._clock()
        if (
            self._thinking_since is not None
            and now - self._thinking_since >= self._thinking_timeout_s
        ):
            self._thinking_since = None

        if self._device_state != VISUAL_IDLE or self._thinking_since is not None:
            self._last_activity = now
            self._activity = ACTIVITY_AWAKE
            return

        idle_for = now - self._last_activity
        if 0 < self._asleep_after_s <= idle_for:
            self._activity = ACTIVITY_ASLEEP
        elif 0 < self._sleepy_after_s <= idle_for:
            self._activity = ACTIVITY_SLEEPY
