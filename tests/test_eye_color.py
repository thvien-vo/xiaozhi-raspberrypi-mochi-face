"""Eye color and the face mirror on the web panel: colors, config, commands, voice tool,
face snapshots and the QML face."""

import asyncio
import json
import os
import re
import socket
import threading
from pathlib import Path

import pytest

from src.mcp.tools.face import register_face_tools
from src.mcp.tools.face.register import set_eye_color
from src.ui import face_color as fc
from src.ui.face_color import EyeColorState
from src.web_panel.backend import AppState, RobotBackend
from src.web_panel.commands import CommandError, parse_command
from src.web_panel.face_stream import FaceFrameHub
from src.web_panel.server import presets_module
from src.web_panel.settings import WebSettings

ROOT = Path(__file__).resolve().parent.parent
QML_DIR = ROOT / "src" / "ui" / "gui" / "qml"


class DictConfig:
    """ConfigManager stand-in backed by a nested dict."""

    def __init__(self, data=None):
        self.data = data or {}

    def get_config(self, path, default=None):
        value = self.data
        for key in path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    def update_config(self, path, value):
        node = self.data
        *parts, last = path.split(".")
        for part in parts:
            node = node.setdefault(part, {})
        node[last] = value
        return True


# ----- Color conversion and validation -----

@pytest.mark.parametrize(
    ("raw", "expected"),
    [("#FFF", "#ffffff"), ("abc", "#aabbcc"), ("#3D9BFF", "#3d9bff"), (" #00ff88 ", "#00ff88")],
)
def test_normalize_hex(raw, expected):
    assert fc.normalize_hex(raw) == expected


@pytest.mark.parametrize("raw", ["", "#12", "#gggggg", "red; drop", "#1234567", None, 5, "url(x)"])
def test_bad_hex_is_refused(raw):
    with pytest.raises(ValueError):
        fc.normalize_hex(raw)


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("tím", "purple"), ("Màu Tím", "purple"), ("xanh lá", "green"), ("xanh lá cây", "green"),
        ("Xanh dương", "blue"), ("xanh da trời", "blue"), ("hồng", "pink"), ("cam", "orange"),
        ("đỏ", "red"), ("ĐỎ", "red"), ("trắng", "white"), ("vàng", "yellow"), ("xanh ngọc", "cyan"),
        ("sang màu vàng", "yellow"), ("purple", "purple"),
    ],
)
def test_vietnamese_color_names(raw, key):
    assert fc.parse_color(raw) == fc.PALETTE_HEX[key]


def test_unknown_color_name():
    with pytest.raises(ValueError):
        fc.parse_color("màu khủng long")


def test_dark_colors_are_lifted_to_stay_visible_on_black():
    for dark in ("#000000", "#0000ff", "#200010", "#003300"):
        lifted = fc.ensure_visible(dark)
        assert fc.relative_luminance(lifted) >= fc.MIN_LUMINANCE
        assert fc.relative_luminance(lifted) < fc.MIN_LUMINANCE + 0.02  # Only as much as needed
    # Pure blue keeps its hue (blue stays the largest channel)
    r, g, b = fc.hex_to_rgb(fc.ensure_visible("#0000ff"))
    assert b == 255 and r < 200 and g < 200
    for _key, _label, color in fc.PALETTE:
        assert fc.ensure_visible(color) == color  # The palette is already visible


def test_rainbow_turns_through_the_hues():
    assert fc.rainbow_color(0) == "#ff4d4d"
    assert fc.rainbow_color(fc.RAINBOW_PERIOD_S) == fc.rainbow_color(0)
    colors = {fc.rainbow_color(t) for t in range(0, 12)}
    assert len(colors) == 12
    for t in range(12):
        assert fc.relative_luminance(fc.rainbow_color(t)) >= fc.MIN_LUMINANCE


