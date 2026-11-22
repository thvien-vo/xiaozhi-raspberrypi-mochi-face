"""Robot face ViewModel: exposes emotion, state and spoken text to QML."""

from typing import Any

from PySide6.QtCore import Property, Qt, QTimer, Signal, Slot

from src.ui.face_color import EMOTION_COLORS, EyeColorState, get_eye_colors
from src.ui.gui.models.base_model import BaseModel
from src.ui.gui.services.face_state import FaceStateMachine
from src.utils.config_manager import ConfigManager, get_config


def load_face_config(config=None) -> dict[str, Any]:
    """Read FACE.* with defaults and range checks (bad values must not break the UI)."""
    config = config or get_config()
    defaults = ConfigManager.DEFAULT_CONFIG["FACE"]

    def number(key: str, minimum: float, cast=int):
        try:
            value = cast(config.get_config(f"FACE.{key}", defaults[key]))
        except (TypeError, ValueError):
            value = defaults[key]
        return max(minimum, value)

    blink_min = number("BLINK_MIN_MS", 200)
    return {
        "style": str(config.get_config("FACE.STYLE", "robot") or "robot").lower(),
        "fullscreen": bool(config.get_config("FACE.FULLSCREEN", False)),
        "windowWidth": number("WINDOW_WIDTH", 200),
        "windowHeight": number("WINDOW_HEIGHT", 150),
        "escToExit": bool(config.get_config("FACE.ESC_TO_EXIT", True)),
        "showUserText": bool(config.get_config("FACE.SHOW_USER_TEXT", True)),
        "typewriterCps": number("TYPEWRITER_CHARS_PER_S", 1),
        "textHoldMs": number("TEXT_HOLD_MS", 0),
        "thinkingTimeoutS": number("THINKING_TIMEOUT_S", 1, float),
        "sleepyAfterS": number("SLEEPY_AFTER_S", 0, float),
        "asleepAfterS": number("ASLEEP_AFTER_S", 0, float),
        "blinkMinMs": blink_min,
        "blinkMaxMs": max(blink_min, number("BLINK_MAX_MS", 200)),
        "doubleBlinkChance": min(1.0, number("DOUBLE_BLINK_CHANCE", 0.0, float)),
        "showFps": bool(config.get_config("FACE.SHOW_FPS", False)),
    }


