"""Camera preview: frame composition and the QML overlay."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QSize, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.ui.gui.services.camera_preview import (  # noqa: E402
    PROVIDER_ID,
    CameraPreviewProvider,
    CameraPreviewService,
    compose_preview,
    to_qimage,
)
from src.vision.camera_service import CameraFrame  # noqa: E402
from src.vision.detection_service import DetectionResult  # noqa: E402
from src.vision.detector import Detection  # noqa: E402
from src.vision.settings import PreviewSettings, VisionSettings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QML_DIR = ROOT / "src" / "ui" / "gui" / "qml"


@pytest.fixture(scope="module")
def qt_app():
    os.environ["QT_QUICK_BACKEND"] = "software"
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
    yield QApplication.instance() or QApplication(["tests", "-platform", "offscreen"])


def run_events(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(condition, timeout_ms=3000):
    deadline = time.monotonic() + timeout_ms / 1000
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        run_events(10)


class FakeCamera:
    def __init__(self, frames=True, width=640, height=480):
        self.frames = frames
        self.acquired = 0
        self.reads = 0
        self.image = np.full((height, width, 3), 90, np.uint8)
        self.sequence = 0

    def acquire(self, client):
        self.acquired += 1

    def release(self, client):
        self.acquired -= 1

    def wait_for_frame(self, after, timeout):
        if not self.frames:
            time.sleep(min(timeout, 0.01))
            return None
        self.reads += 1
        self.sequence += 1
        time.sleep(0.001)
        return CameraFrame(self.image, self.sequence, time.monotonic())


def make_runtime(camera=None, error=None, result=None, **preview):
    return SimpleNamespace(
        camera=camera or FakeCamera(),
        detection=SimpleNamespace(error=error, latest=lambda: result),
        settings=VisionSettings(preview=PreviewSettings(**preview)),
    )


def test_compose_scales_to_width_and_keeps_aspect():
    image = np.zeros((480, 640, 3), np.uint8)
    assert compose_preview(image, 240).shape == (180, 240, 3)


def test_compose_draws_boxes_without_touching_the_shared_frame():
    image = np.zeros((480, 640, 3), np.uint8)
    original = image.copy()
    detection = Detection("cup", 0.8, (100, 100, 300, 300))
    preview = compose_preview(image, 240, (detection,))
    assert preview.any()
    assert np.array_equal(image, original)


def test_compose_can_hide_boxes():
    image = np.zeros((480, 640, 3), np.uint8)
    detection = Detection("cup", 0.8, (100, 100, 300, 300))
    assert not compose_preview(image, 240, (detection,), show_boxes=False).any()


def test_box_at_the_frame_edge_does_not_crash():
    image = np.zeros((480, 640, 3), np.uint8)
    compose_preview(image, 240, (Detection("cup", 0.8, (0, 0, 640, 480)),))


def test_qimage_is_independent_of_the_numpy_buffer(qt_app):
    array = np.zeros((10, 20, 3), np.uint8)
    array[:, :, 2] = 255  # BGR: red
    image = to_qimage(array)
    array[:] = 0
    assert (image.width(), image.height()) == (20, 10)
    assert image.pixelColor(5, 5).red() == 255


def test_provider_returns_latest_image(qt_app):
    provider = CameraPreviewProvider()
    provider.set_image(to_qimage(np.zeros((10, 20, 3), np.uint8)))
    size = QSize()
    assert provider.requestImage("frame/1", size, QSize()).width() == 20
    assert (size.width(), size.height()) == (20, 10)


class TestService:
    def test_becomes_available_with_frames(self, qt_app):
        runtime = make_runtime()
        service = CameraPreviewService(runtime)
        service.start()
        wait_until(lambda: service.available)
        assert service.aspect == pytest.approx(0.75)
        first = service.frameId
        wait_until(lambda: service.frameId > first)
        service.stop()
        assert not service.available
        assert runtime.camera.acquired == 0

    def test_frame_rate_is_capped(self, qt_app):
        runtime = make_runtime(fps=10)
        service = CameraPreviewService(runtime)
        service.start()
        run_events(500)
        service.stop()
        assert 2 <= runtime.camera.reads <= 8

    def test_hidden_when_camera_has_no_frames(self, qt_app):
        camera = FakeCamera()
        service = CameraPreviewService(make_runtime(camera))
        service.start()
        wait_until(lambda: service.available)
        camera.frames = False
        wait_until(lambda: not service.available)
        camera.frames = True
        wait_until(lambda: service.available)
        service.stop()

    def test_vision_failure_hides_and_releases_camera(self, qt_app):
        runtime = make_runtime(error="no model")
        service = CameraPreviewService(runtime)
        service.start()
        run_events(100)
        assert not service.available
        assert runtime.camera.acquired == 0
        service.stop()

    def test_stale_boxes_are_not_drawn(self, qt_app):
        old = DetectionResult(
            (Detection("cup", 0.8, (0, 0, 10, 10)),), 640, 480, time.monotonic() - 60, 5
        )
        service = CameraPreviewService(make_runtime(result=old))
        assert service._fresh_detections() == ()

    def test_fresh_boxes_are_drawn(self, qt_app):
        detection = Detection("cup", 0.8, (0, 0, 10, 10))
        result = DetectionResult((detection,), 640, 480, time.monotonic(), 5)
        service = CameraPreviewService(make_runtime(result=result))
        assert service._fresh_detections() == (detection,)


@pytest.fixture
def face_with_preview(qt_app):
    from PySide6.QtQml import QQmlApplicationEngine

    from src.ui.gui.models import FaceModel, MainModel, load_face_config

    class StubConfig:
        def get_config(self, path, default=None):
            return default

    face_config = load_face_config(StubConfig())
    face_config.update(sleepyAfterS=0, asleepAfterS=0)
    camera = FakeCamera()
    preview = CameraPreviewService(make_runtime(camera, corner="bottom-left"))
    warnings = []
    model, main_model = FaceModel(face_config), MainModel()

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda items: warnings.extend(w.toString() for w in items))
    context = engine.rootContext()
    context.setContextProperty("eventBridge", None)
    context.setContextProperty("mainModel", main_model)
    context.setContextProperty("faceModel", model)
    context.setContextProperty("cameraPreview", preview)
    engine.addImageProvider(PROVIDER_ID, preview.provider)
    engine.addImportPath(str(QML_DIR))
    engine.load(str(QML_DIR / "face_main.qml"))
    roots = engine.rootObjects()
    assert roots, warnings
    roots[0].show()
    yield preview, camera, roots[0], warnings
    preview.stop()
    roots[0].hide()
    engine.deleteLater()
    run_events(0)


def find_preview_item(window):
    pending = [window.contentItem()]
    while pending:
        item = pending.pop()
        if item.property("preview") is not None:
            return item
        pending.extend(item.childItems())
    return None


class TestOverlay:
    def test_overlay_follows_availability_and_corner(self, face_with_preview):
        preview, camera, window, warnings = face_with_preview
        assert not preview.available
        preview.start()
        wait_until(lambda: preview.available)
        run_events(100)
        item = find_preview_item(window)
        assert item.isVisible()
        assert item.width() == 240
        assert item.height() == 180
        assert item.x() == 16
        assert item.y() == 600 - 180 - 16

        camera.frames = False
        wait_until(lambda: not preview.available)
        run_events(50)
        assert not item.isVisible()
        assert not warnings

    def test_face_keeps_running_without_preview_frames(self, face_with_preview):
        preview, camera, window, warnings = face_with_preview
        camera.frames = False
        preview.start()
        run_events(100)
        assert not preview.available
        assert window.isVisible()
        assert not warnings


class TestAttachVision:
    def attach(self, style="robot", enabled=True, preview_enabled=True):
        from src.ui.gui.manager import GuiViewManager

        runtime = make_runtime(enabled=preview_enabled)
        runtime.settings.enabled = enabled
        manager = SimpleNamespace(
            _main=SimpleNamespace(
                face_model=SimpleNamespace(config={"style": style})
            ),
            _preview=None,
        )
        return GuiViewManager.attach_vision(manager, runtime), manager._preview

    def test_robot_face_keeps_detection_running(self, qt_app):
        keep_alive, preview = self.attach()
        assert keep_alive and preview is not None

    @pytest.mark.parametrize(
        "kwargs",
        [{"style": "gif"}, {"enabled": False}, {"preview_enabled": False}],
    )
    def test_otherwise_detection_stays_on_demand(self, qt_app, kwargs):
        keep_alive, preview = self.attach(**kwargs)
        assert not keep_alive and preview is None