@pytest.mark.parametrize(
    ("text", "result"),
    [
        ("cầu vồng", (None, "rainbow")), ("mắt cầu vồng", (None, "rainbow")), ("rainbow", (None, "rainbow")),
        ("theo cảm xúc", (None, "emotion")), ("màu theo cảm xúc", (None, "emotion")),
        ("cố định", (None, "fixed")), ("mặc định", ("#ffffff", "fixed")),
        ("tắt cầu vồng", (None, "fixed")), ("bỏ màu theo cảm xúc", (None, "fixed")),
        ("bật mắt cầu vồng", (None, "rainbow")), ("cho mắt đổi màu theo cảm xúc", (None, "emotion")),
        ("đổi màu mắt sang màu tím", (fc.PALETTE_HEX["purple"], "fixed")),
        ("mắt màu xanh lá cây đi", (fc.PALETTE_HEX["green"], "fixed")),
        ("cho mắt màu cam", (fc.PALETTE_HEX["orange"], "fixed")),
        ("về màu mắt mặc định", ("#ffffff", "fixed")),
        ("tím", (fc.PALETTE_HEX["purple"], "fixed")), ("#33aaff", ("#33aaff", "fixed")),
    ],
)
def test_parse_request(text, result):
    assert fc.parse_request(text) == result


def test_effective_color_per_mode():
    assert fc.effective_color("#3d9bff", "fixed", "angry", 0) == "#3d9bff"
    assert fc.effective_color("#3d9bff", "emotion", "angry", 0) == fc.PALETTE_HEX["red"]
    assert fc.effective_color("#3d9bff", "emotion", "happy", 0) == fc.PALETTE_HEX["yellow"]
    assert fc.effective_color("#3d9bff", "emotion", "sad", 0) == fc.PALETTE_HEX["blue"]
    assert fc.effective_color("#3d9bff", "emotion", "nope", 0) == "#3d9bff"
    assert fc.effective_color("#3d9bff", "rainbow", "angry", 3) == fc.rainbow_color(3)


def test_active_expression_matches_the_qml_presets(qt_app):
    """Python's copy of activeName() must agree with FacePresets.js for every case."""
    qt = pytest.importorskip("PySide6.QtQml")
    source = (QML_DIR / "face" / "FacePresets.js").read_text(encoding="utf-8")
    engine = qt.QJSEngine()
    assert not engine.evaluate(re.sub(r"^\.pragma library$", "", source, flags=re.M)).isError()
    active = engine.globalObject().property("activeName")
    names = list(fc.EMOTION_COLORS) + ["nope"]
    for emotion in names:
        for visual in ("idle", "listening", "thinking", "speaking"):
            for activity in ("awake", "sleepy", "asleep"):
                js = active.call([emotion, visual, activity]).toString()
                assert fc.active_expression(emotion, visual, activity) == js, (emotion, visual, activity)
    # Every preset has an emotion-mode color
    presets = engine.evaluate("Object.keys(EMOTIONS)").toVariant()
    assert set(presets) == set(fc.EMOTION_COLORS)


def test_presets_module_for_the_page():
    source = (QML_DIR / "face" / "FacePresets.js").read_text(encoding="utf-8")
    module = presets_module(source)
    assert ".pragma" not in module and module.rstrip().endswith("export { EMOTIONS, activeName, compose };")


# ----- State, listeners and config -----

def test_state_validates_notifies_and_saves():
    saved = []
    state = EyeColorState(save=lambda path, value: saved.append((path, value)))
    seen = []
    state.add_listener(lambda color, mode: seen.append((color, mode)))
    assert state.set(color="#B46BFF") == ("#b46bff", "fixed")
    assert state.set(mode="rainbow") == ("#b46bff", "rainbow")
    with pytest.raises(ValueError):
        state.set(color="javascript:alert(1)")
    with pytest.raises(ValueError):
        state.set(mode="disco")
    assert seen == [("#b46bff", "fixed"), ("#b46bff", "rainbow")]
    assert saved == [("FACE.EYE_COLOR", "#b46bff"), ("FACE.EYE_COLOR_MODE", "rainbow")]
    state.set(mode="rainbow")  # No change: nothing saved or notified again
    assert len(saved) == 2 and len(seen) == 2


def test_preview_then_final_value_is_saved_once():
    saved = []
    state = EyeColorState(save=lambda path, value: saved.append((path, value)))
    for c in ("#110000", "#ff0000", "#ff4d4d"):
        state.set(color=c, persist=False)
    assert saved == []
    state.set(color="#ff4d4d")  # Same as the preview, but not yet in the config
    assert saved == [("FACE.EYE_COLOR", "#ff4d4d")]


