"""Shared camera service: the one owner of the camera device.

A background thread reads frames while at least one client holds the camera. Detection,
the preview and take_photo all read the latest frame, so the device is opened once.
Disconnects are handled by reopening with a backoff.
"""

import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np

from src.logging import get_logger

logger = get_logger()

STATE_STOPPED = "stopped"
STATE_OPENING = "opening"
STATE_RUNNING = "running"
STATE_RECONNECTING = "reconnecting"

_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 10.0
_MAX_READ_FAILURES = 15
_READ_RETRY_S = 0.03
_THREAD_JOIN_S = 3.0
_PHOTO_TIMEOUT_S = 12.0

# Process-wide owner, the same pattern as get_config(): capture_jpeg() is called from
# places with no reference to the container, and they must all reach this one instance
_active_service: "CameraService | None" = None


def set_active_camera_service(service: "CameraService | None") -> None:
    global _active_service
    _active_service = service


def get_active_camera_service() -> "CameraService | None":
    return _active_service


@dataclass(frozen=True)
class CameraFrame:
    """A captured frame. Shared between readers: treat the image as read-only."""

    image: np.ndarray
    sequence: int
    timestamp: float


class FrameSource(Protocol):
    def open(self) -> bool: ...

    def read(self) -> np.ndarray | None: ...

    def close(self) -> None: ...


class OpenCvSource:
    """USB/UVC cameras through OpenCV (V4L2 on Linux)."""

    def __init__(self, cfg, fps: int) -> None:
        self._cfg = cfg
        self._fps = fps
        self._capture = None

    def open(self) -> bool:
        from src.mcp.tools.camera.capture_backend import (
            _open_capture,
            _silence_opencv_logs,
        )

        _silence_opencv_logs()
        cfg = self._cfg
        source: Any = cfg.device or int(cfg.camera_index)
        capture = _open_capture(source)
        if not capture.isOpened():
            capture.release()
            logger.warning("Camera %r could not be opened", source)
            return False
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.frame_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.frame_height)
        if self._fps > 0:
            capture.set(cv2.CAP_PROP_FPS, self._fps)
        # Keep latency low: the newest frame matters, not a queue of old ones
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._capture = capture
        logger.info(
            "Camera %r opened: %dx%d, backend %s",
            source,
            capture.get(cv2.CAP_PROP_FRAME_WIDTH),
            capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
            capture.getBackendName(),
        )
        return True

    def read(self) -> np.ndarray | None:
        ok, frame = self._capture.read()
        if not ok or frame is None or frame.size == 0:
            return None
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class Picamera2Source:
    """Raspberry Pi CSI cameras."""

    def __init__(self, cfg) -> None:
        self._cfg = cfg
        self._camera = None

    def open(self) -> bool:
        try:
            from picamera2 import Picamera2
        except ImportError:
            logger.warning("picamera2 is not installed")
            return False
        try:
            camera = Picamera2()
            size = (self._cfg.frame_width, self._cfg.frame_height)
            camera.configure(camera.create_video_configuration(main={"size": size}))
            camera.start()
        except Exception as e:
            logger.warning("picamera2 could not start: %s", e)
            return False
        self._camera = camera
        return True

    def read(self) -> np.ndarray | None:
        from src.mcp.tools.camera.capture_backend import _bgr_from_picamera_array

        try:
            return _bgr_from_picamera_array(self._camera.capture_array())
        except Exception as e:
            logger.warning("picamera2 read failed: %s", e)
            return None

    def close(self) -> None:
        if self._camera is not None:
            try:
                self._camera.stop()
                self._camera.close()
            except Exception as e:
                logger.debug("picamera2 close failed: %s", e)
            self._camera = None


class AutoSource:
    """Tries OpenCV first, then the CSI stack (same order as capture_backend)."""

    def __init__(self, cfg, fps: int) -> None:
        self._sources = [OpenCvSource(cfg, fps)]
        if sys.platform.startswith("linux"):
            self._sources.append(Picamera2Source(cfg))
        self._active: FrameSource | None = None

    def open(self) -> bool:
        for source in self._sources:
            if source.open():
                self._active = source
                return True
        return False

    def read(self) -> np.ndarray | None:
        return self._active.read()

    def close(self) -> None:
        if self._active is not None:
            self._active.close()
            self._active = None


