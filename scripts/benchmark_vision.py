"""Benchmark object detection with the current settings, to compare laptop and Pi.

    python scripts/benchmark_vision.py
    python scripts/benchmark_vision.py --size 640 --threads 4 --backend onnxruntime
    python scripts/benchmark_vision.py --test-pattern     # No camera needed

Reports inference time alone and the full pipeline (camera + detection), both uncapped
so the numbers show what the hardware can do, not VISION.INFERENCE_RATE.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.config_manager import initialize_config  # noqa: E402

initialize_config()

from src.logging import load_logging_config, setup_logging  # noqa: E402

setup_logging(enable_console=False, config=load_logging_config())

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from src.utils.resource_finder import get_app_root  # noqa: E402
from src.vision.camera_service import CameraService, TestPatternSource  # noqa: E402
from src.vision.detector import (  # noqa: E402
    DetectorError,
    ObjectDetector,
    create_backend,
)
from src.vision.settings import load_vision_settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark object detection")
    parser.add_argument("--seconds", type=float, default=10, help="Length of each run")
    parser.add_argument("--size", type=int, help="Override VISION.INPUT_SIZE")
    parser.add_argument("--threads", type=int, help="Override VISION.NUM_THREADS")
    parser.add_argument("--backend", choices=["ncnn", "onnxruntime"], help="Override backend")
    parser.add_argument(
        "--test-pattern", action="store_true", help="Use synthetic frames, no camera"
    )
    return parser.parse_args()


def report(name: str, times_ms: list[float], elapsed: float) -> None:
    ordered = sorted(times_ms)
    p95 = ordered[int(len(ordered) * 0.95) - 1] if len(ordered) >= 20 else ordered[-1]
    print(
        f"{name:<22} {len(times_ms) / elapsed:6.1f} FPS   "
        f"mean {statistics.mean(times_ms):6.1f} ms   "
        f"median {statistics.median(times_ms):6.1f} ms   p95 {p95:6.1f} ms"
    )


def bench_inference(detector: ObjectDetector, seconds: float) -> None:
    """Inference on a fixed busy image, without any camera cost."""
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    frame = cv2.GaussianBlur(frame, (9, 9), 0)
    detector.detect(frame)  # First run allocates, keep it out of the numbers

    times = []
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        begin = time.perf_counter()
        detector.detect(frame)
        times.append((time.perf_counter() - begin) * 1000)
    report("Inference only", times, time.perf_counter() - started)


def bench_pipeline(detector: ObjectDetector, camera: CameraService, seconds: float) -> None:
    """Newest camera frame through detection, as fast as frames and CPU allow."""
    client = object()
    camera.acquire(client)
    try:
        frame = camera.wait_for_frame(0, 15)
        if frame is None:
            print(f"Camera delivered no frames (state: {camera.state}). Skipping pipeline.")
            return
        height, width = frame.image.shape[:2]
        print(f"Camera frame size      {width}x{height}")
        times = []
        started = time.perf_counter()
        sequence = frame.sequence
        while time.perf_counter() - started < seconds:
            frame = camera.wait_for_frame(sequence, 3)
            if frame is None:
                print("Camera stopped delivering frames during the run.")
                break
            sequence = frame.sequence
            begin = time.perf_counter()
            detector.detect(frame.image)
            times.append((time.perf_counter() - begin) * 1000)
        if times:
            report("Camera + inference", times, time.perf_counter() - started)
    finally:
        camera.release(client)


def main() -> int:
    args = parse_args()
    settings = load_vision_settings()
    if args.size:
        settings.detector.input_size = args.size
    if args.threads:
        settings.detector.num_threads = args.threads
    if args.backend:
        settings.detector.backend = args.backend

    cv2.setNumThreads(1)
    try:
        backend = create_backend(settings.detector, get_app_root())
    except DetectorError as e:
        print(f"Cannot run benchmark: {e}")
        return 1

    detector = ObjectDetector(backend, settings.detector)
    print(
        f"Backend {backend.name}, input {settings.detector.input_size}, "
        f"{settings.detector.num_threads} threads, CPU only"
    )
    bench_inference(detector, args.seconds)

    if args.test_pattern:
        camera = CameraService(source_factory=lambda cfg: TestPatternSource())
    else:
        camera = CameraService(fps=settings.capture_fps)
    bench_pipeline(detector, camera, args.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