def test_config_roundtrip():
    config = DictConfig({"FACE": {}})
    state = EyeColorState.from_config(config, save=config.update_config)
    assert (state.color, state.mode) == (fc.DEFAULT_COLOR, "fixed")
    state.set(color="#3dff7a", mode="emotion")
    assert config.data["FACE"] == {"EYE_COLOR": "#3dff7a", "EYE_COLOR_MODE": "emotion"}
    again = EyeColorState.from_config(config)  # Restart
    assert (again.color, again.mode) == ("#3dff7a", "emotion")


@pytest.mark.parametrize("bad", [{"EYE_COLOR": "not a color", "EYE_COLOR_MODE": 7}, {"EYE_COLOR": None}])
def test_bad_config_falls_back_to_defaults(bad):
    state = EyeColorState.from_config(DictConfig({"FACE": bad}))
    assert (state.color, state.mode) == (fc.DEFAULT_COLOR, "fixed")


def test_default_config_has_eye_color():
    from src.utils.config_manager import ConfigManager

    face = ConfigManager.DEFAULT_CONFIG["FACE"]
    assert face["EYE_COLOR"] == fc.DEFAULT_COLOR and face["EYE_COLOR_MODE"] == "fixed"
    assert ConfigManager.DEFAULT_CONFIG["WEB"]["FACE_STREAM_FPS"] == 10


def test_listeners_on_other_threads():
    state = EyeColorState()
    seen = []
    state.add_listener(lambda c, m: seen.append(threading.current_thread().name))
    t = threading.Thread(target=lambda: state.set(color="#ff6fb5"), name="web")
    t.start()
    t.join()
    assert seen == ["web"]


# ----- WebSocket commands and backend -----

def parse(obj):
    return parse_command(json.dumps(obj), 90, 45, ("neutral", "happy"))


def test_parse_eye_color_commands():
    assert parse({"type": "eye_color", "color": "#ABCDEF"}).args == {"color": "#abcdef", "mode": None, "save": True}
    assert parse({"type": "eye_color", "mode": "rainbow"}).args == {"color": None, "mode": "rainbow", "save": True}
    assert parse({"type": "eye_color", "color": "#fff", "mode": "fixed", "save": False}).args["save"] is False
    assert parse({"type": "face_stream", "enabled": True}).args == {"enabled": True}


@pytest.mark.parametrize(
    "obj",
    [
        {"type": "eye_color"},
        {"type": "eye_color", "color": "red"},          # Names only by voice; the page sends hex
        {"type": "eye_color", "color": "#12345"},
        {"type": "eye_color", "color": 0xFF0000},
        {"type": "eye_color", "mode": "disco"},
        {"type": "eye_color", "mode": ["rainbow"]},
        {"type": "face_stream", "enabled": "yes"},
    ],
)
def test_bad_eye_color_commands(obj):
    with pytest.raises(CommandError):
        parse(obj)


def test_backend_eye_color_and_snapshot():
    eye = EyeColorState()
    changes = []
    backend = RobotBackend(app_state=AppState(emotions=("neutral",)), eye_colors=eye)
    backend.add_listener(lambda: changes.append(1))
    assert backend.handle(parse({"type": "eye_color", "color": "#ff9a3c"}))["ok"]
    assert eye.color == "#ff9a3c" and changes
    snap = backend.snapshot()["face"]
    assert snap["eye"] == {"color": "#ff9a3c", "mode": "fixed", "label": "Cam"} and snap["stream"] is False
    consts = backend.face_constants()
    assert {p["color"] for p in consts["palette"]} >= {"#ffffff", "#b46bff"}
    assert consts["emotion_colors"]["angry"] == fc.PALETTE_HEX["red"]
    # Without the GUI face there is nothing to color
    plain = RobotBackend(app_state=AppState())
    assert plain.handle(parse({"type": "eye_color", "mode": "rainbow"}))["ok"] is False
    assert plain.snapshot()["face"]["eye"] is None


def test_app_state_reports_the_shown_expression():
    state = AppState(emotions=("neutral", "happy"))
    state.update(emotion="neutral", face_visual="thinking")
    assert state.snapshot()["expression"] == "thinking"
    state.update(face_activity="asleep")
    assert state.snapshot()["expression"] == "asleep"


def test_frame_hub_turns_the_source_on_and_off():
    calls = []
    hub = FaceFrameHub(on_active=calls.append, fps=10)
    assert hub.available and hub.latest() == (None, 0)
    hub.add_viewer()
    hub.add_viewer()
    hub.remove_viewer()
    assert calls == [True]
    hub.remove_viewer()
    hub.remove_viewer()  # Extra removes are harmless
    assert calls == [True, False]
    hub.publish(b"\xff\xd8jpeg", 320, 135)
    assert hub.latest() == (b"\xff\xd8jpeg", 1) and hub.fresh()
    assert FaceFrameHub().available is False


