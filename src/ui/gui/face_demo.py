"""Robot face preview: cycles all emotions and states without a server."""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCursor, QGuiApplication, Qt
from PySide6.QtWidgets import QApplication

from src.logging import get_logger
from src.ui.gui.models import FaceModel, MainModel, load_face_config
from src.ui.gui.qml_host import QmlAppHost
from src.ui.gui.services import EmotionService

logger = get_logger()

_SENTENCES = (
    "Hello! I am your desk robot. It is nice to meet you.",
    "This sentence is long enough to wrap over more than one line, so the "
    "typewriter text can be checked on the seven inch screen as well.",
)


def build_demo_steps() -> list[tuple[str, tuple]]:
    """Return the demo script as (FaceModel method, arguments) pairs."""
    steps: list[tuple[str, tuple]] = [
        ("set_device_state", ("idle",)),
        ("set_device_state", ("listening",)),
        ("set_speech_text", ("stt", "What can you see in front of you?")),
        ("set_device_state", ("speaking",)),
        ("set_speech_text", ("tts", _SENTENCES[0])),
        ("set_speech_text", ("tts", _SENTENCES[1])),
        ("end_speech", ()),
        ("set_device_state", ("idle",)),
    ]
    steps.append(("set_device_state", ("speaking",)))
    for emotion in EmotionService.KNOWN_EMOTIONS:
        steps.append(("set_emotion", (emotion,)))
        steps.append(("set_speech_text", ("tts", emotion)))
    steps.extend(
        [
            ("end_speech", ()),
            ("set_device_state", ("idle",)),
            ("set_emotion", ("neutral",)),
            ("force_activity", ("sleepy",)),
            ("force_activity", ("asleep",)),
            ("force_activity", ("asleep",)),
            ("force_activity", ("awake",)),
        ]
    )
    return steps


class _DemoRunner:
    """Plays the demo script in a loop."""

    def __init__(self, model: FaceModel, step_ms: int) -> None:
        self._model = model
        self._steps = build_demo_steps()
        self._index = 0
        self._timer = QTimer()
        self._timer.setInterval(step_ms)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        self._advance()
        self._timer.start()

    def _advance(self) -> None:
        method, args = self._steps[self._index]
        logger.info("Face demo: %s%s", method, args)
        getattr(self._model, method)(*args)
        # Two-part emotion steps change face and label together
        if method == "set_emotion":
            self._index = (self._index + 1) % len(self._steps)
            method, args = self._steps[self._index]
            getattr(self._model, method)(*args)
        self._index = (self._index + 1) % len(self._steps)


def run_face_demo(step_ms: int = 2500) -> int:
    """Open the face window and run the demo until the window closes."""
    app = QApplication.instance() or QApplication(sys.argv)

    face_config = load_face_config()
    # The demo drives activity itself, so inactivity timers stay off
    face_config.update(sleepyAfterS=0, asleepAfterS=0)
    face_model = FaceModel(face_config)

    host = QmlAppHost()
    host.create_engine()
    host.inject_context(
        {"eventBridge": None, "mainModel": MainModel(), "faceModel": face_model}
    )
    host.load_main("face_main.qml")
    host.set_fullscreen(face_config["fullscreen"])
    if face_config["fullscreen"]:
        QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
    host.show_root()

    runner = _DemoRunner(face_model, step_ms)
    runner.start()
    return app.exec()
