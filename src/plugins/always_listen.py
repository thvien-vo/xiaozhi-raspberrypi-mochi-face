"""Always-listen plugin: keeps the assistant listening whatever stopped it.

Enabled by ``FACE.ALWAYS_LISTEN``. A small supervisor loop checks that the audio channel is open
and the session is in continuous listening (or speaking, which returns to listening by itself).
When it is not - first start, the server closed the session for silence, the WebSocket dropped,
an audio or network error reset the state - it reconnects and starts listening again, waiting
longer after each failed attempt so a dead network is never hammered.

It also logs when the assistant seems to hear its own voice, since software AEC is off on the Pi.
"""

import asyncio
import re
import time
from typing import Any, Optional, Sequence

from src.constants.constants import DeviceState, ListeningMode
from src.logging import get_logger
from src.plugins.base import Plugin

logger = get_logger()

# Seconds to wait before attempt 1, 2, 3, ... (the last value repeats)
DEFAULT_BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0)
# Settle time before the very first attempt after listening is lost (TTS stop etc. recover alone)
DEFAULT_GRACE_S = 0.5
# Listening must hold this long before the backoff resets
DEFAULT_STABLE_S = 10.0
DEFAULT_TICK_S = 0.25

# A transcript this soon after TTS stopped is more likely the robot's own voice than a reply
ECHO_WINDOW_S = 1.0
_ECHO_MIN_CHARS = 4
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _normalize(text: str) -> str:
    return _NON_WORD.sub("", text or "").lower()


class AlwaysListenPlugin(Plugin):
    """Restores continuous listening after any interruption."""

    name = "always_listen"
    priority = 70  # After UI so the viewport is already up

    def __init__(
        self,
        backoff: Sequence[float] = DEFAULT_BACKOFF_S,
        grace_s: float = DEFAULT_GRACE_S,
        stable_s: float = DEFAULT_STABLE_S,
        tick_s: float = DEFAULT_TICK_S,
    ) -> None:
        super().__init__()
        self._backoff = tuple(backoff) or DEFAULT_BACKOFF_S
        self._grace_s = grace_s
        self._stable_s = stable_s
        self._tick_s = tick_s
        self._task: Optional[asyncio.Future] = None
        self._failures = 0
        self._next_attempt = 0.0
        self._lost_since: Optional[float] = None
        self._good_since: Optional[float] = None
        self._tts_stopped_at: Optional[float] = None
        self._recent_tts: list[str] = []
        self.restore_count = 0

    # -------------------------
    # Lifecycle
    # -------------------------
    async def start(self) -> None:
        await super().start()
        if self._task is None:
            self._task = self._cmd.spawn(self._supervise(), name="always_listen:supervisor")
        logger.info("AlwaysListen: supervisor started (FACE.ALWAYS_LISTEN)")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and hasattr(task, "cancel"):
            task.cancel()
        await super().stop()

    # -------------------------
    # Supervisor
    # -------------------------
    def _is_listening_ok(self) -> bool:
        """True when listening is on, or the assistant is talking and will go back to listening."""
        state = self._ctx.get_device_state()
        if state == DeviceState.NOTIFYING:
            return True  # A server notification is playing; it ends in IDLE and we restore then
        if not self._ctx.is_audio_channel_opened():
            return False
        if not self._ctx.is_keep_listening():
            return False
        return state in (DeviceState.LISTENING, DeviceState.SPEAKING)

    def _delay_for(self, failures: int) -> float:
        if failures <= 0:
            return self._grace_s
        return self._backoff[min(failures - 1, len(self._backoff) - 1)]

    async def _supervise(self) -> None:
        while True:
            try:
                await self.tick(time.monotonic())
            except asyncio.CancelledError:
                raise
            except Exception as e:  # Never let the supervisor die
                logger.error(f"AlwaysListen: supervisor error: {e}", exc_info=True)
            await asyncio.sleep(self._tick_s)

    async def tick(self, now: float) -> None:
        """One supervisor step (separate from the loop so tests can drive time)."""
        if self._is_listening_ok():
            self._lost_since = None
            if self._good_since is None:
                self._good_since = now
            elif self._failures and now - self._good_since >= self._stable_s:
                logger.info("AlwaysListen: listening is stable, backoff reset")
                self._failures = 0
            return

        self._good_since = None
        if self._lost_since is None:
            self._lost_since = now
            # First loss waits the grace time; a loss during a backoff keeps its timer
            self._next_attempt = max(self._next_attempt, now + self._delay_for(self._failures))
            logger.info(
                "AlwaysListen: not listening "
                f"(state={self._ctx.get_device_state()}, "
                f"channel={self._ctx.is_audio_channel_opened()}); "
                f"restoring in {self._next_attempt - now:.1f}s"
            )
        if now < self._next_attempt:
            return
        await self._restore(now)

    async def _restore(self, now: float) -> None:
        self._failures += 1
        self.restore_count += 1
        attempt = self._failures
        try:
            if await self._cmd.connect_protocol():
                await self._cmd.start_listening(self._listen_mode())
        except Exception as e:
            logger.warning(f"AlwaysListen: restore attempt {attempt} raised: {e}")
        if self._is_listening_ok():
            logger.info(f"AlwaysListen: listening restored (attempt {attempt})")
            self._lost_since = None
            self._good_since = now
            # Keep self._failures until the listening proves stable, so a session the
            # server drops right away does not reconnect in a tight loop.
            self._next_attempt = now + self._delay_for(self._failures)
        else:
            delay = self._delay_for(self._failures)
            self._next_attempt = now + delay
            logger.warning(
                f"AlwaysListen: restore attempt {attempt} failed, retrying in {delay:.0f}s"
            )

    def _listen_mode(self) -> ListeningMode:
        aec = self._ctx.get_config().get_config("AEC_OPTIONS.ENABLED", True)
        return ListeningMode.REALTIME if aec else ListeningMode.AUTO_STOP

    # -------------------------
    # Self-hearing detection (log only)
    # -------------------------
    async def on_incoming_json(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        now = time.monotonic()
        if kind == "tts":
            state = message.get("state")
            if state == "sentence_start" and message.get("text"):
                self._recent_tts.append(_normalize(message["text"]))
                del self._recent_tts[:-6]
            elif state == "stop":
                self._tts_stopped_at = now
        elif kind == "stt":
            self._check_echo(str(message.get("text", "")), now)

    def _check_echo(self, text: str, now: float) -> None:
        heard = _normalize(text)
        if len(heard) < _ECHO_MIN_CHARS:
            return
        spoken = "".join(self._recent_tts)
        too_soon = (
            self._tts_stopped_at is not None and now - self._tts_stopped_at < ECHO_WINDOW_S
        )
        if heard in spoken or too_soon:
            logger.warning(
                "AlwaysListen: possible self-hearing - the transcript "
                f"{text[:40]!r} {'repeats the last TTS text' if heard in spoken else 'came within 1s of TTS ending'}"
            )
