"""Drops a whole conversation turn whose transcript is an STT hallucination.

The official server answers one recognized sentence like this, in order on one connection:

    stt (the user's text) -> tts start -> llm (emotion) -> [mcp tools/call] ->
    tts sentence_start ... + binary audio frames ... -> tts stop

The transcript comes first, so the turn can be stopped before anything is shown or heard.
ProtocolTransport asks this gate about every message and every audio frame from the server,
synchronously and in arrival order (see src/core/protocol_manager.py).

Measured on the official server (2026-11-23): "abort" does not cancel the reply. The server
answers it with an immediate ``tts stop`` (before or after ``tts start``) and then still sends
``llm``, the ``tts sentence_start`` texts, the audio and a final ``tts stop``. So no single
message marks the end of the dropped turn, and the gate works like this:

* An ``stt`` the filter (src/audio_processing/stt_filter.py) calls a hallucination is
  swallowed and starts suppression; ``on_drop`` runs (the session sends "abort", clears
  queued speech and keeps the face listening). The microphone is held closed
  (``mic_blocked``) so nothing new is sent while the server is still busy with the reply.
* While suppressing, every ``tts`` and ``llm`` message, every MCP ``tools/call`` (answered
  with an error through ``on_tool_blocked`` so the server does not wait) and every audio
  frame is swallowed. ``quiet_for()`` says how long the swallowed stream has been silent;
  the session reopens the microphone once it has been quiet for a moment (``release_mic``).
* Suppression ends only with the next real ``stt`` (the user spoke), with typed text or a
  greeting (``note_typed``: the user starts a turn), or ``max_suppress_s`` after the drop.
  Messages that straggle in after the microphone reopened are still swallowed that way.
* Audio frames stay blocked until the next delivered ``tts start``, so speech that arrives
  late for the dropped turn is never played.

* A transcript that comes within TAIL_WINDOW_S of a dropped one and is only a run of words
  of it ("Để không bỏ lỡ." after "Hãy subscribe ... để không bỏ lỡ những video hấp dẫn") is
  the leftover of the same hallucination and is dropped too: the server keeps the dropped
  sentence in its history and would answer the fragment from it.
* Outside a dropped turn, each robot sentence (``tts sentence_start``) that repeats
  hallucinated content (SttFilter.check_reply) is swallowed together with its audio frames
  (up to the next sentence); the other sentences of the turn go through. Not done when the
  user's own last sentence mentioned the same thing ("mở nhạc La La School").

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

MAX_SUPPRESS_S = 30.0
TYPED_WINDOW_S = 10.0
TAIL_WINDOW_S = 20.0


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
        self._last_activity = 0.0
        self._audio_blocked = False
        self._mic_blocked = False
        self._turn = 0
        self._typed: list[tuple[float, str]] = []
        self._dropped_texts: list[tuple[float, str]] = []
        self._last_user_text = ""
        self._sentence_blocked = False
        self.dropped_sentences = 0
        self.dropped_turns = 0
        self.dropped_audio_frames = 0
        self.dropped_messages = 0
        # Callbacks (run synchronously on the protocol's thread; they must only schedule work)
        self.on_drop: Callable[[int, str, Any], None] | None = None
        self.on_tool_blocked: Callable[[dict], None] | None = None
        self.on_reply_blocked: Callable[[str, Any], None] | None = None

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

    def quiet_for(self) -> float | None:
        """Seconds since the last swallowed message or frame (None when not suppressing)."""
        with self._lock:
            if not self._suppressing:
                return None
            return self._clock() - self._last_activity

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
            self._sentence_blocked = False

    def note_typed(self, text: str) -> None:
        """Typed text or the greeting goes out: the user starts a new turn."""
        now = self._clock()
        with self._lock:
            self._typed = [(t, s) for t, s in self._typed if now - t < TYPED_WINDOW_S]
            self._typed.append((now, _plain(text)))
            if self._suppressing:
                logger.info("STT filter: text sent by the user, the dropped turn is over")
            self._suppressing = False
            self._mic_blocked = False

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
        drop = None
        blocked_tool = None
        reply_blocked = None
        deliver = True
        with self._lock:
            if self._suppressing and now - self._suppress_since > self._max_suppress_s:
                logger.info("STT filter: dropped turn over (time limit)")
                self._suppressing = False
                self._mic_blocked = False
            if kind == "stt":
                text = str(message.get("text") or "")
                result = None
                if not self._was_typed_locked(text, now):
                    result = self._filter.check(text)
                    if not result.ignored:
                        self._dropped_texts = [
                            (t, s) for t, s in self._dropped_texts if now - t < TAIL_WINDOW_S
                        ]
                        if self._dropped_texts:
                            result = self._filter.check_tail(text, [s for _, s in self._dropped_texts])
                if result is not None and result.ignored:
                    self._turn += 1
                    self._suppressing = True
                    self._suppress_since = now
                    self._last_activity = now
                    self._audio_blocked = True
                    self._mic_blocked = True
                    self.dropped_turns += 1
                    self._sentence_blocked = False
                    self._dropped_texts.append((now, text))
                    drop = (self._turn, text, result)
                elif self._suppressing:
                    logger.info("STT filter: a real sentence arrived, the dropped turn is over")
                    self._suppressing = False
                    self._mic_blocked = False
                if drop is None:
                    self._last_user_text = text
            elif self._suppressing:
                swallow = kind in ("tts", "llm")
                if kind == "mcp":
                    payload = message.get("payload")
                    if isinstance(payload, dict) and payload.get("method") == "tools/call":
                        swallow = True
                        blocked_tool = payload
                if swallow:
                    self.dropped_messages += 1
                    self._last_activity = now
                deliver = not swallow
            elif kind == "tts":
                state = message.get("state")
                if state == "start":
                    self._audio_blocked = False
                    self._sentence_blocked = False
                elif state == "stop":
                    self._sentence_blocked = False
                elif state == "sentence_start":
                    sentence = str(message.get("text") or "")
                    matched = self._filter.check_reply(sentence)
                    if matched and set(matched) <= set(self._filter.check_reply(self._last_user_text)):
                        matched = ()  # The user asked for it
                    if matched:
                        self._sentence_blocked = True
                        self.dropped_sentences += 1
                        reply_blocked = (sentence, matched)
                        deliver = False
                    else:
                        self._sentence_blocked = False
                elif state == "sentence_end" and self._sentence_blocked:
                    deliver = False
        if drop is not None:
            turn, text, result = drop
            logger.info(f"STT filter: ignored {text!r} (matched: {result.label})")
            self._filter.record_ignored(text, result)
            self._call(self.on_drop, turn, text, result)
            return False
        if reply_blocked is not None:
            sentence, matched = reply_blocked
            logger.info(f"STT filter: robot sentence not spoken {sentence!r} (matched: {', '.join(matched)})")
            self._filter.record_reply_blocked(sentence, matched)
            self._call(self.on_reply_blocked, sentence, matched)
        if blocked_tool is not None:
            logger.info(
                "STT filter: blocked tool call %r of a dropped turn",
                (blocked_tool.get("params") or {}).get("name"),
            )
            self._call(self.on_tool_blocked, blocked_tool)
        return deliver

    def filter_audio(self) -> bool:
        """True to play the server's audio frame."""
        with self._lock:
            if self._suppressing or self._audio_blocked or self._sentence_blocked:
                self.dropped_audio_frames += 1
                if self._suppressing:
                    self._last_activity = self._clock()
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
                "mic_blocked": self._mic_blocked,
                "dropped_turns": self.dropped_turns,
                "dropped_audio_frames": self.dropped_audio_frames,
                "dropped_messages": self.dropped_messages,
                "dropped_sentences": self.dropped_sentences,
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
