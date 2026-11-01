"""Live camera preview for QML: small frames with detection boxes, at a capped rate.

Frames are scaled and annotated in a background thread. The GUI thread only hands the
finished image to QML, so the preview cannot slow down the face animation.
"""

import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import Property, QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from src.logging import get_logger
from src.vision.detection_service import DetectionResult
from src.vision.detector import Detection

logger = get_logger()

PROVIDER_ID = "camera"

_BOX_COLOR = (80, 220, 80)
_LABEL_TEXT_COLOR = (0, 0, 0)
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_FONT_SCALE = 0.38
_FRAME_WAIT_S = 0.25
_STALE_BOXES_S = 1.5


def scale_box(detection: Detection, scale: float) -> tuple[int, int, int, int]:
    return tuple(round(v * scale) for v in detection.box)


def compose_preview(
    image: np.ndarray,
    width: int,
    detections: tuple[Detection, ...] = (),
    show_boxes: bool = True,
) -> np.ndarray:
    """Return a downscaled copy of a BGR frame with boxes and labels drawn on it."""
    source_height, source_width = image.shape[:2]
    scale = width / source_width
    height = max(1, round(source_height * scale))
    preview = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    if not show_boxes:
        return preview

    for detection in detections:
        x1, y1, x2, y2 = scale_box(detection, scale)
        cv2.rectangle(preview, (x1, y1), (x2, y2), _BOX_COLOR, 1)
        label = f"{detection.label} {detection.confidence:.0%}"
        (text_width, text_height), _ = cv2.getTextSize(label, _FONT, _FONT_SCALE, 1)
        top = max(y1 - text_height - 4, 0)
        cv2.rectangle(
            preview, (x1, top), (x1 + text_width + 4, top + text_height + 4), _BOX_COLOR, -1
        )
        cv2.putText(
            preview,
            label,
            (x1 + 2, top + text_height + 1),
            _FONT,
            _FONT_SCALE,
            _LABEL_TEXT_COLOR,
            1,
            cv2.LINE_AA,
        )
    return preview


def to_qimage(preview: np.ndarray) -> QImage:
    height, width = preview.shape[:2]
    image = QImage(
        preview.data, width, height, preview.strides[0], QImage.Format.Format_BGR888
    )
    return image.copy()  # QImage above only borrows the NumPy buffer


class CameraPreviewProvider(QQuickImageProvider):
    """Holds the newest preview image; QML reads it through image://camera/..."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._image = QImage()
        self._lock = threading.Lock()

    def set_image(self, image: QImage) -> None:
        with self._lock:
            self._image = image

    def requestImage(self, _id: str, size: QSize, _requested: QSize) -> QImage:
        with self._lock:
            image = self._image
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image


class CameraPreviewService(QObject):
    """Runs the preview thread and tells QML when a new frame is ready."""

    frameChanged = Signal()
    availableChanged = Signal()
    aspectChanged = Signal()
    # Emitted from the preview thread; queued onto the GUI thread
    _frameReady = Signal(float)
    _unavailable = Signal()

    def __init__(self, runtime, parent=None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._settings = runtime.settings.preview
        self.provider = CameraPreviewProvider()
        self._frame_id = 0
        self._available = False
        self._aspect = 0.75
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._frameReady.connect(self._on_frame_ready, Qt.ConnectionType.QueuedConnection)
        self._unavailable.connect(
            self._on_unavailable, Qt.ConnectionType.QueuedConnection
        )

    # ========== Properties ==========

    @Property(int, notify=frameChanged)
    def frameId(self) -> int:
        return self._frame_id

    @Property(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    @Property(float, notify=aspectChanged)
    def aspect(self) -> float:
        """Height divided by width."""
        return self._aspect

    @Property(str, constant=True)
    def corner(self) -> str:
        return self._settings.corner

    @Property(int, constant=True)
    def previewWidth(self) -> int:
        return self._settings.width

    @Property(int, constant=True)
    def margin(self) -> int:
        return self._settings.margin

    # ========== Lifecycle ==========

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(self._stop,), name="camera-preview", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        thread = self._thread
        self._thread = None
        self._stop.set()
        if thread is not None:
            thread.join(2.0)
        self._on_unavailable()

    # ========== Preview thread ==========

    def _fresh_detections(self) -> tuple[Detection, ...]:
        result: DetectionResult | None = self._runtime.detection.latest()
        if result is None or time.monotonic() - result.timestamp > _STALE_BOXES_S:
            return ()
        return result.detections

    def _run(self, stop: threading.Event) -> None:
        camera = self._runtime.camera
        interval = 1.0 / self._settings.fps
        client = object()
        camera.acquire(client)
        try:
            sequence = 0
            while not stop.is_set():
                if self._runtime.detection.error:
                    logger.warning("Vision failed, hiding the camera preview")
                    break
                frame = camera.wait_for_frame(sequence, _FRAME_WAIT_S)
                if frame is None:
                    sequence = 0
                    self._unavailable.emit()
                    continue
                sequence = frame.sequence
                started = time.monotonic()
                preview = compose_preview(
                    frame.image,
                    self._settings.width,
                    self._fresh_detections(),
                    self._settings.show_boxes,
                )
                self.provider.set_image(to_qimage(preview))
                self._frameReady.emit(preview.shape[0] / preview.shape[1])
                stop.wait(max(0.0, interval - (time.monotonic() - started)))
        except Exception as e:
            logger.error("Camera preview stopped: %s", e, exc_info=True)
        finally:
            camera.release(client)
            if not stop.is_set():
                self._unavailable.emit()

    # ========== GUI thread ==========

    @Slot(float)
    def _on_frame_ready(self, aspect: float) -> None:
        if self._thread is None:
            return
        self._frame_id += 1
        if aspect != self._aspect:
            self._aspect = aspect
            self.aspectChanged.emit()
        self.frameChanged.emit()
        if not self._available:
            self._available = True
            self.availableChanged.emit()

    @Slot()
    def _on_unavailable(self) -> None:
        if self._available:
            self._available = False
            self.availableChanged.emit()
