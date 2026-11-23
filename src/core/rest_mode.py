"""Rest mode ("Nghỉ"): the robot neither listens nor speaks until it is woken from the web.

While resting the conversation session with the server is closed, so no microphone audio
goes out, no speech-to-text turn can start and the server's chat quota is not used. The
state survives a restart (``FACE.RESTING`` in the config). Music, the web panel and manual
head control keep working.

RestController owns the state and the transitions; the parts that need other subsystems
(face, head, tracking) subscribe as listeners (src/plugins/rest.py). It is safe to call
``request`` from any thread: the flag flips at once (the microphone stops right away) and
the slower work runs on the app's event loop.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Callable
from typing import Any

from src.logging import get_logger

logger = get_logger()

REST_CONFIG_KEY = "FACE.RESTING"
# Ignore a second toggle this soon after the first (double clicks, flaky touch)
MIN_INTERVAL_S = 1.0
# Voice "go to sleep": let the spoken answer finish playing before closing the session
AFTER_REPLY_DELAY_S = 2.5
PENDING_TIMEOUT_S = 25.0

SOURCE_WEB = "web"
SOURCE_VOICE = "voice"
SOURCE_BOOT = "khởi động"

Listener = Callable[[bool, str], Any]


class RestController:
    def __init__(
        self,
        config,
        state,
        session=None,
        *,
        clock: Callable[[], float] = time.monotonic,
        min_interval_s: float = MIN_INTERVAL_S,
    ) -> None:
        self._config = config
        self._state = state
        self._session = session
        self._clock = clock
        self._min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._transition_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listeners: list[Listener] = []
        self._last_change = -1e9
        self._pending_timer: asyncio.TimerHandle | None = None
        self.pending_after_reply = False
        self._since: float | None = None
        self.source = ""

        self._resting = bool(config.get_config(REST_CONFIG_KEY, False))
        state.set_resting(self._resting)
        if self._resting:
            self._since = time.time()
            self.source = SOURCE_BOOT
            logger.info("Chế độ NGHỈ: đang nghỉ từ lần chạy trước (FACE.RESTING), không nghe, không nói")

    # ----- Wiring -----

    @property
    def resting(self) -> bool:
        return self._resting

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def add_listener(self, callback: Listener) -> None:
        """callback(resting, source) runs on the app loop after every change (sync or async)."""
        self._listeners.append(callback)

    def snapshot(self) -> dict:
        return {
            "resting": self._resting,
            "since": self._since,
            "source": self.source,
            "pending": self.pending_after_reply,
        }

    # ----- Requests -----

    def _gate(self, resting: bool, source: str) -> dict | None:
        """Flip the flag if allowed. None when a transition must follow, else the answer."""
        with self._lock:
            if resting == self._resting:
                self._cancel_pending()
                return {"ok": True, "message": "Robot đang nghỉ rồi" if resting else "Robot đang thức rồi", "changed": False}
            now = self._clock()
            if now - self._last_change < self._min_interval_s:
                return {"ok": False, "message": "Bấm quá nhanh, thử lại sau một giây", "silent": False}
            self._last_change = now
            self._resting = resting
            self._since = time.time() if resting else None
            self.source = source
            self._cancel_pending()
        # The microphone stops (or may resume) from this moment
        self._state.set_resting(resting)
        return None

    def request(self, resting: bool, source: str = SOURCE_WEB) -> dict:
        """Thread-safe entry for the web panel and tools; returns the answer at once."""
        resting = bool(resting)
        answer = self._gate(resting, source)
        if answer is not None:
            return answer
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("Chế độ NGHỈ: chưa có vòng lặp ứng dụng, chỉ đổi cờ")
            return {"ok": True, "message": "Đã đổi trạng thái", "changed": True}
        asyncio.run_coroutine_threadsafe(self._transition(resting, source), loop)
        return {"ok": True, "message": "Robot đi nghỉ" if resting else "Robot thức dậy", "changed": True}

    async def set_resting(self, resting: bool, source: str = SOURCE_WEB) -> dict:
        """Same as request, for code already on the loop (awaits the whole transition)."""
        resting = bool(resting)
        answer = self._gate(resting, source)
        if answer is not None:
            return answer
        await self._transition(resting, source)
        return {"ok": True, "message": "Robot đi nghỉ" if resting else "Robot thức dậy", "changed": True}

    # ----- "Go to sleep" by voice: after the spoken reply -----

    def request_after_reply(self, source: str = SOURCE_VOICE) -> dict:
        """Rest once the robot has finished answering (the tool's own reply must be heard)."""
        if self._resting:
            return {"ok": True, "message": "Robot đang nghỉ rồi", "changed": False}
        with self._lock:
            self.pending_after_reply = True
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._arm_timeout, source)
        logger.info("Chế độ NGHỈ: sẽ nghỉ sau khi robot nói xong (%s)", source)
        return {"ok": True, "message": "Sẽ đi nghỉ sau khi nói xong", "changed": False}

    def _arm_timeout(self, source: str) -> None:
        if self._pending_timer is not None:
            self._pending_timer.cancel()
        loop = self._loop
        self._pending_timer = loop.call_later(PENDING_TIMEOUT_S, self._fire_pending, source)

    def _cancel_pending(self) -> None:
        self.pending_after_reply = False
        timer, self._pending_timer = self._pending_timer, None
        if timer is not None:
            timer.cancel()

    def _fire_pending(self, source: str = SOURCE_VOICE) -> None:
        if not self.pending_after_reply:
            return
        self.pending_after_reply = False
        self._pending_timer = None
        answer = self.request(True, source)
        if not answer.get("ok"):
            logger.info("Chế độ NGHỈ: bỏ qua yêu cầu nghỉ sau khi nói (%s)", answer.get("message"))

    def note_tts_stop(self) -> None:
        """The server finished a spoken reply (loop thread): carry out a pending rest."""
        if not self.pending_after_reply:
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_later(AFTER_REPLY_DELAY_S, self._fire_pending, SOURCE_VOICE)

    # ----- Transition -----

    async def _transition(self, resting: bool, source: str) -> None:
        async with self._transition_lock:
            if resting != self._resting:
                return  # Toggled back before this ran; the later transition handles it
            logger.info("Chế độ NGHỈ: %s (nguồn: %s)", "VÀO nghỉ" if resting else "RA khỏi nghỉ (thức)", source)
            try:
                self._config.update_config(REST_CONFIG_KEY, resting)
            except Exception as e:
                logger.error("Chế độ NGHỈ: không lưu được vào cấu hình: %s", e, exc_info=True)
            if resting and self._session is not None:
                try:
                    await self._session.enter_rest()
                except Exception as e:
                    logger.error("Chế độ NGHỈ: lỗi khi dừng nghe/nói: %s", e, exc_info=True)
            await self._notify(resting, source)
            logger.info(
                "Chế độ NGHỈ: xong (%s)",
                "đã đóng phiên với máy chủ, không nghe, không nói" if resting else "cho phép nghe lại",
            )

    async def _notify(self, resting: bool, source: str) -> None:
        for callback in list(self._listeners):
            try:
                result = callback(resting, source)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.error("Chế độ NGHỈ: bộ lắng nghe lỗi", exc_info=True)

    async def apply_initial(self) -> None:
        """Start-up while resting: tell the listeners so the face and head follow."""
        if self._resting:
            await self._notify(True, SOURCE_BOOT)
