"""EventBus bridge - bidirectional conversion between Python signals and QML signals."""

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from src.core.event_bus import EventBus, Events
from src.logging import get_logger

logger = get_logger()


class EventBridge(QObject):
    """Bidirectional bridge between EventBus and QML.

    QML -> Python: QML calls a slot, which internally emits an EventBus event
    Python -> QML: EventBus events trigger a Python Signal, which QML connects to

    Device activation is handled by the standalone GuiActivation window; the main UI bridge no longer carries activation signals.
    """

    # ========== Python -> QML Signals ==========

    # Window control
    showWindow = Signal()
    hideWindow = Signal()
    showSettingsWindow = Signal()  # Show settings window

    # ========== Constructor ==========

    def __init__(self, event_bus: EventBus, task_manager=None, parent: QObject | None = None):
        super().__init__(parent)
        self._event_bus = event_bus
        self._task_manager = task_manager

    def _emit_event(self, event: str, data=None):
        """Safely emit an EventBus event, scheduling onto the asyncio loop from the Qt main thread."""
        if self._task_manager is None:
            logger.error("EventBridge: TaskManager not injected, cannot emit event")
            return

        def do_emit():
            try:
                task_name = f"bridge:{event.split('.')[-1]}" if '.' in event else f"bridge:{event}"
                self._task_manager.spawn(self._event_bus.emit(event, data), name=task_name)
            except Exception as e:
                logger.warning(
                    f"EventBridge: Failed to emit event {event}: {e}",
                    exc_info=True,
                )

        # Use QTimer.singleShot to ensure execution in the Qt event loop
        QTimer.singleShot(0, do_emit)

    # ========== QML -> Python (Slots) ==========

    @Slot()
    def onButtonPress(self):
        """Manual mode button pressed."""
        logger.debug("EventBridge: Button pressed")
        self._emit_event(Events.UI_BUTTON_PRESS)

    @Slot()
    def onButtonRelease(self):
        """Manual mode button released."""
        logger.debug("EventBridge: Button released")
        self._emit_event(Events.UI_BUTTON_RELEASE)

    @Slot()
    def onManualToggle(self):
        """Manual mode recording toggle (click to start/stop)."""
        logger.debug("EventBridge: Manual recording toggle")
        self._emit_event(Events.UI_MANUAL_TOGGLE)

    @Slot()
    def onAutoToggle(self):
        """Auto mode toggle."""
        logger.debug("EventBridge: Auto mode toggle")
        self._emit_event(Events.UI_AUTO_TOGGLE)

    @Slot()
    def onAutoStart(self):
        """Auto mode: start or stop conversation."""
        logger.debug("EventBridge: Auto mode start/stop conversation")
        self._emit_event(Events.UI_AUTO_START)

    @Slot()
    def onAbort(self):
        """Abort request."""
        logger.debug("EventBridge: Abort request")
        self._emit_event(Events.UI_ABORT_REQUEST)

    @Slot(str)
    def onSendText(self, text: str):
        """Send text."""
        if text.strip():
            logger.debug(f"EventBridge: Sending text: {text[:20]}...")
            from src.ui.shared.events import UISendTextRequest
            self._emit_event(Events.UI_SEND_TEXT, UISendTextRequest(text=text))

    @Slot()
    def onQuitRequest(self):
        """Quit request."""
        logger.info("EventBridge: Quit request")
        self._emit_event(Events.UI_QUIT_REQUEST)

    @Slot()
    def onOpenSettings(self):
        """Open settings window - directly emit signal to QML."""
        logger.debug("EventBridge: Open settings window")
        self.showSettingsWindow.emit()
