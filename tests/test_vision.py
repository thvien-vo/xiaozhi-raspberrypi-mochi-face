"""Vision: detector maths, shared camera, detection service, MCP tool."""

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.mcp import tools as _tools  # noqa: F401
from src.mcp.tools.camera import capture_backend
from src.mcp.tools.vision import register_vision_tools
from src.utils.config_manager import ConfigManager
from src.vision import camera_service as camera_module
from src.vision.camera_service import CameraService
from src.vision.detection_service import DetectionResult, DetectionService
from src.vision.detector import (
    Detection,
    DetectorError,
    DetectorSettings,
    ObjectDetector,
    OnnxBackend,
    create_backend,
    expected_outputs,
    letterbox,
    non_max_suppression,
)
from src.vision.runtime import VisionRuntime, horizontal_position, summarize
from src.vision.settings import VisionSettings, load_vision_settings

ROOT = Path(__file__).resolve().parent.parent
CAPTURE_CONFIG = capture_backend.CaptureConfig(warm_up_frames=0)


class FakeSource:
    """Counts opens, can simulate a camera that fails or disappears."""

    def __init__(self, stats, fail_after=None, open_ok=True):
        self.stats = stats
        self.fail_after = fail_after
        self.open_ok = open_ok
        self.reads = 0

    def open(self):
        self.stats["opens"] += 1
        if not self.open_ok:
            return False
        self.stats["open_now"] += 1
        self.stats["max_open"] = max(self.stats["max_open"], self.stats["open_now"])
        return True

    def read(self):
        time.sleep(0.002)
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            return None
        return np.zeros((48, 64, 3), np.uint8)

    def close(self):
        if self.open_ok:
            self.stats["open_now"] -= 1


def make_stats():
    return {"opens": 0, "open_now": 0, "max_open": 0}


def make_service(stats, **source_kwargs):
    return CameraService(
        config_loader=lambda: CAPTURE_CONFIG,
        source_factory=lambda cfg: FakeSource(stats, **source_kwargs),
    )


class FakeDetector:
    backend_name = "fake"

    def __init__(self, detections=()):
        self.detections = list(detections)
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        return self.detections


def box_at(center_x):
    return (center_x - 5, 0.0, center_x + 5, 10.0)


class TestDetectorMaths:
    def test_letterbox_keeps_aspect_and_pads(self):
        image, scale, left, top = letterbox(np.zeros((480, 640, 3), np.uint8), 320)
        assert image.shape == (320, 320, 3)
        assert scale == pytest.approx(0.5)
        assert (left, top) == (0, 40)

    def test_nms_drops_overlap_and_keeps_best(self):
        boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], float)
        scores = np.array([0.5, 0.9, 0.7])
        assert non_max_suppression(boxes, scores, 0.45) == [1, 2]

    def test_decode_maps_back_to_frame_pixels(self):
        size = 320
        count = expected_outputs(size)
        output = np.zeros((84, count), np.float32)
        # person, centered in the input square, 100 x 80 input pixels
        output[:4, 0] = (160, 160, 100, 80)
        output[4, 0] = 0.9

        class Backend:
            name = "stub"

            def run(self, rgb):
                return output

        detector = ObjectDetector(Backend(), DetectorSettings(input_size=size))
        [found] = detector.detect(np.zeros((480, 640, 3), np.uint8))
        assert found.label == "person"
        assert found.confidence == pytest.approx(0.9)
        assert found.box == pytest.approx((220, 160, 420, 320))

    def test_same_box_two_classes_both_survive_nms(self):
        output = np.zeros((84, expected_outputs(320)), np.float32)
        output[:4, :2] = np.array([[160, 160], [160, 160], [100, 100], [100, 100]])
        output[4, 0] = 0.9  # person
        output[5, 1] = 0.8  # bicycle

        class Backend:
            name = "stub"

            def run(self, rgb):
                return output

        detector = ObjectDetector(Backend(), DetectorSettings(input_size=320))
        labels = {d.label for d in detector.detect(np.zeros((320, 320, 3), np.uint8))}
        assert labels == {"person", "bicycle"}

    def test_wrong_model_size_explains_the_fix(self):
        class Backend:
            name = "stub"

            def run(self, rgb):
                return np.zeros((84, expected_outputs(640)), np.float32)

        detector = ObjectDetector(Backend(), DetectorSettings(input_size=320))
        with pytest.raises(DetectorError, match="INPUT_SIZE"):
            detector.detect(np.zeros((320, 320, 3), np.uint8))

    def test_missing_model_explains_the_fix(self, tmp_path):
        with pytest.raises(DetectorError, match="export_yolo"):
            create_backend(DetectorSettings(), tmp_path)


