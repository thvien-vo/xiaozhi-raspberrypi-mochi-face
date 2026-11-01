"""Robot face: state machine, presenter routing, presets and QML loading."""

import os
import re
from pathlib import Path

import pytest

from src.constants.constants import DeviceState
from src.plugins.ui_presenter import UiPresenter
from src.ui.gui.services.face_state import FaceStateMachine
from src.utils.config_manager import ConfigManager

ROOT = Path(__file__).resolve().parent.parent
QML_DIR = ROOT / "src" / "ui" / "gui" / "qml"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class StubConfig:
    """Answers every FACE.* lookup with the default."""

    def get_config(self, path, default=None):
        return default


def make_machine(clock=None):
    return FaceStateMachine(
        sleepy_after_s=10,
        asleep_after_s=20,
        thinking_timeout_s=5,
        clock=clock or Clock(),
    )


class TestFaceStateMachine:
    def test_idle_goes_sleepy_then_asleep(self):
        clock = Clock()
        machine = make_machine(clock)
        clock.now += 9
        machine.tick()
        assert machine.activity == "awake"
        clock.now += 1
        machine.tick()
        assert machine.activity == "sleepy"
        clock.now += 10
        machine.tick()
        assert machine.activity == "asleep"

    def test_interaction_wakes_up(self):
        clock = Clock()
        machine = make_machine(clock)
        clock.now += 30
        machine.tick()
        machine.touch()
        machine.tick()
        assert machine.activity == "awake"

    def test_wake_word_listening_wakes_up(self):
        clock = Clock()
        machine = make_machine(clock)
        clock.now += 30
        machine.tick()
        machine.set_device_state("listening")
        assert machine.activity == "awake"
        assert machine.visual_state == "listening"

    def test_no_sleep_during_conversation(self):
        clock = Clock()
        machine = make_machine(clock)
        machine.set_device_state("speaking")
        clock.now += 100
        machine.tick()
        assert machine.activity == "awake"
        # Time spent speaking does not count towards inactivity afterwards
        machine.set_device_state("idle")
        clock.now += 5
        machine.tick()
        assert machine.activity == "awake"

    def test_zero_disables_sleep(self):
        clock = Clock()
        machine = FaceStateMachine(0, 0, 5, clock=clock)
        clock.now += 10000
        machine.tick()
        assert machine.activity == "awake"

    def test_thinking_between_user_text_and_reply(self):
        machine = make_machine()
        machine.set_device_state("listening")
        machine.on_user_text()
        assert machine.visual_state == "thinking"
        machine.set_device_state("speaking")
        assert machine.visual_state == "speaking"

    def test_thinking_needs_listening_state(self):
        machine = make_machine()
        machine.on_user_text()
        assert machine.visual_state == "idle"

    def test_thinking_times_out(self):
        clock = Clock()
        machine = make_machine(clock)
        machine.set_device_state("listening")
        machine.on_user_text()
        clock.now += 5
        machine.tick()
        assert machine.visual_state == "listening"

    def test_unknown_device_state_is_idle(self):
        machine = make_machine()
        machine.set_device_state("something-new")
        assert machine.visual_state == "idle"


