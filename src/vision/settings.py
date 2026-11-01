"""VISION.* settings with defaults and range checks."""

from dataclasses import dataclass, field

from src.utils.config_manager import ConfigManager, get_config
from src.vision.detector import DetectorSettings

PREVIEW_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


@dataclass
class PreviewSettings:
    enabled: bool = True
    corner: str = "top-right"
    width: int = 240
    fps: float = 12.0
    margin: int = 16
    show_boxes: bool = True


@dataclass
class VisionSettings:
    enabled: bool = True
    detector: DetectorSettings = field(default_factory=DetectorSettings)
    inference_rate: float = 4.0
    max_result_age_s: float = 2.0
    detect_timeout_s: float = 8.0
    idle_stop_s: float = 60.0
    capture_fps: int = 30
    preview: PreviewSettings = field(default_factory=PreviewSettings)


def load_vision_settings(config=None) -> VisionSettings:
    config = config or get_config()
    defaults = ConfigManager.DEFAULT_CONFIG["VISION"]

    def value(key: str, cast, minimum=None, section="VISION", source=defaults):
        try:
            result = cast(config.get_config(f"{section}.{key}", source[key]))
        except (TypeError, ValueError):
            result = cast(source[key])
        return result if minimum is None else max(minimum, result)

    backend = value("BACKEND", str).lower()
    if backend not in ("auto", "ncnn", "onnxruntime"):
        backend = "auto"

    input_size = value("INPUT_SIZE", int, 32)
    input_size -= input_size % 32  # The network only accepts multiples of its stride

    preview_defaults = defaults["PREVIEW"]

    def preview(key: str, cast, minimum=None):
        return value(key, cast, minimum, "VISION.PREVIEW", preview_defaults)

    corner = preview("CORNER", str).lower()
    if corner not in PREVIEW_CORNERS:
        corner = preview_defaults["CORNER"]

    return VisionSettings(
        enabled=value("ENABLED", bool),
        detector=DetectorSettings(
            backend=backend,
            ncnn_model_path=value("NCNN_MODEL_PATH", str),
            onnx_model_path=value("ONNX_MODEL_PATH", str),
            input_size=input_size,
            num_threads=value("NUM_THREADS", int, 1),
            confidence=value("CONFIDENCE", float, 0.01),
            iou=value("IOU", float, 0.01),
        ),
        inference_rate=value("INFERENCE_RATE", float, 0.1),
        max_result_age_s=value("MAX_RESULT_AGE_S", float, 0.1),
        detect_timeout_s=value("DETECT_TIMEOUT_S", float, 1.0),
        idle_stop_s=value("IDLE_STOP_S", float, 1.0),
        capture_fps=int(config.get_config("CAMERA.fps", 30) or 30),
        preview=PreviewSettings(
            enabled=preview("ENABLED", bool),
            corner=corner,
            width=preview("WIDTH", int, 64),
            fps=preview("FPS", float, 1.0),
            margin=preview("MARGIN", int, 0),
            show_boxes=preview("SHOW_BOXES", bool),
        ),
    )