# ----- Voice tool -----

def test_voice_tool():
    tools = []
    eye = EyeColorState()
    register_face_tools(tools.append, eye)
    assert [t.name for t in tools] == ["self.face.set_eye_color"]
    desc = tools[0].description
    assert "màu mắt" in desc and "cầu vồng" in desc and "tím" in desc
    answer = json.loads(tools[0].callback({"color": "màu tím"}))
    assert answer["success"] and eye.color == fc.PALETTE_HEX["purple"] and "tím" in answer["message"]
    answer = json.loads(set_eye_color(eye, "cầu vồng"))
    assert answer["success"] and eye.mode == "rainbow" and "cầu vồng" in answer["message"]
    assert eye.color == fc.PALETTE_HEX["purple"]  # Kept for when the rainbow is turned off
    json.loads(set_eye_color(eye, "theo cảm xúc"))
    assert eye.mode == "emotion"
    json.loads(set_eye_color(eye, "xanh lá"))
    assert (eye.color, eye.mode) == (fc.PALETTE_HEX["green"], "fixed")
    answer = json.loads(set_eye_color(eye, "màu khủng long"))
    assert answer["success"] is False and "xanh dương" in answer["message"]
    assert (eye.color, eye.mode) == (fc.PALETTE_HEX["green"], "fixed")
    json.loads(set_eye_color(eye, "mặc định"))
    assert (eye.color, eye.mode) == ("#ffffff", "fixed")


# ----- Server: presets module and binary face frames -----

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_streams_face_frames_to_subscribers():
    import aiohttp

    from src.utils.resource_finder import get_app_root
    from src.web_panel.server import WebPanelServer

    active = []
    hub = FaceFrameHub(on_active=active.append, fps=20)
    eye = EyeColorState()
    backend = RobotBackend(app_state=AppState(emotions=("neutral",), face_available=True), eye_colors=eye, face_frames=hub)
    port = free_port()
    server = WebPanelServer(backend, WebSettings(enabled=True, host="127.0.0.1", port=port), get_app_root() / "web_assets")
    assert server.start()

    async def scenario():
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/face_presets.js") as r:
                assert r.status == 200 and "export { EMOTIONS" in await r.text()
                assert r.headers["Content-Type"].startswith("text/javascript")
            async with http.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                hello = await ws.receive_json(timeout=3)
                assert hello["face"]["emotion_colors"]["sad"] == fc.PALETTE_HEX["blue"]
                state = await ws.receive_json(timeout=3)
                assert state["face"]["stream"] is True and state["face"]["eye"]["color"] == "#ffffff"
                await ws.send_json({"type": "face_stream", "enabled": True, "id": 1})
                for _ in range(50):
                    if active:
                        break
                    await asyncio.sleep(0.02)
                assert active == [True]
                hub.publish(b"\xff\xd8frame1", 4, 2)
                got = None
                for _ in range(20):
                    msg = await ws.receive(timeout=3)
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        got = msg.data
                        break
                assert got == b"\xff\xd8frame1"
                # Eye color from the page reaches the state and comes back in a snapshot
                await ws.send_json({"type": "eye_color", "color": "#b46bff", "id": 2})
                for _ in range(40):
                    msg = await ws.receive(timeout=3)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("type") == "state" and data["face"]["eye"]["color"] == "#b46bff":
                            break
                else:
                    raise AssertionError("eye color not broadcast")
            # Closing the page stops the snapshots
            for _ in range(50):
                if active == [True, False]:
                    break
                await asyncio.sleep(0.02)
            assert active == [True, False]

    try:
        asyncio.run(scenario())
        assert eye.color == "#b46bff"
    finally:
        server.stop()


def test_server_refuses_face_stream_without_a_face():
    import aiohttp

    from src.utils.resource_finder import get_app_root
    from src.web_panel.server import WebPanelServer

    backend = RobotBackend(app_state=AppState())
    port = free_port()
    server = WebPanelServer(backend, WebSettings(enabled=True, host="127.0.0.1", port=port), get_app_root() / "web_assets")
    assert server.start()

    async def scenario():
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                await ws.receive_json(timeout=3)
                await ws.receive_json(timeout=3)
                await ws.send_json({"type": "face_stream", "enabled": True, "id": 7})
                while True:
                    msg = await ws.receive_json(timeout=3)
                    if msg["type"] == "result":
                        assert msg["ok"] is False and msg["silent"] is True
                        break

    try:
        asyncio.run(scenario())
    finally:
        server.stop()


