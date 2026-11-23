"""Drops a whole conversation turn whose transcript is an STT hallucination.

The official server answers one recognized sentence like this, in order on one connection:

    stt (the user's text) -> tts start -> llm (emotion) -> [mcp tools/call] ->
    tts sentence_start ... + binary audio frames ... -> tts stop

The transcript comes first, so the turn can be stopped before anything is shown or heard.
ProtocolTransport asks this gate about every message and every audio frame from the server,
synchronously and in arrival order (see src/core/protocol_manager.py):

* An ``stt`` the filter (src/audio_processing/stt_filter.py) calls a hallucination is
  swallowed and starts suppression; ``on_drop`` runs (the session sends "abort" to the
  server, clears queued speech and keeps the face listening).
* While suppressing, ``tts``, ``llm`` and MCP ``tools/call`` messages are swallowed (a tool
  call gets an error answer through ``on_tool_blocked`` so the server does not wait for it),
  and so is every audio frame. The microphone is held closed too (``mic_blocked``).
* The swallowed ``tts stop`` ends the turn and runs ``on_resume`` (the session reopens the
  listening). Audio frames stay blocked until the next turn's ``tts start``, so speech that
  arrives late for the dropped turn is never played.
* A real ``stt`` during suppression (the user spoke) ends it at once and is delivered.
  Suppression also ends by itself after ``max_suppress_s``, for a server that never sends
  ``tts stop`` after an abort (the session also has its own resume fallback).

Typed text (web panel, greeting) is sent as a wake-word "detect"; a transcript equal to a
text sent in the last few seconds is never filtered.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from src.logging import get_logger

logger = get_logger()

MAX_SUPPRESS_S = 20.0
TYPED_WINDOW_S = 10.0


class SttGate:
    def __init__(
        self,
        stt_filter,
        clock: Callable[[], float] = time.monotonic,
        max_suppress_s: float = MAX_SUPPRESS_S,
    ) -> None:
        self._filter = stt_filter
        self._clock = clock
        self._max_suppress_s = max_suppress_s
        self._lock = threading.Lock()
        self._suppressing = False
        self._suppress_since = 0.0
        self._audio_blocked = False
        self._mic_blocked = False
        self._turn = 0
        self._typed: list[tuple[float, str]] = []
        self.dropped_turns = 0
        self.dropped_audio_frames = 0
        self.dropped_messages = 0
        # Callbacks (run synchronously on the protocol's thread; they must only schedule work)
        self.on_drop: Callable[[int, str, Any], None] | None = None
        self.on_resume: Callable[[int], None] | None = None
        self.on_tool_blocked: Callable[[dict], None] | None = None

    # ----- State -----

    @property
    def suppressing(self) -> bool:
        with self._lock:
            return self._suppressing

    @property
    def mic_blocked(self) -> bool:
        """True from a dropped transcript until the session has reopened listening."""
        with self._lock:
            return self._mic_blocked

    @property
    def turn(self) -> int:
        with self._lock:
            return self._turn

    def release_mic(self, turn: int | None = None) -> None:
        with self._lock:
            if turn is None or turn == self._turn:
                self._mic_blocked = False

    def reset(self) -> None:
        """Connection closed: nothing of the old turn can arrive any more."""
        with self._lock:
            self._suppressing = False
            self._audio_blocked = False
            self._mic_blocked = False

    def note_typed(self, text: str) -> None:
        now = self._clock()
        with self._lock:
            self._typed = [(t, s) for t, s in self._typed if now - t < TYPED_WINDOW_S]
            self._typed.append((now, _plain(text)))

    def _was_typed_locked(self, text: str, now: float) -> bool:
        plain = _plain(text)
        return any(now - t < TYPED_WINDOW_S and s == plain for t, s in self._typed)

    # ----- Filtering (called by ProtocolTransport) -----

    def filter_json(self, message: Any) -> bool:
        """True to deliver the message, False to swallow it."""
        if not isinstance(message, dict):
            return True
        kind = message.get("type")
        now = self._clock()
        resume_turn = None
        drop = None
        blocked_tool = None
        deliver = True
        with self._lock:
            if self._suppressing and now - self._suppress_since > self._max_suppress_s:
                logger.warning("STT filter: no end of the dropped turn from the server, stopping the drop")
                self._suppressing = False
                resume_turn = self._turn
            if kind == "stt":
                text = str(message.get("text") or "")
                result = None
                if not self._was_typed_locked(text, now):
                    result = self._filter.check(text)
                if result is not None and result.ignored:
                    self._turn += 1
                    self._suppressing = True
                    self._suppress_since = now
                    self._audio_blocked = True
                    self._mic_blocked = True
                    self.dropped_turns += 1
                    drop = (self._turn, text, result)
                    resume_turn = None
                else:
                    if self._suppressing:
                        logger.info("STT filter: a real sentence arrived, the dropped turn is over")
                        self._suppressing = False
                        self._mic_blocked = False
            elif self._suppressing:
                swallow = False
                if kind in ("tts", "llm"):
                    swallow = True
                    if kind == "tts" and message.get("state") == "stop":
                        self._suppressing = False
                        resume_turn = self._turn
                elif kind == "mcp":
                    payload = message.get("payload")
                    if isinstance(payload, dict) and payload.get("method") == "tools/call":
                        swallow = True
                        blocked_tool = payload
                if swallow:
                    self.dropped_messages += 1
                deliver = not swallow
            elif kind == "tts" and message.get("state") == "start":
                self._audio_blocked = False
        if drop is not None:
            turn, text, result = drop
            logger.info(f"STT filter: ignored {text!r} (matched: {result.label})")
            self._filter.record_ignored(text, result)
            self._call(self.on_drop, turn, text, result)
            return False
        if blocked_tool is not None:
            logger.info(
                "STT filter: blocked tool call %r of a dropped turn",
                (blocked_tool.get("params") or {}).get("name"),
            )
            self._call(self.on_tool_blocked, blocked_tool)
        if resume_turn is not None:
            self._call(self.on_resume, resume_turn)
        return deliver

    def filter_audio(self) -> bool:
        """True to play the server's audio frame."""
        with self._lock:
            if self._suppressing or self._audio_blocked:
                self.dropped_audio_frames += 1
                return False
            return True

    @staticmethod
    def _call(callback, *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            logger.error("STT filter callback failed", exc_info=True)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "suppressing": self._suppressing,
                "dropped_turns": self.dropped_turns,
                "dropped_audio_frames": self.dropped_audio_frames,
                "dropped_messages": self.dropped_messages,
            }


def _plain(text: str) -> str:
    from src.audio_processing.stt_filter import fold_words

    return "".join(fold_words(text or ""))


_shared: SttGate | None = None
_shared_lock = threading.Lock()


def get_stt_gate(config=None) -> SttGate:
    """The process-wide gate (one server connection per app)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            from src.audio_processing.stt_filter import get_stt_filter

            _shared = SttGate(get_stt_filter(config))
        return _shared


def reset_stt_gate() -> None:
    """Forget the shared gate (tests only)."""
    global _shared
    with _shared_lock:
        _shared = None