class TestPatternSource:
    """Moving synthetic frames, for running without a camera (face demo, tests)."""

    __test__ = False  # Not a pytest class

    def __init__(self, width: int = 640, height: int = 480, fps: int = 30) -> None:
        self._width = width
        self._height = height
        self._interval = 1.0 / max(1, fps)
        self._count = 0

    def open(self) -> bool:
        return True

    def read(self) -> np.ndarray | None:
        time.sleep(self._interval)
        self._count += 1
        frame = np.full((self._height, self._width, 3), 40, np.uint8)
        frame[:, :, 0] = np.linspace(20, 120, self._width, dtype=np.uint8)
        x = (self._count * 4) % self._width
        cv2.circle(frame, (x, self._height // 2), 60, (60, 200, 240), -1)
        cv2.putText(
            frame, "TEST PATTERN", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2
        )
        return frame

    def close(self) -> None:
        pass


def create_frame_source(cfg, fps: int) -> FrameSource:
    if cfg.backend == "opencv":
        return OpenCvSource(cfg, fps)
    if cfg.backend == "picamera2":
        return Picamera2Source(cfg)
    return AutoSource(cfg, fps)


def _source_key(cfg) -> tuple:
    return (cfg.backend, cfg.device, cfg.camera_index)


class CameraService:
    """Thread-safe owner of the camera device, shared by reference-counted clients."""

    def __init__(
        self,
        config_loader: Callable[[], Any] | None = None,
        source_factory: Callable[[Any], FrameSource] | None = None,
        fps: int = 30,
    ) -> None:
        self._config_loader = config_loader or self._load_capture_config
        self._source_factory = source_factory or (
            lambda cfg: create_frame_source(cfg, fps)
        )
        self._condition = threading.Condition()
        # Serializes opening and closing, so a late acquire() waits for the old thread
        # to let go of the device instead of opening it a second time
        self._lifecycle = threading.Lock()
        self._clients: set[object] = set()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._reopen = threading.Event()
        self._suspended = 0
        self._device_released = threading.Event()
        self._device_released.set()
        self._frame: CameraFrame | None = None
        self._sequence = 0
        self._state = STATE_STOPPED

    @staticmethod
    def _load_capture_config():
        from src.mcp.tools.camera.capture_backend import load_capture_config

        return load_capture_config()

    # ----- Clients -----

    @property
    def state(self) -> str:
        return self._state

    def acquire(self, client: object) -> None:
        """Register a client; the camera opens in the background with the first one."""
        with self._lifecycle, self._condition:
            self._clients.add(client)
            if self._thread is None:
                # A new event per thread: one stuck in a driver call must stay stopped
                self._stop = threading.Event()
                self._frame = None
                self._thread = threading.Thread(
                    target=self._run,
                    args=(self._stop,),
                    name="camera-capture",
                    daemon=True,
                )
                self._thread.start()

    def release(self, client: object) -> None:
        """Unregister a client; the device closes with the last one."""
        with self._lifecycle:
            with self._condition:
                self._clients.discard(client)
                if self._clients or self._thread is None:
                    return
                thread = self._thread
                self._thread = None
                self._stop.set()
                self._condition.notify_all()
            thread.join(_THREAD_JOIN_S)
            if thread.is_alive():
                logger.warning("Camera thread did not stop in time (driver read blocked)")

    def shutdown(self) -> None:
        with self._condition:
            clients = list(self._clients)
        for client in clients:
            self.release(client)

    def request_reopen(self) -> None:
        """Pick up changed CAMERA.* settings without disturbing clients."""
        self._reopen.set()

    # ----- Frames -----

    def latest_frame(self) -> CameraFrame | None:
        with self._condition:
            return self._frame

    def wait_for_frame(self, after_sequence: int, timeout: float) -> CameraFrame | None:
        """Return the first frame newer than after_sequence, or None on timeout."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._stop.is_set():
                frame = self._frame
                if frame is not None and frame.sequence > after_sequence:
                    return frame
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
        return None

    # ----- One-shot capture -----

    def matches(self, cfg) -> bool:
        """Whether cfg selects the device this service reads."""
        return _source_key(cfg) == _source_key(self._config_loader())

    def capture_jpeg(
        self, max_side: int, timeout: float = _PHOTO_TIMEOUT_S
    ) -> bytes | None:
        """Take a fresh photo, opening the camera for the duration if nobody else has."""
        from src.mcp.tools.camera.capture_backend import _encode_bgr_jpeg

        client = object()
        started = time.monotonic()
        current = self.latest_frame()
        self.acquire(client)
        try:
            # The next frame is always newer than this call, so a photo is never stale
            frame = self.wait_for_frame(current.sequence if current else 0, timeout)
        finally:
            self.release(client)
        if frame is None:
            logger.error(
                "No camera frame after %.1fs (state %s)",
                time.monotonic() - started,
                self._state,
            )
            return None
        return _encode_bgr_jpeg(frame.image, max_side)

    @contextmanager
    def suspended(self):
        """Release the device for a moment, for code that must open it directly."""
        with self._condition:
            self._suspended += 1
            running = self._thread is not None
            self._condition.notify_all()
        if running and not self._device_released.wait(_THREAD_JOIN_S):
            logger.warning("Camera was not released in time")
        try:
            yield
        finally:
            with self._condition:
                self._suspended -= 1
                self._condition.notify_all()

    # ----- Capture thread -----

    def _publish(self, image: np.ndarray) -> None:
        with self._condition:
            self._sequence += 1
            self._frame = CameraFrame(image, self._sequence, time.monotonic())
            self._condition.notify_all()

    def _should_close(self, stop: threading.Event) -> bool:
        return stop.is_set() or self._suspended > 0 or self._reopen.is_set()

    def _claim_device(self, stop: threading.Event) -> None:
        """Wait out a suspension, then mark the device busy, atomically with it."""
        with self._condition:
            while self._suspended > 0 and not stop.is_set():
                self._condition.wait(0.2)
            self._device_released.clear()

    def _read_until_closed(
        self, source: FrameSource, warm_up_frames: int, stop: threading.Event
    ) -> bool:
        """Read frames until asked to close. Returns False if the camera failed."""
        failures = 0
        discard = warm_up_frames
        while not self._should_close(stop):
            image = source.read()
            if image is None:
                failures += 1
                if failures >= _MAX_READ_FAILURES:
                    return False
                stop.wait(_READ_RETRY_S)
                continue
            failures = 0
            if discard > 0:
                discard -= 1
                continue
            self._state = STATE_RUNNING
            self._publish(image)
        return True

    def _run(self, stop: threading.Event) -> None:
        backoff = _RECONNECT_MIN_S
        while not stop.is_set():
            self._claim_device(stop)
            self._reopen.clear()
            source = None
            healthy = False
            try:
                if stop.is_set():
                    break
                cfg = self._config_loader()
                if self._state != STATE_RECONNECTING:
                    self._state = STATE_OPENING
                source = self._source_factory(cfg)
                if source.open():
                    healthy = self._read_until_closed(source, cfg.warm_up_frames, stop)
                    if healthy:
                        backoff = _RECONNECT_MIN_S
            except Exception as e:
                logger.error("Camera failure: %s", e, exc_info=True)
            finally:
                if source is not None:
                    try:
                        source.close()
                    except Exception as e:
                        logger.debug("Camera close failed: %s", e)
                # No device, no frame: readers must never mistake an old one for live
                with self._condition:
                    self._frame = None
                self._device_released.set()

            if not healthy and not stop.is_set():
                self._state = STATE_RECONNECTING
                logger.warning("Camera unavailable, retrying in %.0fs", backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX_S)

        self._state = STATE_STOPPED
        with self._condition:
            self._frame = None
            self._condition.notify_all()

