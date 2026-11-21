"""Always-listen plugin: keeps the assistant listening whatever stopped it.

Enabled by ``FACE.ALWAYS_LISTEN``. A small supervisor loop checks that the audio channel is open
and the session is in continuous listening (or speaking, which returns to listening by itself).
When it is not - first start, the server closed the session for silence, the WebSocket dropped,
an audio or network error reset the state - it reconnects and starts listening again, waiting
longer after each failed attempt so a dead network is never hammered.

With ``FACE.GREET_ON_START`` it first waits (bounded) for the audio device, connects, and has the
assistant greet out loud - once per launch and at most once per ``GREET_MIN_INTERVAL_S`` across
restarts. The microphone opens only after the greeting's TTS ends.

It also logs when the assistant seems to hear its own voice, since software AEC is off on the Pi.
"""

import asyncio
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
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

# Startup: how long to wait for the audio device, and how long to keep trying to greet
DEFAULT_STARTUP_WAIT_S = 90.0
DEFAULT_GREET_GIVE_UP_S = 120.0
# After the greeting is sent: wait this long for the server to start speaking, then this
# long for the speech to end, before carrying on with normal listening
GREET_NO_SPEECH_S = 10.0
GREET_TIMEOUT_S = 60.0
DEFAULT_GREET_TEXT = "Xin chào"
DEFAULT_GREET_MIN_INTERVAL_S = 300.0

# A transcript this soon after TTS stopped is more likely the robot's own voice than a reply
ECHO_WINDOW_S = 1.0
_ECHO_MIN_CHARS = 4
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def default_greet_state_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return Path(base) / "xiaozhi" / "last-greeting"


def _normalize(text: str) -> str:
    return _NON_WORD.sub("", text or "").lower()