# ----- Qt: the QML face takes the color, snapshots show it -----

@pytest.fixture(scope="module")
def qt_app():
    os.environ["QT_QUICK_BACKEND"] = "software"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["tests", "-platform", "offscreen"])


def run_events(ms):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_face_model_follows_changes_from_other_threads(qt_app):
    from src.ui.gui.models import FaceModel, load_face_config

    eye = EyeColorState()
    model = FaceModel(load_face_config(DictConfig()), eye_colors=eye)
    seen = []
    model.eyeColorChanged.connect(lambda: seen.append((model.eyeColor, model.eyeColorMode)))
    t = threading.Thread(target=lambda: eye.set(color="#ff4d4d", mode="emotion"))
    t.start()
    t.join()
    assert model.eyeColor == "#ffffff"  # Not yet: applied on the Qt thread
    run_events(20)
    assert seen == [("#ff4d4d", "emotion")]
    assert model.emotionColors["angry"] == "#ff4d4d"


def _eye_pixels(jpeg: bytes):
    from PySide6.QtGui import QImage

    image = QImage.fromData(jpeg, "JPEG")
    total = [0, 0, 0]
    n = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            c = image.pixelColor(x, y)
            if max(c.red(), c.green(), c.blue()) > 110:
                total[0] += c.red()
                total[1] += c.green()
                total[2] += c.blue()
                n += 1
    return [v / n for v in total] if n else None, image.width(), image.height()


def test_snapshots_of_the_qml_face_show_the_eye_color(qt_app):
    from PySide6.QtQml import QQmlApplicationEngine

    from src.ui.gui.models import FaceModel, MainModel, load_face_config
    from src.ui.gui.services.face_snapshot import FaceSnapshotService, find_face_item

    eye = EyeColorState()
    config = load_face_config(DictConfig())
    config.update(sleepyAfterS=0, asleepAfterS=0)
    model = FaceModel(config, eye_colors=eye)
    warnings = []
    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda items: warnings.extend(w.toString() for w in items))
    ctx = engine.rootContext()
    for name, obj in {"eventBridge": None, "mainModel": MainModel(), "faceModel": model, "cameraPreview": None}.items():
        ctx.setContextProperty(name, obj)
    engine.addImportPath(str(QML_DIR))
    engine.load(str(QML_DIR / "face_main.qml"))
    window = engine.rootObjects()[0]
    window.show()
    frames = []
    service = FaceSnapshotService(find_face_item(window), lambda jpeg, w, h: frames.append(jpeg), fps=20)
    try:
        run_events(300)
        assert not frames  # Nothing until someone watches
        service.set_active(True)
        run_events(400)
        assert frames, "no snapshots"
        rgb, w, h = _eye_pixels(frames[-1])
        assert (w, h) == (320, 135) and rgb is not None and min(rgb) > 200  # White eyes

        eye.set(color="#3d9bff")
        run_events(700)  # Color animation is 260 ms
        rgb, _, _ = _eye_pixels(frames[-1])
        assert abs(rgb[0] - 0x3D) < 40 and abs(rgb[1] - 0x9B) < 40 and rgb[2] > 210, rgb

        eye.set(mode="emotion")
        model.set_emotion("angry")
        run_events(700)
        rgb, _, _ = _eye_pixels(frames[-1])
        assert rgb[0] > 200 and rgb[1] < 130 and rgb[2] < 130, rgb

        eye.set(mode="rainbow")
        model.set_emotion("loving")  # Heart eyes take the color too
        run_events(400)
        first, _, _ = _eye_pixels(frames[-1])
        run_events(1500)
        later, _, _ = _eye_pixels(frames[-1])
        assert sum(abs(a - b) for a, b in zip(first, later, strict=True)) > 20, (first, later)

        count = len(frames)
        service.set_active(False)
        run_events(300)
        assert len(frames) <= count + 1
        assert not warnings, warnings
    finally:
        service.set_active(False)
        window.hide()
        engine.deleteLater()
        run_events(0)
