"""Camera capture backend: OpenCV/UVC (desktop + Pi USB) and Picamera2 (Pi CSI).

Desktop USB cameras use OpenCV; Raspberry Pi official CSI stack uses picamera2 (optional dependency).
When ``backend=auto``, OpenCV is tried first, falling back to picamera2 on failure.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.logging import get_logger
from src.utils.config_manager import get_config

logger = get_logger()

_CAPTURE_TIMEOUT_S = 12.0

FLIP_MODES = ("none", "horizontal", "vertical", "both")
_warned_flip_values: set[str] = set()


@dataclass
class CaptureConfig:
    """Capture parameters (loaded from CAMERA.* configuration)."""

    camera_index: int = 0
    device: str = ""  # e.g. /dev/video0; takes precedence over index when non-empty
    backend: str = "auto"  # auto | opencv | picamera2
    frame_width: int = 640
    frame_height: int = 480
    warm_up_frames: int = 5
    flip: str = "none"  # none | horizontal | vertical | both
    fourcc: str = ""  # e.g. "MJPG"; empty keeps the driver default
    jpeg_max_side: int = 320


@dataclass
class CameraDeviceInfo:
    """Displayable/selectable camera device entry."""

    key: str  # Identifier written to config: numeric index string / device path / "picamera2"
    name: str
    kind: str  # opencv | v4l2 | picamera2
    index: int | None = None
    path: str | None = None


def normalize_flip(value: Any) -> str:
    """Return a valid flip mode; anything else is "none", with one warning per value."""
    mode = str(value if value is not None else "none").strip().lower() or "none"
    if mode in FLIP_MODES:
        return mode
    if mode not in _warned_flip_values:
        _warned_flip_values.add(mode)
        logger.warning(
            "Invalid CAMERA.flip %r, using 'none' (valid: %s)", value, ", ".join(FLIP_MODES)
        )
    return "none"


def normalize_fourcc(value: Any) -> str:
    """A 4-character pixel format code, or "" for the driver default."""
    code = str(value or "").strip().upper()
    return code if len(code) == 4 else ""


def apply_fourcc(capture: Any, fourcc: str) -> None:
    """Ask a cv2.VideoCapture for a pixel format. Must be set before width/height."""
    if not fourcc:
        return
    import cv2

    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*fourcc))


def load_capture_config() -> CaptureConfig:
    """Read CAMERA capture configuration from ConfigManager."""
    cfg = get_config()
    raw_backend = (
        str(cfg.get_config("CAMERA.backend", "auto") or "auto").strip().lower()
    )
    if raw_backend not in ("auto", "opencv", "picamera2"):
        raw_backend = "auto"

    device = str(cfg.get_config("CAMERA.device", "") or "").strip()
    try:
        index = int(cfg.get_config("CAMERA.camera_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    try:
        width = int(cfg.get_config("CAMERA.frame_width", 640) or 640)
    except (TypeError, ValueError):
        width = 640
    try:
        height = int(cfg.get_config("CAMERA.frame_height", 480) or 480)
    except (TypeError, ValueError):
        height = 480
    try:
        warm = int(cfg.get_config("CAMERA.warm_up_frames", 5) or 5)
    except (TypeError, ValueError):
        warm = 5
    warm = max(0, min(warm, 30))

    return CaptureConfig(
        camera_index=max(0, index),
        device=device,
        backend=raw_backend,
        frame_width=max(1, width),
        frame_height=max(1, height),
        warm_up_frames=warm,
        flip=normalize_flip(cfg.get_config("CAMERA.flip", "none")),
        fourcc=normalize_fourcc(cfg.get_config("CAMERA.fourcc", "")),
    )


def _silence_opencv_logs() -> None:
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    try:
        import cv2

        if hasattr(cv2, "setLogLevel"):
            cv2.setLogLevel(0)
        if hasattr(cv2, "utils") and hasattr(cv2.utils, "logging"):
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass


def _opencv_open_kwargs():
    """Prefer V4L2 on Linux; use default backend on other platforms."""
    try:
        import cv2

        if sys.platform.startswith("linux") and hasattr(cv2, "CAP_V4L2"):
            return {"apiPreference": cv2.CAP_V4L2}
    except Exception:
        pass
    return {}


def resolve_device_path(source: Any) -> Any:
    """Expand a glob such as ``/dev/v4l/by-id/*-video-index0`` to the first match.

    Stable USB paths survive reboots and replugging, unlike ``/dev/video<N>``. Anything
    that is not a glob is returned unchanged; with no match the pattern is returned as
    is, so opening fails and the caller retries later instead of crashing.
    """
    if isinstance(source, str) and any(ch in source for ch in "*?["):
        import glob

        matches = sorted(glob.glob(source))
        if matches:
            return matches[0]
    return source


def _open_capture(source: Any):
    """Open VideoCapture; source is an int index or device path string (or a glob)."""
    import cv2

    source = resolve_device_path(source)

    kwargs = _opencv_open_kwargs()
    if kwargs:
        cap = cv2.VideoCapture(source, kwargs["apiPreference"])
        if cap is not None and cap.isOpened():
            return cap
        try:
            cap.release()
        except Exception:
            pass
    return cv2.VideoCapture(source)


def _encode_bgr_jpeg(frame, max_side: int = 320) -> bytes | None:
    import cv2

    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    height, width = frame.shape[:2]
    max_dim = max(height, width)
    if max_side > 0 and max_dim > max_side:
        scale = max_side / max_dim
        frame = cv2.resize(
            frame,
            (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return None
    return buf.tobytes()


def _bgr_from_picamera_array(arr):
    """Picamera2 typically outputs RGB/XRGB by default; convert to OpenCV BGR."""
    import cv2
    import numpy as np

    if arr is None:
        return None
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.shape[2] == 4:
        # XRGB / BGRA etc.: take first 3 channels as RGB and convert to BGR (common in picamera2)
        rgb = arr[:, :, :3]
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if arr.shape[2] == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return np.ascontiguousarray(arr)


def _capture_opencv(cfg: CaptureConfig) -> bytes | None:
    _silence_opencv_logs()
    import cv2

    source: Any
    if cfg.device:
        source = cfg.device
        logger.info(f"OpenCV opening device path: {source}")
    else:
        source = int(cfg.camera_index)
        logger.info(f"OpenCV opening index: {source}")

    cap = _open_capture(source)
    if not cap.isOpened():
        logger.error(f"OpenCV failed to open camera source={source!r}")
        return None

    try:
        apply_fourcc(cap, cfg.fourcc)
        # Resolution: best-effort setting, non-fatal if unsupported
        if cfg.frame_width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.frame_width)
        if cfg.frame_height > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.frame_height)

        frame = None
        ret = False
        # Warm-up: initial frames on many USB/Pi devices may be blank or invalid
        loops = max(1, cfg.warm_up_frames + 1)
        for i in range(loops):
            ret, frame = cap.read()
            if ret and frame is not None and getattr(frame, "size", 0) > 0:
                # Reading extra frames stabilizes exposure, but exit early once warm-up threshold is met
                if i >= max(0, cfg.warm_up_frames - 1):
                    break
            time.sleep(0.03)

        if not ret or frame is None:
            logger.error(f"OpenCV failed to read frame source={source!r}")
            return None

        jpeg = _encode_bgr_jpeg(frame, cfg.jpeg_max_side)
        if not jpeg:
            logger.error("OpenCV JPEG encoding failed")
            return None
        logger.info(
            f"OpenCV capture succeeded source={source!r} size={len(jpeg)} "
            f"backend={cap.getBackendName() if hasattr(cap, 'getBackendName') else '?'}"
        )
        return jpeg
    finally:
        try:
            cap.release()
        except Exception:
            pass


def _picamera2_available() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        import picamera2  # noqa: F401

        return True
    except Exception:
        return False


def _capture_picamera2(cfg: CaptureConfig) -> bytes | None:
    if not sys.platform.startswith("linux"):
        logger.error("picamera2 is only supported on Linux / Raspberry Pi")
        return None
    try:
        from picamera2 import Picamera2
    except ImportError:
        logger.error(
            "picamera2 is not installed; cannot use CSI camera. On Pi: sudo apt install python3-picamera2"
        )
        return None

    picam2 = None
    try:
        picam2 = Picamera2()
        # Still configuration; match configured dimensions as closely as driver modes allow
        controls = {}
        config = picam2.create_still_configuration(
            main={"size": (cfg.frame_width, cfg.frame_height)},
            controls=controls,
        )
        picam2.configure(config)
        picam2.start()
        # Allow time for AE/AWB to settle
        time.sleep(0.2)
        for _ in range(max(1, cfg.warm_up_frames)):
            try:
                picam2.capture_array()
            except Exception:
                time.sleep(0.05)
        arr = picam2.capture_array()
        bgr = _bgr_from_picamera_array(arr)
        jpeg = _encode_bgr_jpeg(bgr, cfg.jpeg_max_side)
        if not jpeg:
            logger.error("picamera2 JPEG encoding failed")
            return None
        logger.info(f"picamera2 capture succeeded size={len(jpeg)}")
        return jpeg
    except Exception as e:
        logger.error(f"picamera2 capture failed: {e}", exc_info=True)
        return None
    finally:
        if picam2 is not None:
            try:
                picam2.stop()
            except Exception:
                pass
            try:
                picam2.close()
            except Exception:
                pass


@contextmanager
def _exclusive_device():
    """Make the shared camera service let go of the device while code opens it directly."""
    from src.vision.camera_service import get_active_camera_service

    service = get_active_camera_service()
    if service is None:
        yield
    else:
        with service.suspended():
            yield


def capture_jpeg(cfg: CaptureConfig | None = None) -> bytes | None:
    """Capture a single JPEG frame as bytes per configuration; returns None on failure.

    Goes through the shared camera service, so the device is never opened twice. Only a
    different device than the configured one (settings test) is opened directly.
    """
    from src.vision.camera_service import get_active_camera_service

    cfg = cfg or load_capture_config()
    service = get_active_camera_service()
    if service is not None and service.matches(cfg):
        return service.capture_jpeg(cfg.jpeg_max_side)

    with _exclusive_device():
        return _capture_direct(cfg)


def _capture_direct(cfg: CaptureConfig) -> bytes | None:
    backend = cfg.backend

    def _run() -> bytes | None:
        if backend == "opencv":
            return _capture_opencv(cfg)
        if backend == "picamera2":
            return _capture_picamera2(cfg)

        # auto: try OpenCV (USB/UVC) first, then picamera2 (CSI)
        jpeg = _capture_opencv(cfg)
        if jpeg:
            return jpeg
        if _picamera2_available():
            logger.info("OpenCV capture failed, auto falling back to picamera2 (CSI)")
            return _capture_picamera2(cfg)
        return None

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_run)
        try:
            return fut.result(timeout=_CAPTURE_TIMEOUT_S)
        except FuturesTimeout:
            logger.error(
                f"Camera capture timed out ({_CAPTURE_TIMEOUT_S}s) "
                f"backend={backend} device={cfg.device!r} index={cfg.camera_index}"
            )
            return None


def _list_v4l2_paths() -> list[str]:
    if not sys.platform.startswith("linux"):
        return []
    dev = Path("/dev")
    if not dev.exists():
        return []
    paths = sorted(
        str(p)
        for p in dev.glob("video*")
        if p.name.startswith("video") and p.name[5:].isdigit()
    )
    return paths


def list_camera_devices(
    max_index: int = 5,
    consecutive_fail_limit: int = 2,
) -> list[CameraDeviceInfo]:
    """Enumerate available cameras (for settings UI / scanner scripts)."""
    with _exclusive_device():
        return _enumerate_cameras(max_index, consecutive_fail_limit)


def _enumerate_cameras(
    max_index: int, consecutive_fail_limit: int
) -> list[CameraDeviceInfo]:
    _silence_opencv_logs()
    devices: list[CameraDeviceInfo] = []
    seen: set[str] = set()

    # 1) Linux: list /dev/video* first (helps select the right node on Raspberry Pi)
    for path in _list_v4l2_paths():
        try:

            cap = _open_capture(path)
            opened = bool(cap.isOpened())
            if opened:
                # Try reading a frame to verify this is an actual capture node
                ok = False
                try:
                    for _ in range(2):
                        ret, frame = cap.read()
                        if ret and frame is not None and getattr(frame, "size", 0) > 0:
                            ok = True
                            break
                except Exception:
                    ok = False
                cap.release()
                if ok:
                    key = path
                    if key not in seen:
                        devices.append(
                            CameraDeviceInfo(
                                key=key,
                                name=f"V4L2 {path}",
                                kind="v4l2",
                                path=path,
                            )
                        )
                        seen.add(key)
            else:
                try:
                    cap.release()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Probing {path} failed: {e}")

    # 2) OpenCV numeric indices (all platforms)
    consecutive_fail = 0
    for i in range(max(0, max_index) + 1):
        try:
            cap = _open_capture(i)
            opened = bool(cap.isOpened())
            if opened:
                key = str(i)
                # Skip duplicate if already listed via /dev/video{i}
                path_alias = f"/dev/video{i}"
                if key not in seen and path_alias not in seen:
                    devices.append(
                        CameraDeviceInfo(
                            key=key,
                            name=f"Camera {i}",
                            kind="opencv",
                            index=i,
                        )
                    )
                    seen.add(key)
                try:
                    cap.release()
                except Exception:
                    pass
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                try:
                    cap.release()
                except Exception:
                    pass
                if consecutive_fail >= consecutive_fail_limit:
                    break
        except Exception as e:
            consecutive_fail += 1
            logger.debug(f"Probing index={i} failed: {e}")
            if consecutive_fail >= consecutive_fail_limit:
                break

    # 3) Raspberry Pi CSI
    if _picamera2_available():
        key = "picamera2"
        if key not in seen:
            devices.append(
                CameraDeviceInfo(
                    key=key,
                    name="Raspberry Pi CSI (picamera2)",
                    kind="picamera2",
                )
            )
            seen.add(key)

    logger.info(f"Camera enumeration complete: {len(devices)} found {[d.name for d in devices]}")
    return devices


def apply_device_selection(key: str) -> dict[str, Any]:
    """Generate configuration dictionary fragment to persist for the selected enumeration key.

    Returns:
        Updatable dict: camera_index / device / backend
    """
    key = (key or "").strip()
    if key == "picamera2":
        return {
            "CAMERA.backend": "picamera2",
            "CAMERA.device": "",
            "CAMERA.camera_index": 0,
        }
    if key.startswith("/dev/"):
        return {
            "CAMERA.backend": "opencv",
            "CAMERA.device": key,
            "CAMERA.camera_index": 0,
        }
    try:
        idx = int(key)
    except ValueError:
        idx = 0
    return {
        "CAMERA.backend": "auto",
        "CAMERA.device": "",
        "CAMERA.camera_index": idx,
    }