class AlwaysListenPlugin(Plugin):
    """Restores continuous listening after any interruption, and greets once at startup."""

    name = "always_listen"
    priority = 70  # After UI so the viewport is already up

    def __init__(
        self,
        backoff: Sequence[float] = DEFAULT_BACKOFF_S,
        grace_s: float = DEFAULT_GRACE_S,
        stable_s: float = DEFAULT_STABLE_S,
        tick_s: float = DEFAULT_TICK_S,
        *,
        greet: bool = False,
        greet_text: str = DEFAULT_GREET_TEXT,
        greet_min_interval_s: float = DEFAULT_GREET_MIN_INTERVAL_S,
        greet_state_file: Optional[Path] = None,
        audio_ready: Optional[Callable[[], bool]] = None,
        startup_wait_s: float = DEFAULT_STARTUP_WAIT_S,
        greet_give_up_s: float = DEFAULT_GREET_GIVE_UP_S,
        clock: Callable[[], float] = time.time,
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
        # Greeting
        self._greet = greet
        self._greet_text = greet_text or DEFAULT_GREET_TEXT
        self._greet_min_interval_s = greet_min_interval_s
        self._greet_state_file = greet_state_file or default_greet_state_file()
        self._audio_ready = audio_ready
        self._startup_wait_s = startup_wait_s
        self._greet_give_up_s = greet_give_up_s
        self._clock = clock
        self._startup_done = False
        self._started_at: Optional[float] = None
        self._last_wait_log = -1e9
        self._startup_failures = 0
        self._startup_next = 0.0
        self._greet_sent_at: Optional[float] = None
        self._greet_spoke = False
        self._seen_connected = False
        self.greet_count = 0

    @classmethod
    def from_config(cls, config: Any, audio_plugin: Any = None) -> "AlwaysListenPlugin":
        """Build from FACE.* settings; audio_plugin supplies the 'audio device is open' check."""
        get = config.get_config
        ready = (lambda: bool(audio_plugin.audio_ready)) if audio_plugin is not None else None
        return cls(
            greet=bool(get("FACE.GREET_ON_START", False)),
            greet_text=str(get("FACE.GREET_TEXT", DEFAULT_GREET_TEXT) or DEFAULT_GREET_TEXT),
            greet_min_interval_s=float(
                get("FACE.GREET_MIN_INTERVAL_S", DEFAULT_GREET_MIN_INTERVAL_S)
            ),
            audio_ready=ready,
        )

    # -------------------------
    # Lifecycle
    # -------------------------
    async def start(self) -> None:
        await super().start()
        if self._task is None:
            self._task = self._cmd.spawn(self._supervise(), name="always_listen:supervisor")
        logger.info(
            "AlwaysListen: supervisor started "
            f"(FACE.ALWAYS_LISTEN, greet_on_start={self._greet})"
        )

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
        if not self._startup_done:
            await self._startup_step(now)
            if not self._startup_done:
                return
        if self._greet_sent_at is not None and self._greeting_in_progress(now):
            return
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

    # -------------------------
    # Startup greeting
    # -------------------------
    def _greet_allowed(self) -> bool:
        try:
            last = float(self._greet_state_file.read_text().strip())
        except (OSError, ValueError):
            return True
        age = self._clock() - last
        if age < 0:  # The clock was set back (no RTC): do not stay silent forever
            return True
        return age >= self._greet_min_interval_s

    def _record_greeting(self) -> None:
        try:
            self._greet_state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._greet_state_file.with_suffix(".tmp")
            tmp.write_text(f"{self._clock():.0f}\n")
            os.replace(tmp, self._greet_state_file)
        except OSError as e:
            logger.warning(f"AlwaysListen: could not record the greeting time: {e}")

    async def _startup_step(self, now: float) -> None:
        if self._started_at is None:
            self._started_at = now
            logger.info("AlwaysListen: startup - waiting for the audio device and the server")
        waited = now - self._started_at

        if self._audio_ready is not None and not self._audio_ready():
            if waited < self._startup_wait_s:
                if now - self._last_wait_log >= 5.0:
                    self._last_wait_log = now
                    logger.info(f"AlwaysListen: waiting for the audio device ({waited:.0f}s)")
                return
            logger.warning(
                f"AlwaysListen: audio device not ready after {waited:.0f}s, "
                "skipping the greeting and carrying on"
            )
            self._startup_done = True
            return

        if not self._greet:
            self._startup_done = True
            return
        if not self._greet_allowed():
            logger.info(
                "AlwaysListen: greeting skipped (already greeted within "
                f"{self._greet_min_interval_s:.0f}s)"
            )
            self._startup_done = True
            return
        if waited >= self._greet_give_up_s:
            logger.warning(f"AlwaysListen: could not greet within {waited:.0f}s, giving up")
            self._startup_done = True
            return
        if now < self._startup_next:
            return

        logger.info(
            f"AlwaysListen: connecting to the server to greet (attempt {self._startup_failures + 1})"
        )
        try:
            sent = await self._cmd.begin_greeting(self._listen_mode(), self._greet_text)
        except Exception as e:
            logger.warning(f"AlwaysListen: greeting attempt raised: {e}")
            sent = False
        if not sent:
            self._startup_failures += 1
            delay = self._delay_for(self._startup_failures)
            self._startup_next = now + delay
            logger.warning(f"AlwaysListen: server not reachable, retrying in {delay:.0f}s")
            return

        self._record_greeting()
        self.greet_count += 1
        self._startup_done = True
        self._greet_sent_at = now
        self._greet_spoke = False
        logger.info(f"AlwaysListen: greeting sent: {self._greet_text!r}; microphone opens after it")

    def _greeting_in_progress(self, now: float) -> bool:
        """True while the greeting is being spoken; ends it (and returns False) otherwise."""
        state = self._ctx.get_device_state()
        if state == DeviceState.SPEAKING and not self._greet_spoke:
            self._greet_spoke = True
            logger.info("AlwaysListen: AI is speaking the greeting")
        if state == DeviceState.LISTENING:
            logger.info("AlwaysListen: greeting finished, now listening")
            self._greet_sent_at = None
            self._lost_since = None
            self._good_since = now
            return False
        if not self._ctx.is_audio_channel_opened():
            logger.warning("AlwaysListen: connection lost during the greeting")
            self._greet_sent_at = None
            return False
        limit = GREET_TIMEOUT_S if self._greet_spoke else GREET_NO_SPEECH_S
        if now - self._greet_sent_at >= limit:
            reason = "did not finish" if self._greet_spoke else "was not answered by the server"
            logger.warning(f"AlwaysListen: greeting {reason}; starting to listen anyway")
            self._greet_sent_at = None
            return False
        return True

    # -------------------------
    # Restore
    # -------------------------
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
    # Logging: one line per state change
    # -------------------------
    async def on_device_state_changed(self, state: Any) -> None:
        logger.info(f"AlwaysListen: state -> {getattr(state, 'value', state)}")

    async def on_protocol_connected(self, protocol: Any) -> None:
        if self._seen_connected:
            logger.info("AlwaysListen: server reconnected (no greeting on reconnect)")
        else:
            logger.info("AlwaysListen: server connected")
        self._seen_connected = True

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
