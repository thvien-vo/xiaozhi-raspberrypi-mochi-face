"""Background object detection at a capped rate.

Runs in a thread, which is enough to keep audio smooth: both ncnn and onnxruntime release
the GIL during inference (see docs/DECISIONS.md for the measurement).
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from src.logging import get_logger
from src.vision.detector import Detection, DetectorError

logger = get_logger()

_FRAME_WAIT_S = 1.0


@dataclass(frozen=True)
class DetectionResult:
    detections: tuple[Detection, ...]
    frame_width: int
    frame_height: int
    timestamp: float
    inference_ms: float


class DetectionService:
    """Reads frames from the camera service and keeps the latest detections."""

    def __init__(
        self,
        camera,
        detector_factory: Callable[[], object],
        inference_rate: float = 4.0,
    ) -> None:
        self._camera = camera
        self._detector_factory = detector_factory
        self._interval = 1.0 / inference_rate
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._result: DetectionResult | None = None
        self._error: str | None = None
        self._detector = None

    @property
    def running(self) -> bool:
        return self._thread is not None

    @property
    def error(self) -> str | None:
        """Why detection cannot run; set when the model or runtime is unusable."""
        return self._error

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                return
            self._error = None
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(self._stop,),
                name="object-detection",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._condition:
            thread = self._thread
            self._thread = None
            self._stop.set()
            self._result = None
            self._condition.notify_all()
        if thread is not None:
            thread.join(5.0)

    def latest(self) -> DetectionResult | None:
        with self._condition:
            return self._result

    def wait_for_result(self, not_before: float, timeout: float) -> DetectionResult | None:
        """Return a result no older than not_before, or None on timeout or failure."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._error is None and self._thread is not None:
                result = self._result
                if result is not None and result.timestamp >= not_before:
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
        return None

    def _fail(self, message: str) -> None:
        logger.error("Object detection stopped: %s", message)
        with self._condition:
            self._error = message
            self._result = None
            self._condition.notify_all()

    def _run_once(self, frame) -> None:
        started = time.perf_counter()
        detections = self._detector.detect(frame.image)
        inference_ms = (time.perf_counter() - started) * 1000
        height, width = frame.image.shape[:2]
        with self._condition:
            self._result = DetectionResult(
                tuple(detections), width, height, frame.timestamp, inference_ms
            )
            self._condition.notify_all()

    def _run(self, stop: threading.Event) -> None:
        client = object()
        self._camera.acquire(client)
        try:
            if self._detector is None:
                self._detector = self._detector_factory()
            sequence = 0
            while not stop.is_set():
                frame = self._camera.wait_for_frame(sequence, _FRAME_WAIT_S)
                if stop.is_set():
                    break
                if frame is None:
                    # Camera gone: results must not look fresh, it reconnects by itself
                    with self._condition:
                        self._result = None
                    sequence = 0
                    continue
                sequence = frame.sequence
                started = time.monotonic()
                self._run_once(frame)
                stop.wait(max(0.0, self._interval - (time.monotonic() - started)))
        except (DetectorError, OSError) as e:
            self._fail(str(e))
        except Exception as e:
            logger.error("Object detection crashed", exc_info=True)
            self._fail(f"Detection failed: {e}")
        finally:
            self._camera.release(client)
            with self._condition:
                if self._stop is stop:
                    self._thread = None
                self._condition.notify_all()