class FaceViewport:
    """Records calls, including the optional robot face extensions."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, *args))

        return record


class PlainViewport:
    """A CLI-like viewport without the robot face extensions."""

    def __init__(self):
        self.chat = []

    def set_chat_text(self, text):
        self.chat.append(text)

    def set_emotion(self, emotion):
        pass

    def set_status(self, status, connected=True):
        pass


class TestPresenterRouting:
    def make(self):
        viewport = FaceViewport()
        return UiPresenter(viewport), viewport

    def test_assistant_sentence_reaches_face(self):
        presenter, viewport = self.make()
        presenter.show_protocol_message(
            {"type": "tts", "state": "sentence_start", "text": "Hello"}
        )
        assert ("set_speech_text", "tts", "Hello") in viewport.calls
        assert ("set_chat_text", "Hello") in viewport.calls

    def test_user_speech_is_marked_as_stt(self):
        presenter, viewport = self.make()
        presenter.show_protocol_message({"type": "stt", "text": "Hi"})
        assert ("set_speech_text", "stt", "Hi") in viewport.calls

    def test_sentence_end_does_not_restart_typewriter(self):
        presenter, viewport = self.make()
        presenter.show_protocol_message(
            {"type": "tts", "state": "sentence_end", "text": "Hello"}
        )
        assert not [c for c in viewport.calls if c[0] == "set_speech_text"]

    def test_tts_stop_ends_speech(self):
        presenter, viewport = self.make()
        presenter.show_protocol_message({"type": "tts", "state": "stop"})
        assert ("end_speech",) in viewport.calls

    def test_emotion_survives_speaking_state(self):
        presenter, viewport = self.make()
        presenter.show_protocol_message({"type": "llm", "emotion": "happy"})
        presenter.show_device_state(DeviceState.SPEAKING)
        emotions = [c[1] for c in viewport.calls if c[0] == "set_emotion"]
        assert emotions == ["happy"]

    @pytest.mark.parametrize("state", [DeviceState.IDLE, DeviceState.LISTENING])
    def test_other_states_reset_to_neutral(self, state):
        presenter, viewport = self.make()
        presenter.show_device_state(state)
        assert ("set_emotion", "neutral") in viewport.calls
        assert ("set_device_state", state.value) in viewport.calls

    def test_viewports_without_face_methods_still_work(self):
        viewport = PlainViewport()
        presenter = UiPresenter(viewport)
        presenter.show_protocol_message(
            {"type": "tts", "state": "sentence_start", "text": "Hello"}
        )
        presenter.show_protocol_message({"type": "tts", "state": "stop"})
        presenter.show_device_state(DeviceState.SPEAKING)
        presenter.show_notification_subtitle("Notice")
        assert viewport.chat == ["Hello", "Notice"]


def test_default_config_has_face_section():
    face = ConfigManager.DEFAULT_CONFIG["FACE"]
    assert face["STYLE"] == "robot"
    assert (face["WINDOW_WIDTH"], face["WINDOW_HEIGHT"]) == (1024, 600)


@pytest.fixture(scope="module")
def qt_app():
    os.environ["QT_QUICK_BACKEND"] = "software"
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["tests", "-platform", "offscreen"])


class TestEmotionMapping:
    def test_known_emotions_match_assets(self, qt_app):
        from src.ui.gui.services import EmotionService

        assets = {p.stem for p in (ROOT / "assets" / "emojis").glob("*.gif")}
        assert assets <= set(EmotionService.KNOWN_EMOTIONS)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("happy", "happy"), (" HAPPY ", "happy"), ("nope", "neutral"), ("", "neutral")],
    )
    def test_normalize(self, qt_app, raw, expected):
        from src.ui.gui.services import EmotionService

        assert EmotionService.normalize_emotion(raw) == expected
        assert EmotionService.normalize_emotion(None) == "neutral"

    def test_every_emotion_has_a_preset(self, qt_app):
        from PySide6.QtQml import QJSEngine

        from src.ui.gui.services import EmotionService

        source = (QML_DIR / "face" / "FacePresets.js").read_text(encoding="utf-8")
        engine = QJSEngine()
        result = engine.evaluate(re.sub(r"^\.pragma library$", "", source, flags=re.M))
        assert not result.isError(), result.toString()

        active = engine.globalObject().property("activeName")
        compose = engine.globalObject().property("compose")
        for name in EmotionService.KNOWN_EMOTIONS:
            resolved = active.call([name, "idle", "awake"]).toString()
            assert resolved == name
            pose = compose.call([name, "idle"])
            for side in ("left", "right"):
                assert pose.property(side).property("w").toNumber() > 0

        assert active.call(["nope", "idle", "awake"]).toString() == "neutral"
        assert active.call(["happy", "idle", "asleep"]).toString() == "asleep"
        assert active.call(["happy", "idle", "sleepy"]).toString() == "sleepy"
        assert active.call(["neutral", "thinking", "awake"]).toString() == "thinking"
        assert active.call(["happy", "thinking", "awake"]).toString() == "happy"


def run_events(ms):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def face_window(qt_app):
    from PySide6.QtQml import QQmlApplicationEngine

    from src.ui.gui.models import FaceModel, MainModel, load_face_config

    face_config = load_face_config(StubConfig())
    face_config.update(sleepyAfterS=0, asleepAfterS=0, typewriterCps=1000)
    face_config.update(textHoldMs=0)
    model = FaceModel(face_config)
    main_model = MainModel()
    warnings = []

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda items: warnings.extend(w.toString() for w in items))
    context = engine.rootContext()
    context.setContextProperty("eventBridge", None)
    context.setContextProperty("mainModel", main_model)
    context.setContextProperty("faceModel", model)
    context.setContextProperty("cameraPreview", None)
    engine.addImportPath(str(QML_DIR))
    engine.load(str(QML_DIR / "face_main.qml"))
    roots = engine.rootObjects()
    assert roots, warnings
    roots[0].show()
    yield model, roots[0], warnings
    roots[0].hide()
    engine.deleteLater()
    run_events(0)


class TestFaceWindow:
    def test_window_uses_configured_size(self, face_window):
        _, window, warnings = face_window
        assert (window.width(), window.height()) == (1024, 600)
        assert not warnings

    def test_all_emotions_and_states_run_without_qml_errors(self, face_window):
        from src.ui.gui.services import EmotionService

        model, _, warnings = face_window
        for emotion in EmotionService.KNOWN_EMOTIONS:
            model.set_emotion(emotion)
            run_events(15)
        for state in ("listening", "speaking", "notifying", "idle"):
            model.set_device_state(state)
            run_events(15)
        for activity in ("sleepy", "asleep", "awake"):
            model.force_activity(activity)
            run_events(15)
        assert not warnings

    def test_text_clears_after_speech_ends(self, face_window):
        model, _, warnings = face_window
        model.set_device_state("listening")
        model.set_speech_text("stt", "Hello robot")
        model.set_device_state("speaking")
        model.set_speech_text("tts", "Hi")
        assert model.speechActive
        run_events(50)
        assert model.speechText == "Hi"
        model.end_speech()
        run_events(700)
        assert model.speechText == ""
        assert model.userText == ""
        assert not warnings

    def test_same_sentence_twice_restarts_typewriter(self, face_window):
        model, _, _ = face_window
        model.set_speech_text("tts", "Again")
        first = model.speechSerial
        model.set_speech_text("tts", "Again")
        assert model.speechSerial == first + 1


def test_invalid_face_config_falls_back_to_defaults(qt_app):
    from src.ui.gui.models import load_face_config

    class BadConfig:
        def get_config(self, path, default=None):
            return "not a number" if path.endswith("WINDOW_WIDTH") else default

    assert load_face_config(BadConfig())["windowWidth"] == 1024


def test_demo_script_covers_all_emotions_and_states(qt_app):
    from src.ui.gui.face_demo import build_demo_steps
    from src.ui.gui.services import EmotionService

    steps = build_demo_steps()
    shown = {args[0] for method, args in steps if method == "set_emotion"}
    assert shown >= set(EmotionService.KNOWN_EMOTIONS)
    states = {args[0] for method, args in steps if method == "set_device_state"}
    assert states >= {"idle", "listening", "speaking"}
    assert ("set_speech_text", ("stt", "What can you see in front of you?")) in steps
    assert ("force_activity", ("asleep",)) in steps
