"""Vision lifecycle: owns the camera and detection services, answers detection queries."""

import threading
import time
from typing import Any

import cv2

from src.logging import get_logger
from src.utils.resource_finder import get_app_root
from src.vision.camera_service import CameraService
from src.vision.detection_service import DetectionResult, DetectionService
from src.vision.detector import Detection, ObjectDetector, create_backend
from src.vision.settings import VisionSettings

logger = get_logger()

NO_OBJECTS_NOTE = (
    "Only the 80 common COCO object types can be recognized. "
    "Use take_photo for anything else or for more detail."
)


def horizontal_position(detection: Detection, frame_width: int) -> str:
    """Rough position of a box, in thirds of the frame, from the camera's viewpoint."""
    center = (detection.box[0] + detection.box[2]) / 2
    if center < frame_width / 3:
        return "left"
    if center > frame_width * 2 / 3:
        return "right"
    return "center"


def summarize(result: DetectionResult) -> dict[str, Any]:
    """Turn detections into compact JSON the LLM can phrase as a sentence."""
    groups: dict[str, dict[str, Any]] = {}
    for detection in result.detections:
        group = groups.setdefault(
            detection.label,
            {"label": detection.label, "count": 0, "confidence": 0.0, "positions": []},
        )
        group["count"] += 1
        group["confidence"] = max(group["confidence"], round(detection.confidence, 2))
        position = horizontal_position(detection, result.frame_width)
        if position not in group["positions"]:
            group["positions"].append(position)

    objects = sorted(groups.values(), key=lambda g: (-g["count"], -g["confidence"]))
    response: dict[str, Any] = {
        "success": True,
        "total": len(result.detections),
        "objects": objects,
    }
    if objects:
        parts = [
            f"{g['count']} {g['label']} ({', '.join(g['positions'])})" for g in objects
        ]
        response["summary"] = "Detected: " + "; ".join(parts) + "."
    else:
        response["summary"] = "No known objects detected."
        response["note"] = NO_OBJECTS_NOTE
    return response


class VisionRuntime:
    """Owns CameraService and DetectionService.

    With keep-alive on (the preview is showing) detection runs continuously. Otherwise it
    starts on the first query and stops again after VISION.IDLE_STOP_S, so CLI, TUI and
    GPIO modes do not keep the camera open or spend CPU when nobody asks.
    """

    def __init__(self, settings: VisionSettings, camera: CameraService | None = None):
        self.settings = settings
        self.camera = camera or CameraService(fps=settings.capture_fps)
        self.detection = DetectionService(
            self.camera, self._create_detector, settings.inference_rate
        )
        self._keep_alive = False
        self._last_query = 0.0
        self._idle_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _create_detector(self) -> ObjectDetector:
        # Resizing a few frames per second gains nothing from a thread pool
        cv2.setNumThreads(1)
        backend = create_backend(self.settings.detector, get_app_root())
        return ObjectDetector(backend, self.settings.detector)

    @property
    def keep_alive(self) -> bool:
        return self._keep_alive

    def set_keep_alive(self, keep_alive: bool) -> None:
        self._keep_alive = keep_alive

    def start(self) -> None:
        """Start detection in the background. Never blocks on the camera or model."""
        if self.settings.enabled:
            self.detection.start()

    def _schedule_idle_check(self) -> None:
        with self._lock:
            if self._keep_alive or self._idle_timer is not None:
                return
            timer = threading.Timer(self.settings.idle_stop_s, self._on_idle_check)
            timer.daemon = True
            self._idle_timer = timer
            timer.start()

    def _on_idle_check(self) -> None:
        with self._lock:
            self._idle_timer = None
        idle_for = time.monotonic() - self._last_query
        if idle_for < self.settings.idle_stop_s:
            self._schedule_idle_check()
        elif not self._keep_alive:
            logger.info("Vision idle, releasing the camera")
            self.detection.stop()

    def query(self) -> dict[str, Any]:
        """Latest detections as JSON-ready data. Blocking: call from a worker thread."""
        if not self.settings.enabled:
            return {"success": False, "message": "Vision is disabled in the configuration"}

        now = time.monotonic()
        self._last_query = now
        self.start()
        self._schedule_idle_check()

        oldest = now - self.settings.max_result_age_s
        result = self.detection.wait_for_result(oldest, self.settings.detect_timeout_s)
        if result is not None:
            return summarize(result)

        if self.detection.error:
            return {"success": False, "message": self.detection.error}
        return {
            "success": False,
            "message": f"The camera is not delivering frames ({self.camera.state})",
        }

    def shutdown(self) -> None:
        self._keep_alive = False
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
        self.detection.stop()
        self.camera.shutdown()