class FaceModel(BaseModel):
    """Face data model; written by MainWindowController, read by QML."""

    emotionChanged = Signal()
    visualStateChanged = Signal()
    activityChanged = Signal()
    speechChanged = Signal()
    speechActiveChanged = Signal()
    userTextChanged = Signal()
    eyeColorChanged = Signal()
    musicBpmChanged = Signal()
    # Carries changes made on other threads (web panel) onto the Qt thread
    _eyeColorPosted = Signal(str, str)

    def __init__(
        self,
        face_config: dict[str, Any] | None = None,
        state_machine: FaceStateMachine | None = None,
        parent=None,
        eye_colors: EyeColorState | None = None,
    ):
        super().__init__(parent)
        self._config = face_config or load_face_config()
        self._machine = state_machine or FaceStateMachine(
            sleepy_after_s=self._config["sleepyAfterS"],
            asleep_after_s=self._config["asleepAfterS"],
            thinking_timeout_s=self._config["thinkingTimeoutS"],
        )
        self._emotion = "neutral"
        self._visual_state = self._machine.visual_state
        self._activity = self._machine.activity
        self._speech_text = ""
        # Bumped for every sentence so repeating the same text restarts the typewriter
        self._speech_serial = 0
        self._speech_active = False
        self._user_text = ""
        self._music_bpm = 0.0
        self._eye_colors = eye_colors or get_eye_colors()
        self._eye_color = self._eye_colors.color
        self._eye_color_mode = self._eye_colors.mode
        self._eyeColorPosted.connect(self._apply_eye_color, Qt.ConnectionType.QueuedConnection)
        self._eye_colors.add_listener(self._eyeColorPosted.emit)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

    # ========== Properties ==========

    @Property("QVariantMap", constant=True)
    def config(self) -> dict[str, Any]:
        return self._config

    @Property(str, notify=emotionChanged)
    def emotion(self) -> str:
        return self._emotion

    @Property(str, notify=visualStateChanged)
    def visualState(self) -> str:
        return self._visual_state

    @Property(str, notify=activityChanged)
    def activity(self) -> str:
        return self._activity

    @Property(str, notify=speechChanged)
    def speechText(self) -> str:
        return self._speech_text

    @Property(int, notify=speechChanged)
    def speechSerial(self) -> int:
        return self._speech_serial

    @Property(bool, notify=speechActiveChanged)
    def speechActive(self) -> bool:
        return self._speech_active

    @Property(str, notify=userTextChanged)
    def userText(self) -> str:
        return self._user_text

    @Property(str, notify=eyeColorChanged)
    def eyeColor(self) -> str:
        return self._eye_color

    @Property(str, notify=eyeColorChanged)
    def eyeColorMode(self) -> str:
        return self._eye_color_mode

    @Property(float, notify=musicBpmChanged)
    def musicBpm(self) -> float:
        """Tempo the face bounces to; 0 when no music plays."""
        return self._music_bpm

    @Property("QVariantMap", constant=True)
    def emotionColors(self) -> dict[str, str]:
        return dict(EMOTION_COLORS)

    @property
    def eye_colors(self) -> EyeColorState:
        return self._eye_colors

    # ========== Lifecycle ==========

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # ========== Setters ==========

    def set_emotion(self, emotion: str) -> None:
        self._machine.touch()
        self._sync()
        if self._emotion != emotion:
            self._emotion = emotion
            self.emotionChanged.emit()

    def set_device_state(self, state: str) -> None:
        self._machine.set_device_state(state)
        if state == "listening" and not self._speech_active:
            self._set_user_text("")
        if state in ("idle", "listening"):
            self._set_speech_active(False)
        self._sync()

    def set_speech_text(self, role: str, text: str) -> None:
        """role: "tts" (assistant sentence) or "stt" (recognized user speech)."""
        if role == "stt":
            self._machine.on_user_text()
            self._set_user_text(text)
        else:
            self._machine.touch()
            self._speech_text = text
            self._speech_serial += 1
            self.speechChanged.emit()
            self._set_speech_active(True)
        self._sync()

    def set_music_bpm(self, bpm: float) -> None:
        bpm = max(40.0, min(200.0, float(bpm))) if bpm and bpm > 0 else 0.0
        if bpm:
            self._machine.touch()  # Wake up and stay awake while music plays
            self._sync()
        if self._music_bpm != bpm:
            self._music_bpm = bpm
            self.musicBpmChanged.emit()

    def end_speech(self) -> None:
        self._set_speech_active(False)

    @Slot()
    def interact(self) -> None:
        """Touch, click or key press."""
        self._machine.touch()
        self._sync()

    @Slot()
    def clearText(self) -> None:
        """Called by QML once finished text has faded out."""
        self._set_user_text("")
        if self._speech_text:
            self._speech_text = ""
            self.speechChanged.emit()

    def force_activity(self, activity: str) -> None:
        """Set the activity directly (face demo only)."""
        self._activity = activity
        self.activityChanged.emit()

    # ========== Internal ==========

    @Slot(str, str)
    def _apply_eye_color(self, color: str, mode: str) -> None:
        if (color, mode) != (self._eye_color, self._eye_color_mode):
            self._eye_color, self._eye_color_mode = color, mode
            self.eyeColorChanged.emit()

    def _set_user_text(self, text: str) -> None:
        if self._user_text != text:
            self._user_text = text
            self.userTextChanged.emit()

    def _set_speech_active(self, active: bool) -> None:
        if self._speech_active != active:
            self._speech_active = active
            self.speechActiveChanged.emit()

    def _on_tick(self) -> None:
        if self._music_bpm:
            self._machine.touch()
        self._machine.tick()
        self._sync()

    def _sync(self) -> None:
        if self._visual_state != self._machine.visual_state:
            self._visual_state = self._machine.visual_state
            self.visualStateChanged.emit()
        if self._activity != self._machine.activity:
            self._activity = self._machine.activity
            self.activityChanged.emit()
