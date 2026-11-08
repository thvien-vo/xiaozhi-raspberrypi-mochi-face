"""Translates UI button presses, text submissions, and mode switches into protocol calls."""

from typing import TYPE_CHECKING

from src.constants.constants import AbortReason, DeviceState, ListeningMode
from src.core.event_bus import Events
from src.logging import get_logger

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext
    from src.plugins.ui_presenter import UiPresenter

logger = get_logger()


class SessionActions:
    """Session-related actions."""

    def __init__(
        self,
        ctx: "PluginContext",
        cmd: "PluginCommands",
        presenter: "UiPresenter",
    ) -> None:
        self._ctx = ctx
        self._cmd = cmd
        self._ui = presenter
        self._manual_recording = False
        self._auto_mode = False
        # Whether a chat session is active in auto mode (button shows "Stop Chat")
        self._auto_session_active = False
        # Always-listen kiosk: taps never end the conversation (set in subscribe)
        self._always_listen = False

    @property
    def auto_mode(self) -> bool:
        return self._auto_mode

    @property
    def auto_session_active(self) -> bool:
        return self._auto_session_active

    @property
    def manual_recording(self) -> bool:
        return self._manual_recording

    def subscribe(self, bus) -> None:
        # Kiosk robots start in auto mode: one tap starts a conversation that keeps
        # listening, instead of the press-to-talk toggle (which a single tap would start
        # and stop at once, sending no audio).
        config = self._ctx.get_config()
        self._always_listen = bool(config.get_config("FACE.ALWAYS_LISTEN", False))
        if self._always_listen or config.get_config("FACE.AUTO_MODE", False):
            self._auto_mode = True
            self._ui.set_auto_mode(True)
            logger.info("Auto mode enabled by FACE.AUTO_MODE")
        bus.on(Events.UI_BUTTON_PRESS, self.press)
        bus.on(Events.UI_BUTTON_RELEASE, self.release)
        bus.on(Events.UI_MANUAL_TOGGLE, self.manual_toggle)
        bus.on(Events.UI_AUTO_TOGGLE, self.auto_toggle)
        bus.on(Events.UI_AUTO_START, self.auto_session_toggle)
        bus.on(Events.UI_ABORT_REQUEST, self.abort)
        bus.on(Events.UI_SEND_TEXT, self.send_text_from_event)
        bus.on(Events.UI_QUIT_REQUEST, self.request_shutdown)
        logger.info("SessionActions subscribed to UI user action events")

    def on_device_state_changed(self, state) -> None:
        # Reset button if pulled out of listening during manual recording
        if state != DeviceState.LISTENING and self._manual_recording:
            self._manual_recording = False
            if not self._auto_mode:
                self._ui.set_button_text("Hold to Talk")

        if self._auto_mode and state == DeviceState.IDLE and self._auto_session_active:
            self._auto_session_active = False
            self._ui.set_button_text("Start Chat")
        elif self._auto_mode and state in (DeviceState.LISTENING, DeviceState.SPEAKING):
            if not self._auto_session_active:
                self._auto_session_active = True
            self._ui.set_button_text("Stop Chat")

    async def request_shutdown(self, _data=None) -> None:
        self._cmd.request_shutdown()

    def _listen_mode(self) -> ListeningMode:
        if not self._auto_mode:
            return ListeningMode.MANUAL
        aec = self._ctx.get_config().get_config("AEC_OPTIONS.ENABLED", True)
        return ListeningMode.REALTIME if aec else ListeningMode.AUTO_STOP

    async def _ensure_listen_session(self) -> bool:
        # Server often drops direct detect when idle; start listening first before sending
        if self._ctx.is_listening() or self._ctx.is_speaking():
            return True
        if not await self._cmd.connect_protocol():
            logger.warning("Cannot establish protocol connection, cancelling session action")
            return False
        mode = self._listen_mode()
        await self._cmd.start_listening(mode)
        if self._auto_mode:
            self._auto_session_active = True
            self._ui.set_button_text("Stop Chat")
        logger.debug(f"Started listen session: mode={mode}")
        return True

    async def send_text_from_event(self, data) -> None:
        if hasattr(data, "text"):
            text = data.text
        elif isinstance(data, dict):
            text = data.get("text", "")
        elif isinstance(data, str):
            text = data
        else:
            logger.warning(f"Invalid send text payload: {type(data)}")
            return
        await self.send_text(text)

    async def send_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return

        logger.info(f"Sending text: {text[:40]}{'...' if len(text) > 40 else ''}")

        if self._ctx.is_speaking():
            await self._cmd.abort_speaking(AbortReason.USER_INTERRUPTION)

        if not await self._ensure_listen_session():
            return

        await self._cmd.send_wake_word_detected(text)

    async def press(self, _data=None) -> None:
        await self._cmd.connect_protocol()
        await self._cmd.start_listening(ListeningMode.MANUAL)

    async def release(self, _data=None) -> None:
        await self._cmd.stop_listening()

    async def manual_toggle(self, _data=None) -> None:
        if not self._manual_recording:
            self._manual_recording = True
            logger.debug("Manual mode: start recording")
            self._ui.set_button_text("Send")
            await self._cmd.connect_protocol()
            await self._cmd.start_listening(ListeningMode.MANUAL)
        else:
            self._manual_recording = False
            logger.debug("Manual mode: stop recording and send")
            self._ui.set_button_text("Hold to Talk")
            await self._cmd.stop_listening()

    async def auto_toggle(self, _data=None) -> None:
        if self._always_listen:
            return  # Always-listen is auto-only; leaving it would stop the listening
        # Only toggle between auto and manual without automatically starting listening
        self._auto_mode = not self._auto_mode
        if not self._auto_mode:
            self._auto_session_active = False
        if self._auto_mode and self._manual_recording:
            self._manual_recording = False
        self._ui.set_auto_mode(self._auto_mode)
        logger.debug(f"Mode switched: {'Auto' if self._auto_mode else 'Manual'}")

    async def auto_session_toggle(self, _data=None) -> None:
        # Primary button: Start Chat / Stop Chat
        if self._always_listen:
            await self._tap_interrupt()
            return
        if self._auto_session_active or self._ctx.is_listening() or self._ctx.is_speaking():
            await self._stop_auto_session()
            return

        if not await self._ensure_listen_session():
            return
        logger.debug("Auto mode: start chat")

    async def _tap_interrupt(self) -> None:
        """Always-listen tap: cut off the assistant if it is talking, otherwise do nothing.

        abort_speaking keeps continuous listening, so listening resumes right away. If the
        session is not listening at all, the always-listen supervisor restores it.
        """
        if self._ctx.is_speaking():
            logger.info("Always-listen: tap interrupts speech")
            await self._cmd.abort_speaking(AbortReason.USER_INTERRUPTION)

    async def _stop_auto_session(self) -> None:
        # Stop first to clear keep_listening, then abort to avoid auto-resuming listening after interruption
        try:
            if self._ctx.is_speaking():
                await self._cmd.stop_listening()
                await self._cmd.abort_speaking(AbortReason.USER_INTERRUPTION)
            else:
                await self._cmd.stop_listening()
        finally:
            self._auto_session_active = False
            self._ui.set_button_text("Start Chat")
            logger.debug("Auto mode: stop chat")

    async def abort(self, _data=None) -> None:
        await self._cmd.abort_speaking(AbortReason.USER_INTERRUPTION)
