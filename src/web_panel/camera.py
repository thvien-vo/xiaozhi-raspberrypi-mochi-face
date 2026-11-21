"""MJPEG frames for the web panel: camera image with YOLO boxes, encoded only while watched.

One encoder thread serves every viewer. It starts with the first viewer and stops with
the last, so nobody watching costs nothing. Frames come from the app's shared
CameraService (the same frames detection and the face preview use); boxes come from
the detector's latest result, mapped to the frame because both use the flipped image.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import cv2
import numpy as np

from src.logging import get_logger

logger = get_logger()

STREAM_WIDTH = 640
JPEG_QUALITY = 70
STALE_BOXES_S = 1.5
_BOX = (80, 220, 80)
_TRACK = (40, 160, 255)
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def annotate(image: np.ndarray, detections=(), width: int = STREAM_WIDTH) -> np.ndarray:
    """Downscaled copy of a BGR frame with detection boxes; the largest person is orange
    (the one the head tracker follows)."""
    h, w = image.shape[:2]
    scale = min(1.0, width / w)
    out = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else image.copy()
    people = [d for d in detections if d.label == "person"]
    target = max(people, key=lambda d: (d.box[2] - d.box[0]) * (d.box[3] - d.box[1]), default=None)
    for det in detections:
        x1, y1, x2, y2 = (round(v * scale) for v in det.box)
        color = _TRACK if det is target else _BOX
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = f"{det.label} {det.confidence:.0%}"
        (tw, th), _ = cv2.getTextSize(label, _FONT, 0.5, 1)
        top = max(y1 - th - 6, 0)
        cv2.rectangle(out, (x1, top), (x1 + tw + 6, top + th + 6), color, -1)
        cv2.putText(out, label, (x1 + 3, top + th + 2), _FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    return out


class CameraFeed:
    """Shared JPEG producer.

    get_frame(after_seq, timeout) -> (image, seq) | None   blocking, newest frame
    get_detections() -> detections for the current frame (may be empty)
    on_start() -> handle / on_stop(handle): open and release the camera around the
    first/last viewer (the handle keeps an old stream from releasing a newer one)
    """

    def __init__(
        self,
        get_frame: Callable[[int, float], tuple[np.ndarray, int] | None],
        get_detections: Callable[[], tuple] = lambda: (),
        fps: float = 8.0,
        on_start: Callable[[], object] | None = None,
        on_stop: Callable[[object], None] | None = None,
        is_available: Callable[[], bool] = lambda: True,
    ) -> None:
        self._get_frame = get_frame
        self._get_detections = get_detections
        self.fps = max(0.5, float(fps))
        self._on_start = on_start
        self._on_stop = on_stop
        self._is_available = is_available
        self._cond = threading.Condition()
        self._viewers = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._jpeg: bytes | None = None
        self._seq = 0
        self.frames_encoded = 0
        self.error: str | None = None

    def available(self) -> bool:
        try:
            return bool(self._is_available())
        except Exception:
            return False

    @property
    def viewers(self) -> int:
        return self._viewers

    def add_viewer(self) -> None:
        with self._cond:
            self._viewers += 1
            if self._thread is None:
                self._stop = threading.Event()
                self._thread = threading.Thread(target=self._run, args=(self._stop,), name="web-camera", daemon=True)
                self._thread.start()

    def remove_viewer(self) -> None:
        with self._cond:
            self._viewers = max(0, self._viewers - 1)
            if self._viewers == 0 and self._thread is not None:
                self._stop.set()
                self._thread = None
                self._cond.notify_all()

    def wait_jpeg(self, after_seq: int, timeout: float) -> tuple[bytes, int] | None:
        """Next encoded frame newer than after_seq (blocking; call from a thread)."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                if self._jpeg is not None and self._seq > after_seq:
                    return self._jpeg, self._seq
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._thread is None:
                    return None
                self._cond.wait(remaining)

    def _run(self, stop: threading.Event) -> None:
        handle = None
        if self._on_start is not None:
            try:
                handle = self._on_start()
            except Exception as e:
                logger.warning("Web camera: could not start the camera: %s", e)
        interval = 1.0 / self.fps
        seq = 0
        logger.info("Web camera stream started (%.1f fps)", self.fps)
        try:
            while not stop.is_set():
                started = time.monotonic()
                got = self._get_frame(seq, 1.0)
                if stop.is_set():
                    break
                if got is None:
                    continue
                image, seq = got
                try:
                    dets = self._get_detections() or ()
                except Exception:
                    dets = ()
                ok, buf = cv2.imencode(".jpg", annotate(image, dets), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    with self._cond:
                        self._jpeg = buf.tobytes()
                        self._seq += 1
                        self.frames_encoded += 1
                        self._cond.notify_all()
                stop.wait(max(0.0, interval - (time.monotonic() - started)))
        except Exception as e:
            self.error = str(e)
            logger.error("Web camera stream failed", exc_info=True)
        finally:
            with self._cond:
                self._jpeg = None
                self._cond.notify_all()
            if self._on_stop is not None:
                try:
                    self._on_stop(handle)
                except Exception as e:
                    logger.debug("Web camera stop failed: %s", e)
            logger.info("Web camera stream stopped")


def vision_camera_feed(vision, fps: float) -> CameraFeed:
    """Feed on the app's VisionRuntime: shared camera, boxes from the running detector."""
    camera = vision.camera

    def on_start() -> tuple[object, bool]:
        client = object()
        camera.acquire(client)
        started = False
        if vision.settings.enabled and not vision.detection.running:
            vision.detection.start()
            started = True
        return client, started

    def on_stop(handle) -> None:
        if not handle:
            return
        client, started = handle
        camera.release(client)
        # Leave detection to whoever else needs it (face preview, tracking)
        if started and not vision.keep_alive:
            vision.detection.stop()

    def get_frame(after: int, timeout: float):
        frame = camera.wait_for_frame(after, timeout)
        return None if frame is None else (frame.image, frame.sequence)

    def get_detections():
        result = vision.detection.latest()
        if result is None or time.monotonic() - result.timestamp > STALE_BOXES_S:
            return ()
        return result.detections

    return CameraFeed(get_frame, get_detections, fps, on_start, on_stop)
