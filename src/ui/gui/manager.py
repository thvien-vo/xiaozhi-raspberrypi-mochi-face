"""GUI ViewManager: composes QmlAppHost / main window / settings controller to implement ViewPort."""

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtGui import QCursor, QGuiApplication

from src.core.event_bus import EventBus, Events
from src.core.task_manager import TaskManager
from src.logging import get_logger
from src.ui.gui.main_controller import MainWindowController
from src.ui.gui.qml_host import QmlAppHost
from src.ui.gui.services import CameraPreviewService, TrayService
from src.ui.gui.services.camera_preview import PROVIDER_ID
from src.ui.gui.settings_controller import SettingsController
from src.ui.shared.bridge import EventBridge

logger = get_logger()


class GuiViewManager(QObject):
    """GUI entry point (ViewPort + settings helpers).

    Device activation is completed by the standalone GuiActivation window before container startup;
    the main window no longer mounts activation Model/API.
    """

    def __init__(self, event_bus: EventBus, task_manager: TaskManager | None = None):
        super().__init__()
        self._event_bus = event_bus
        self._running = False

        self._owns_tasks = task_manager is None
        if task_manager is not None:
            self._tasks = task_manager
        else:
            self._tasks = TaskManager()
            self._tasks.initialize()

        self._bridge = EventBridge(event_bus, task_manager=self._tasks)
        self._host = QmlAppHost()
        self._main = MainWindowController()
        self._settings = SettingsController(event_bus, self._tasks, self._bridge)
        self._tray_service: TrayService | None = None
        self._cursor_hidden = False
        self._preview: CameraPreviewService | None = None

        self._event_bus.on(Events.UI_TOGGLE_WINDOW, self._on_toggle_window)
        logger.debug("GuiViewManager: Subscribed to window toggle event")

    async def start(self, mode: str = "gui"):
        if mode == "cli":
            logger.info("GuiViewManager: CLI mode, skipping GUI initialization")
            return

        logger.info("GuiViewManager: Starting GUI...")
        self._running = True

        self._host.create_engine()
        self._host.inject_context(
            {
                "eventBridge": self._bridge,
                "mainModel": self._main.main_model,
                "settingsModel": self._settings.ensure_model(),
                "emotionService": self._main.emotion_service,
                "faceModel": self._main.face_model,
                "cameraPreview": self._preview,
            }
        )
        if self._preview:
            self._host.add_image_provider(PROVIDER_ID, self._preview.provider)
        self._load_window()
        if self._preview:
            self._preview.start()
        # Show without stealing focus on cold start, preventing macOS from switching out of other fullscreen app Spaces
        self._host.show_root(activate=False)
        self._setup_tray()
        self._main.set_neutral_emotion()
        logger.info("GuiViewManager: GUI startup complete")

    async def close(self):
        logger.info("GuiViewManager: Closing...")
        self._running = False
        self._main.face_model.stop()
        if self._preview:
            self._preview.stop()
        if self._cursor_hidden:
            QGuiApplication.restoreOverrideCursor()
            self._cursor_hidden = False
        if self._tray_service:
            self._tray_service.hide()
        self._host.shutdown()
        logger.info("GuiViewManager: Closed")

    def attach_vision(self, runtime) -> bool:
        """Create the camera preview. Returns whether detection must run continuously."""
        settings = runtime.settings
        face_style = self._main.face_model.config["style"]
        if not (settings.enabled and settings.preview.enabled and face_style == "robot"):
            return False
        self._preview = CameraPreviewService(runtime)
        return True

    def create_face_snapshot(self, sink, fps: float = 10.0, width: int = 320):
        """Snapshots of the robot face for the web panel; None without the robot face."""
        root = self._host.root_window()
        if root is None or self._main.face_model.config["style"] != "robot":
            return None
        from src.ui.gui.services.face_snapshot import (
            FaceSnapshotService,
            find_face_item,
        )

        lookup = find_face_item(root)
        if lookup() is None:
            return None
        return FaceSnapshotService(lookup, sink, fps=fps, width=width, parent=self)

    @property
    def face_model(self):
        return self._main.face_model

    def _load_window(self) -> None:
        """Load the configured face; a broken face falls back to the GIF window."""
        face = self._main.face_model
        if face.config["style"] == "robot":
            try:
                self._host.load_main("face_main.qml")
                self._apply_kiosk(face.config["fullscreen"])
                face.start()
                return
            except RuntimeError as e:
                logger.error("Robot face failed to load, using GIF window: %s", e)
        self._host.load_main("main.qml")

    def _apply_kiosk(self, fullscreen: bool) -> None:
        self._host.set_fullscreen(fullscreen)
        if fullscreen:
            QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
            self._cursor_hidden = True

    def _setup_tray(self) -> None:
        root = self._host.root_window()
        if root is None:
            return
        self._tray_service = TrayService(root)
        self._tray_service.setup(
            on_show=self._host.show_root,
            on_quit=self._request_quit,
        )

    def _request_quit(self) -> None:
        self._tasks.spawn(
            self._event_bus.emit(Events.UI_QUIT_REQUEST), name="ui:quit_request"
        )

    async def _on_toggle_window(self, data=None):
        logger.debug("GuiViewManager: Received window toggle event")
        self.toggle_window()

    # ----- ViewPort -----

    @property
    def is_running(self) -> bool:
        return self._running

    def set_chat_text(self, text: str) -> None:
        self._main.set_chat_text(text)

    def set_speech_text(self, role: str, text: str) -> None:
        self._main.set_speech_text(role, text)

    def end_speech(self) -> None:
        self._main.end_speech()

    def set_device_state(self, state: str) -> None:
        self._main.set_device_state(state)

    def set_music_line(self, text: str) -> None:
        self._main.set_music_line(text)

    def set_emotion(self, emotion: str) -> None:
        self._main.set_emotion(emotion)

    def set_status(self, status: str, connected: bool = True) -> None:
        self._main.set_status(status, connected)

    def set_button_text(self, text: str) -> None:
        self._main.set_button_text(text)

    def set_auto_mode(self, auto_mode: bool) -> None:
        self._main.set_auto_mode(auto_mode)

    def is_auto_mode(self) -> bool:
        return self._main.is_auto_mode()

    # ----- Settings / Window -----

    @property
    def main_model(self):
        return self._main.main_model

    @property
    def settings_model(self):
        return self._settings.settings_model

    @Slot()
    def toggle_mode(self):
        self._tasks.spawn(
            self._event_bus.emit(Events.UI_AUTO_TOGGLE), name="ui:auto_toggle"
        )

    @Slot()
    def toggle_window(self):
        self._host.toggle_root_visible()

    def open_settings(self):
        if not self._host.engine:
            logger.warning("GuiViewManager: Engine not initialized, cannot open settings")
            return
        self._settings.open_settings()
