"""YOLO11 object detector for CPU runtimes (ncnn or onnxruntime).

Models are exported by scripts/export_yolo.py with the decoded head and no NMS, so the
output is (84, N): box center, box size (input pixels), then 80 class scores.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from src.logging import get_logger
from src.vision.coco import COCO_LABELS

logger = get_logger()

_LETTERBOX_FILL = 114
_NUM_OUTPUTS = 4 + len(COCO_LABELS)


class DetectorError(RuntimeError):
    """The model or its runtime cannot be used; the message says how to fix it."""


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in frame pixels


class InferenceBackend(Protocol):
    """Runs the network on a square RGB uint8 image, returns (84, N)."""

    name: str

    def run(self, rgb: np.ndarray) -> np.ndarray: ...


def letterbox(frame: np.ndarray, size: int) -> tuple[np.ndarray, float, int, int]:
    """Resize to fit a size x size square, keeping aspect ratio, and pad the rest.

    Returns the image, the scale applied, and the left and top padding.
    """
    height, width = frame.shape[:2]
    scale = min(size / width, size / height)
    new_width, new_height = round(width * scale), round(height * scale)
    resized = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
    left = (size - new_width) // 2
    top = (size - new_height) // 2
    padded = cv2.copyMakeBorder(
        resized,
        top,
        size - new_height - top,
        left,
        size - new_width - left,
        cv2.BORDER_CONSTANT,
        value=(_LETTERBOX_FILL,) * 3,
    )
    return padded, scale, left, top


def non_max_suppression(
    boxes: np.ndarray, scores: np.ndarray, iou_threshold: float
) -> list[int]:
    """Return indices to keep, highest score first. Boxes are x1, y1, x2, y2."""
    order = scores.argsort()[::-1]
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep = []
    while order.size:
        best = order[0]
        keep.append(int(best))
        rest = order[1:]
        x1 = np.maximum(boxes[best, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[best, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[best, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[best, 3], boxes[rest, 3])
        intersection = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        union = areas[best] + areas[rest] - intersection
        iou = intersection / np.maximum(union, 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


def expected_outputs(size: int) -> int:
    """Number of candidate boxes the network produces for an input size."""
    return sum((size // stride) ** 2 for stride in (8, 16, 32))


class OnnxBackend:
    name = "onnxruntime"

    def __init__(self, model_path: Path, threads: int) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        # Detection runs a few times per second: idle workers must sleep, not spin,
        # or they take CPU from audio between inferences
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self._session = ort.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def run(self, rgb: np.ndarray) -> np.ndarray:
        tensor = rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        return self._session.run(None, {self._input: tensor})[0][0]


class NcnnBackend:
    name = "ncnn"

    def __init__(self, model_path: Path, threads: int) -> None:
        # Same reason as allow_spinning above. Has to be set before OpenMP starts.
        os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
        import ncnn

        self._ncnn = ncnn
        self._net = ncnn.Net()
        self._net.opt.use_vulkan_compute = False
        self._net.opt.num_threads = threads
        self._net.opt.openmp_blocktime = 0
        if self._net.load_param(str(model_path / "model.ncnn.param")) != 0:
            raise DetectorError(f"Failed to load NCNN param file in {model_path}")
        if self._net.load_model(str(model_path / "model.ncnn.bin")) != 0:
            raise DetectorError(f"Failed to load NCNN weights in {model_path}")

    def run(self, rgb: np.ndarray) -> np.ndarray:
        # from_pixels, not Mat(array): wrapping a NumPy array keeps the wrong channel
        # stride, and the network then sees a scrambled image without any error
        height, width = rgb.shape[:2]
        pixels = self._ncnn.Mat.PixelType.PIXEL_RGB
        image = self._ncnn.Mat.from_pixels(rgb, pixels, width, height)
        image.substract_mean_normalize([0, 0, 0], [1 / 255.0] * 3)
        extractor = self._net.create_extractor()
        extractor.input("in0", image)
        status, output = extractor.extract("out0")
        if status != 0:
            raise DetectorError(f"NCNN inference failed with status {status}")
        return np.array(output)


@dataclass
class DetectorSettings:
    backend: str = "auto"  # auto | ncnn | onnxruntime
    ncnn_model_path: str = "models/yolo/yolo11n_{imgsz}_ncnn_model"
    onnx_model_path: str = "models/yolo/yolo11n_{imgsz}.onnx"
    input_size: int = 320
    num_threads: int = 2
    confidence: float = 0.35
    iou: float = 0.45


def _resolve_model(template: str, input_size: int, root: Path) -> Path:
    path = Path(template.replace("{imgsz}", str(input_size)))
    return path if path.is_absolute() else root / path


def create_backend(settings: DetectorSettings, root: Path) -> InferenceBackend:
    """Pick a runtime and model. ncnn is preferred, it is the faster option on ARM."""
    ncnn_path = _resolve_model(settings.ncnn_model_path, settings.input_size, root)
    onnx_path = _resolve_model(settings.onnx_model_path, settings.input_size, root)
    candidates = {
        "ncnn": (NcnnBackend, ncnn_path),
        "onnxruntime": (OnnxBackend, onnx_path),
    }
    if settings.backend in candidates:
        order = [settings.backend]
    else:
        order = list(candidates)

    problems = []
    for name in order:
        backend_class, path = candidates[name]
        if not path.exists():
            problems.append(f"{name}: model not found at {path}")
            continue
        try:
            backend = backend_class(path, settings.num_threads)
        except ImportError:
            problems.append(f"{name}: runtime is not installed")
            continue
        logger.info("Vision backend %s, model %s", name, path)
        return backend

    raise DetectorError(
        "No usable detection backend ("
        + "; ".join(problems)
        + "). Install the vision extra and run scripts/export_yolo.py."
    )


class ObjectDetector:
    """Frame in, list of detections out. Not thread-safe: use from one thread."""

    def __init__(self, backend: InferenceBackend, settings: DetectorSettings) -> None:
        self._backend = backend
        self._settings = settings

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Detect objects in a BGR frame."""
        size = self._settings.input_size
        image, scale, left, top = letterbox(frame, size)
        output = self._backend.run(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

        if output.shape != (_NUM_OUTPUTS, expected_outputs(size)):
            raise DetectorError(
                f"Model output {output.shape} does not match input size {size}. "
                "Exported models have a fixed size: check VISION.INPUT_SIZE and the "
                "model path, or export this size with scripts/export_yolo.py."
            )
        return self._decode(output, frame.shape, scale, left, top)

    def _decode(self, output, frame_shape, scale, left, top) -> list[Detection]:
        scores = output[4:]
        class_ids = scores.argmax(axis=0)
        confidences = scores[class_ids, np.arange(scores.shape[1])]
        mask = confidences >= self._settings.confidence
        if not mask.any():
            return []

        cx, cy, width, height = output[:4, mask]
        confidences = confidences[mask]
        class_ids = class_ids[mask]

        frame_height, frame_width = frame_shape[:2]
        boxes = np.stack(
            [
                np.clip((cx - width / 2 - left) / scale, 0, frame_width),
                np.clip((cy - height / 2 - top) / scale, 0, frame_height),
                np.clip((cx + width / 2 - left) / scale, 0, frame_width),
                np.clip((cy + height / 2 - top) / scale, 0, frame_height),
            ],
            axis=1,
        )

        # Shifting each class apart makes the single NMS pass class-aware
        offsets = class_ids[:, None].astype(np.float32) * (frame_width + frame_height)
        keep = non_max_suppression(boxes + offsets, confidences, self._settings.iou)
        return [
            Detection(
                label=COCO_LABELS[class_ids[i]],
                confidence=float(confidences[i]),
                box=tuple(float(v) for v in boxes[i]),
            )
            for i in keep
        ]
