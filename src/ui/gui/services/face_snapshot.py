"""Small JPEG snapshots of the robot face for the web panel's mirror of the screen.

QQuickItem.grabToImage() renders the face item again into an image of the requested
size, so the cost scales with the small target (about 320x135), not with the screen,
and the face's own frame is not touched. Grabs only run while set_active(True) (someone
has the web page open), at most `fps` a second, one at a time; a grab that never
completes (window hidden) is dropped after a second.

All methods run on the Qt thread; the sink gets (jpeg_bytes, width, height) there and
must return quickly.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QBuffer, QIODevice, QObject, QSize, Qt, QTimer, Slot
from PySide6.QtGui import QImage, QPainter

from src.logging import get_logger

logger = get_logger()

ITEM_NAME = "robotFace"


class FaceSnapshotService(QObject):
    def __init__(
        self,
        find_item: Callable[[], object | None],
        sink: Callable[[bytes, int, int], None],
        fps: float = 10.0,
        width: int = 320,
        quality: int = 80,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._find_item = find_item
        self._sink = sink
        self._width = max(64, int(width))
        self._quality = max(10, min(95, int(quality)))
        self._pending = None
        self._pending_since = 0.0
        self._failures = 0
        self.frames = 0
        self._timer = QTimer(self)
        self._timer.setInterval(max(40, int(1000 / max(0.5, fps))))
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(self._grab)

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    @Slot(bool)
    def set_active(self, active: bool) -> None:
        if active and not self._timer.isActive():
            self._timer.start()
            self._grab()
        elif not active and self._timer.isActive():
            self._timer.stop()
            self._pending = None

    def _grab(self) -> None:
        if self._pending is not None:
            if time.monotonic() - self._pending_since < 1.0:
                return
            self._pending = None  # Never completed (window hidden or not rendering)
        item = self._find_item()
        if item is None:
            return
        w, h = item.width(), item.height()
        if w < 2 or h < 2:
            return
        size = QSize(self._width, max(2, round(self._width * h / w)))
        try:
            result = item.grabToImage(size)
        except Exception as e:
            self._failures += 1
            if self._failures in (1, 100):
                logger.warning("Face snapshot failed: %s", e)
            return
        if result is None:
            return
        self._pending = result
        self._pending_since = time.monotonic()
        result.ready.connect(self._on_ready)

    def _on_ready(self) -> None:
        result, self._pending = self._pending, None
        if result is None:
            return
        image = result.image()
        if image.isNull():
            return
        # The face item has no background of its own: put it on the screen's black
        frame = QImage(image.size(), QImage.Format.Format_RGB32)
        frame.fill(Qt.GlobalColor.black)
        painter = QPainter(frame)
        painter.drawImage(0, 0, image)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        frame.save(buffer, "JPEG", self._quality)
        self.frames += 1
        try:
            self._sink(bytes(buffer.data()), frame.width(), frame.height())
        except Exception:
            logger.debug("Face snapshot sink failed", exc_info=True)


def find_face_item(root_window) -> Callable[[], object | None]:
    """A lookup for the face item in the loaded window (None for the GIF window)."""
    from PySide6.QtQuick import QQuickItem

    cache: dict = {}

    def lookup():
        item = cache.get("item")
        if item is None and root_window is not None:
            item = root_window.findChild(QQuickItem, ITEM_NAME)
            cache["item"] = item
        return item

    return lookup
