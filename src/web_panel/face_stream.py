"""Latest snapshot of the robot face, shared between the Qt side and the web server.

The app's FaceSnapshotService publishes small JPEGs here while at least one page
watches; pages subscribe over their WebSocket ({"type": "face_stream", "enabled": true})
and get each new JPEG as a binary message. The first viewer turns snapshots on through
on_active(True), the last one turns them off, so nobody watching costs nothing.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from src.logging import get_logger

logger = get_logger()


class FaceFrameHub:
    def __init__(self, on_active: Callable[[bool], None] | None = None, fps: float = 10.0) -> None:
        self.on_active = on_active
        self.fps = max(0.5, float(fps))
        self.available = on_active is not None  # A source exists (GUI robot face)
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._size = (0, 0)
        self._seq = 0
        self._time = 0.0
        self._viewers = 0

    def publish(self, jpeg: bytes, width: int = 0, height: int = 0) -> None:
        """Called by the snapshot source on any thread."""
        with self._lock:
            self._jpeg = jpeg
            self._size = (width, height)
            self._seq += 1
            self._time = time.monotonic()

    def latest(self) -> tuple[bytes | None, int]:
        with self._lock:
            return self._jpeg, self._seq

    def fresh(self, max_age_s: float = 2.0) -> bool:
        with self._lock:
            return self._jpeg is not None and time.monotonic() - self._time < max_age_s

    @property
    def viewers(self) -> int:
        return self._viewers

    def add_viewer(self) -> None:
        with self._lock:
            self._viewers += 1
            first = self._viewers == 1
        if first:
            self._set_active(True)

    def remove_viewer(self) -> None:
        with self._lock:
            if self._viewers == 0:
                return
            self._viewers -= 1
            last = self._viewers == 0
        if last:
            self._set_active(False)

    def _set_active(self, active: bool) -> None:
        if self.on_active is None:
            return
        try:
            self.on_active(active)
        except Exception:
            logger.debug("Face snapshot on/off failed", exc_info=True)