def installed_model(name):
    path = ROOT / "models" / "yolo" / name
    if not path.exists():
        pytest.skip(f"{name} not exported")
    return path


class TestRealModels:
    def test_ncnn_and_onnx_agree(self):
        ncnn = pytest.importorskip("ncnn")
        assert ncnn
        pytest.importorskip("onnxruntime")
        from src.vision.detector import NcnnBackend

        rng = np.random.default_rng(1)
        rgb = rng.integers(0, 255, (320, 320, 3), dtype=np.uint8)
        onnx_out = OnnxBackend(installed_model("yolo11n_320.onnx"), 2).run(rgb)
        ncnn_out = NcnnBackend(installed_model("yolo11n_320_ncnn_model"), 2).run(rgb)
        assert onnx_out.shape == ncnn_out.shape == (84, expected_outputs(320))
        assert np.allclose(onnx_out, ncnn_out, atol=0.5)


class TestCameraService:
    def test_all_clients_share_one_open(self):
        stats = make_stats()
        service = make_service(stats)
        clients = [object() for _ in range(3)]
        for client in clients:
            service.acquire(client)
        assert service.wait_for_frame(0, 2) is not None
        assert stats["opens"] == 1
        for client in clients:
            service.release(client)
        assert stats["open_now"] == 0
        assert service.state == camera_module.STATE_STOPPED

    def test_device_stays_open_until_last_client_leaves(self):
        stats = make_stats()
        service = make_service(stats)
        first, second = object(), object()
        service.acquire(first)
        service.acquire(second)
        assert service.wait_for_frame(0, 2) is not None
        service.release(first)
        assert stats["open_now"] == 1
        frame = service.latest_frame()
        assert service.wait_for_frame(frame.sequence, 2) is not None
        service.release(second)

    def test_rapid_acquire_release_never_opens_twice(self):
        stats = make_stats()
        service = make_service(stats)

        def churn():
            for _ in range(30):
                client = object()
                service.acquire(client)
                service.release(client)

        threads = [threading.Thread(target=churn) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        service.shutdown()
        assert stats["max_open"] == 1

    def test_warm_up_frames_are_discarded(self):
        stats = make_stats()
        config = capture_backend.CaptureConfig(warm_up_frames=5)
        source = FakeSource(stats)
        service = CameraService(lambda: config, lambda cfg: source)
        client = object()
        service.acquire(client)
        assert service.wait_for_frame(0, 2) is not None
        assert source.reads >= 6
        service.release(client)

    def test_reconnects_after_disconnect(self, monkeypatch):
        monkeypatch.setattr(camera_module, "_RECONNECT_MIN_S", 0.01)
        stats = make_stats()
        sources = iter(
            [FakeSource(stats, fail_after=2), FakeSource(stats, open_ok=False)]
        )
        service = CameraService(
            lambda: CAPTURE_CONFIG, lambda cfg: next(sources, FakeSource(stats))
        )
        client = object()
        service.acquire(client)
        first = service.wait_for_frame(0, 2)
        assert first is not None

        deadline = time.monotonic() + 5
        while stats["opens"] < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.wait_for_frame(first.sequence + 2, 2) is not None
        assert stats["opens"] >= 3
        service.release(client)

    def test_frames_are_cleared_while_disconnected(self, monkeypatch):
        monkeypatch.setattr(camera_module, "_RECONNECT_MIN_S", 30)
        monkeypatch.setattr(camera_module, "_MAX_READ_FAILURES", 2)
        stats = make_stats()
        service = make_service(stats, fail_after=1)
        client = object()
        service.acquire(client)
        assert service.wait_for_frame(0, 2) is not None
        deadline = time.monotonic() + 3
        while service.state != camera_module.STATE_RECONNECTING:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert service.latest_frame() is None
        service.release(client)

    def test_suspended_releases_device_and_resumes(self):
        stats = make_stats()
        service = make_service(stats)
        client = object()
        service.acquire(client)
        first = service.wait_for_frame(0, 2)
        with service.suspended():
            assert stats["open_now"] == 0
        assert service.wait_for_frame(first.sequence, 2) is not None
        assert stats["open_now"] == 1
        service.release(client)

    def test_request_reopen_reloads_config(self):
        stats = make_stats()
        service = make_service(stats)
        client = object()
        service.acquire(client)
        first = service.wait_for_frame(0, 2)
        service.request_reopen()
        assert service.wait_for_frame(first.sequence + 3, 2) is not None
        assert stats["opens"] == 2
        service.release(client)

    def test_capture_jpeg_alone_opens_and_closes(self):
        stats = make_stats()
        service = make_service(stats)
        jpeg = service.capture_jpeg(320, timeout=2)
        assert jpeg[:2] == b"\xff\xd8"
        assert stats["open_now"] == 0

    def test_capture_jpeg_times_out_without_camera(self, monkeypatch):
        monkeypatch.setattr(camera_module, "_RECONNECT_MIN_S", 30)
        service = make_service(make_stats(), open_ok=False)
        assert service.capture_jpeg(320, timeout=0.05) is None


class TestFlip:
    @pytest.fixture
    def image(self):
        # 2 rows x 3 columns, each pixel unique
        return np.arange(18, dtype=np.uint8).reshape(2, 3, 3)

    def test_modes(self, image):
        assert camera_module.apply_flip(image, "none") is image
        assert np.array_equal(camera_module.apply_flip(image, "horizontal"), image[:, ::-1])
        assert np.array_equal(camera_module.apply_flip(image, "vertical"), image[::-1])
        assert np.array_equal(camera_module.apply_flip(image, "both"), image[::-1, ::-1])

    @pytest.mark.parametrize("bad", ["sideways", "", None, 5])
    def test_invalid_values_fall_back_to_none(self, bad):
        capture_backend._warned_flip_values.clear()
        assert capture_backend.normalize_flip(bad) == ("none")

    def test_normalize_accepts_case_and_spaces(self):
        assert capture_backend.normalize_flip(" Horizontal ") == "horizontal"

    def test_invalid_value_warns_once(self, monkeypatch):
        capture_backend._warned_flip_values.clear()
        warnings = []
        monkeypatch.setattr(
            capture_backend.logger, "warning", lambda *a, **k: warnings.append(a)
        )
        for _ in range(3):
            capture_backend.normalize_flip("sideways")
        assert len(warnings) == 1

    def test_config_default_is_none(self):
        assert ConfigManager.DEFAULT_CONFIG["CAMERA"]["flip"] == "none"
        assert capture_backend.CaptureConfig().flip == "none"

    def test_service_flips_once_for_every_reader(self):
        stats = make_stats()
        cfg = capture_backend.CaptureConfig(warm_up_frames=0, flip="horizontal")
        marker = np.zeros((48, 64, 3), np.uint8)
        marker[:, :8] = 255  # bright strip on the left of the device image

        class Source(FakeSource):
            def read(self):
                time.sleep(0.002)
                return marker

        service = CameraService(lambda: cfg, lambda c: Source(stats))
        client = object()
        service.acquire(client)
        frame = service.wait_for_frame(0, 2)
        assert frame.image[:, -8:].all() and not frame.image[:, :8].any()
        jpeg = service.capture_jpeg(0, timeout=2)
        decoded = __import__("cv2").imdecode(np.frombuffer(jpeg, np.uint8), 1)
        assert decoded[:, -8:].mean() > 200 and decoded[:, :8].mean() < 50
        service.release(client)
        assert marker[:, :8].all()  # The device buffer itself is untouched


class TestCaptureRouting:
    @pytest.fixture
    def active(self):
        stats = make_stats()
        service = make_service(stats)
        camera_module.set_active_camera_service(service)
        yield service, stats
        service.shutdown()
        camera_module.set_active_camera_service(None)

    def test_take_photo_uses_running_camera(self, active, monkeypatch):
        service, stats = active
        monkeypatch.setattr(
            capture_backend,
            "_capture_direct",
            lambda cfg: pytest.fail("opened the device directly"),
        )
        client = object()
        service.acquire(client)
        assert service.wait_for_frame(0, 2) is not None
        assert capture_backend.capture_jpeg(CAPTURE_CONFIG)[:2] == b"\xff\xd8"
        assert stats["opens"] == 1

    def test_other_device_suspends_shared_camera(self, active, monkeypatch):
        service, stats = active
        client = object()
        service.acquire(client)
        first = service.wait_for_frame(0, 2)
        seen = {}

        def direct(cfg):
            seen["open_now"] = stats["open_now"]
            return b"jpeg"

        monkeypatch.setattr(capture_backend, "_capture_direct", direct)
        other = capture_backend.CaptureConfig(device="/dev/video2")
        assert capture_backend.capture_jpeg(other) == b"jpeg"
        assert seen["open_now"] == 0
        assert service.wait_for_frame(first.sequence, 2) is not None

    def test_enumeration_suspends_shared_camera(self, active, monkeypatch):
        service, stats = active
        client = object()
        service.acquire(client)
        service.wait_for_frame(0, 2)
        seen = {}

        def enumerate_cameras(max_index, limit):
            seen["open_now"] = stats["open_now"]
            return []

        monkeypatch.setattr(capture_backend, "_enumerate_cameras", enumerate_cameras)
        assert capture_backend.list_camera_devices() == []
        assert seen["open_now"] == 0

    def test_without_service_opens_directly(self, monkeypatch):
        monkeypatch.setattr(capture_backend, "_capture_direct", lambda cfg: b"direct")
        assert capture_backend.capture_jpeg(CAPTURE_CONFIG) == b"direct"


class TestDetectionService:
    def make(self, stats, factory=None, rate=100.0):
        camera = make_service(stats)
        detector = FakeDetector([Detection("cup", 0.8, box_at(10))])
        return camera, detector, DetectionService(camera, factory or (lambda: detector), rate)

    def test_publishes_results_and_releases_camera(self):
        stats = make_stats()
        _, detector, service = self.make(stats)
        service.start()
        result = service.wait_for_result(0, 2)
        assert result.detections[0].label == "cup"
        assert (result.frame_width, result.frame_height) == (64, 48)
        service.stop()
        assert stats["open_now"] == 0
        assert service.latest() is None

    def test_rate_limit_is_respected(self):
        stats = make_stats()
        _, detector, service = self.make(stats, rate=10.0)
        service.start()
        time.sleep(0.55)
        service.stop()
        assert 3 <= detector.calls <= 8

    def test_model_failure_is_reported_and_camera_released(self):
        stats = make_stats()

        def broken():
            raise DetectorError("no model")

        _, _, service = self.make(stats, factory=broken)
        service.start()
        assert service.wait_for_result(0, 2) is None
        assert service.error == "no model"
        deadline = time.monotonic() + 2
        while stats["open_now"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert stats["open_now"] == 0

    def test_detector_crash_does_not_escape_thread(self):
        stats = make_stats()

        class Crashing:
            def detect(self, image):
                raise ValueError("bad frame")

        _, _, service = self.make(stats, factory=Crashing)
        service.start()
        assert service.wait_for_result(0, 2) is None
        assert "bad frame" in service.error


def make_result(detections, width=300):
    return DetectionResult(tuple(detections), width, 200, time.monotonic(), 5.0)


class TestSummary:
    @pytest.mark.parametrize(
        ("center", "position"), [(50, "left"), (150, "center"), (250, "right")]
    )
    def test_position_thirds(self, center, position):
        assert horizontal_position(Detection("cup", 0.5, box_at(center)), 300) == position

    def test_groups_by_label(self):
        result = summarize(
            make_result(
                [
                    Detection("person", 0.9, box_at(50)),
                    Detection("person", 0.7, box_at(250)),
                    Detection("cup", 0.6, box_at(150)),
                ]
            )
        )
        assert result["total"] == 3
        person, cup = result["objects"]
        assert person == {
            "label": "person",
            "count": 2,
            "confidence": 0.9,
            "positions": ["left", "right"],
        }
        assert cup["label"] == "cup"
        assert "2 person (left, right)" in result["summary"]
        json.dumps(result)

    def test_empty_scene_points_to_take_photo(self):
        result = summarize(make_result([]))
        assert result["success"] is True
        assert result["objects"] == []
        assert "take_photo" in result["note"]


def make_runtime(stats, detector=None, **settings):
    settings = VisionSettings(detector=DetectorSettings(), inference_rate=100.0, **settings)
    runtime = VisionRuntime(settings, make_service(stats))
    runtime._create_detector = lambda: detector or FakeDetector(
        [Detection("cup", 0.8, box_at(10))]
    )
    runtime.detection._detector_factory = runtime._create_detector
    return runtime


class TestRuntime:
    def test_query_starts_on_demand(self):
        stats = make_stats()
        runtime = make_runtime(stats)
        result = runtime.query()
        assert result["success"] and result["objects"][0]["label"] == "cup"
        runtime.shutdown()
        assert stats["open_now"] == 0

    def test_idle_timeout_releases_camera(self):
        stats = make_stats()
        runtime = make_runtime(stats, idle_stop_s=0.05)
        runtime.query()
        deadline = time.monotonic() + 3
        while stats["open_now"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert stats["open_now"] == 0
        assert not runtime.detection.running
        runtime.shutdown()

    def test_keep_alive_survives_idle_timeout(self):
        stats = make_stats()
        runtime = make_runtime(stats, idle_stop_s=0.05)
        runtime.set_keep_alive(True)
        runtime.start()
        runtime.query()
        time.sleep(0.2)
        assert runtime.detection.running
        runtime.shutdown()

    def test_disabled(self):
        runtime = make_runtime(make_stats(), enabled=False)
        assert runtime.query()["success"] is False

    def test_missing_model_message_reaches_the_llm(self):
        stats = make_stats()
        runtime = make_runtime(stats)

        def broken():
            raise DetectorError("run export_yolo")

        runtime.detection._detector_factory = broken
        result = runtime.query()
        assert result == {"success": False, "message": "run export_yolo"}
        runtime.shutdown()

    def test_camera_failure_is_reported(self, monkeypatch):
        monkeypatch.setattr(camera_module, "_RECONNECT_MIN_S", 30)
        runtime = make_runtime(make_stats(), detect_timeout_s=0.1)
        runtime.camera = make_service(make_stats(), open_ok=False)
        runtime.detection._camera = runtime.camera
        result = runtime.query()
        assert result["success"] is False
        assert "camera" in result["message"].lower()
        runtime.shutdown()


class TestTool:
    def make_tool(self, result):
        tools = []
        register_vision_tools(tools.append, SimpleNamespace(query=lambda: result))
        return tools[0]

    async def test_call_returns_json_text(self):
        tool = self.make_tool({"success": True, "summary": "Detected: 1 cup (left)."})
        response = json.loads(await tool.call({}))
        assert response["isError"] is False
        payload = json.loads(response["content"][0]["text"])
        assert payload["summary"] == "Detected: 1 cup (left)."

    def test_description_tells_the_llm_when_to_use_it(self):
        tool = self.make_tool({})
        assert tool.name == "detect_objects"
        assert "in front of" in tool.description
        assert "take_photo" in tool.description

    async def test_query_does_not_block_the_event_loop(self):
        ticks = []

        def slow_query():
            time.sleep(0.15)
            return {"success": True}

        tools = []
        register_vision_tools(tools.append, SimpleNamespace(query=slow_query))

        async def ticker():
            for _ in range(10):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        await asyncio.gather(tools[0].call({}), ticker())
        gaps = np.diff(ticks)
        assert gaps.max() < 0.1


class TestConfig:
    class Config:
        def __init__(self, values):
            self.values = values

        def get_config(self, path, default=None):
            return self.values.get(path, default)

    def test_defaults_match_requirements(self):
        settings = load_vision_settings(self.Config({}))
        assert settings.detector.input_size == 320
        assert settings.detector.num_threads == 2
        assert 3 <= settings.inference_rate <= 5
        assert 10 <= settings.preview.fps <= 15

    def test_bad_values_fall_back(self):
        settings = load_vision_settings(
            self.Config(
                {
                    "VISION.BACKEND": "gpu",
                    "VISION.NUM_THREADS": "many",
                    "VISION.INPUT_SIZE": 333,
                    "VISION.PREVIEW.CORNER": "middle",
                }
            )
        )
        assert settings.detector.backend == "auto"
        assert settings.detector.num_threads == 2
        assert settings.detector.input_size == 320
        assert settings.preview.corner == "top-right"

    def test_vision_section_exists(self):
        assert "VISION" in ConfigManager.DEFAULT_CONFIG


def test_vl_camera_failure_falls_back_to_normal_camera(monkeypatch):
    import builtins
    import sys

    from src.mcp.tools.camera import normal_camera, register

    class Config:
        def get_config(self, path, default=None):
            return {"CAMERA.VLapi_key": "key", "CAMERA.Local_VL_url": "url"}.get(path)

    real_import = builtins.__import__

    def fail_vl(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "vl_camera" and level == 1:
            raise ImportError("no cloud client")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_vl)
    monkeypatch.setattr(register, "get_config", lambda: Config())
    monkeypatch.setattr(normal_camera.NormalCamera, "__init__", lambda self: None)
    monkeypatch.delitem(sys.modules, "src.mcp.tools.camera.vl_camera", raising=False)
    assert isinstance(register.create_camera(), normal_camera.NormalCamera)
