"""Session control: listen/speak state machine and protocol transmission.

Separated from UI-side SessionActions (button labels/modes): this module only handles
application-level session logic such as connect / listen / abort / TTS loopback.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.constants.constants import DeviceState, ListeningMode
from src.core.event_bus import Events
from src.logging import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.protocol_manager import ProtocolManager
    from src.core.state_manager import StateManager
    from src.notifications.notification_player import NotificationPlayer
    from src.plugins.manager import PluginManager

logger = get_logger()


class ConversationSession:
    """Application-level session controller."""

    def __init__(
        self,
        state: StateManager,
        protocol: ProtocolManager,
        plugins: PluginManager,
        event_bus: EventBus | None = None,
        notification_player: NotificationPlayer | None = None,
    ) -> None:
        self.state = state
        self.protocol = protocol
        self.plugins = plugins
        self._event_bus = event_bus
        self._aborted = False
        # Reconnect after MCP tool config changes, etc.: remain IDLE after channel opens instead of auto-listening
        self._keep_idle_on_channel_open = False
        self._notification_player = notification_player
        self._notification_generation = 0
        self._notification_lock = asyncio.Lock()

    def set_notification_player(self, player: NotificationPlayer) -> None:
        """Inject notify playback service from the container."""
        self._notification_player = player

    # -------------------------
    # Event subscriptions
    # -------------------------
    def bind_events(self, event_bus: EventBus) -> None:
        """Subscribe to protocol/state events (idempotent, latest call takes effect)."""
        self._event_bus = event_bus
        event_bus.on(Events.AUDIO_CHANNEL_OPENED, self._on_audio_channel_opened)
        event_bus.on(Events.AUDIO_CHANNEL_CLOSED, self._on_audio_channel_closed)
        event_bus.on(Events.INCOMING_JSON, self._on_incoming_json)
        # Note: INCOMING_AUDIO goes through the direct channel, no longer via EventBus
        event_bus.on(Events.NETWORK_ERROR, self._on_network_error)
        event_bus.on(Events.DEVICE_STATE_CHANGED, self._on_device_state_changed)
        event_bus.on(
            Events.PROTOCOL_RECONNECT_REQUEST, self._on_protocol_reconnect_request
        )

    # -------------------------
    # Event handlers
    # -------------------------
    async def _on_audio_channel_opened(self, _=None) -> None:
        if self._keep_idle_on_channel_open:
            self._keep_idle_on_channel_open = False
            self.state.set_keep_listening(False)
            await self.state.set_device_state(DeviceState.IDLE)
            logger.info("Protocol channel opened (config reconnect): staying idle, not entering listening mode")
            return
        await self.state.set_device_state(DeviceState.LISTENING)

    async def _on_audio_channel_closed(self, _=None) -> None:
        await self._cancel_notification()
        await self.state.set_device_state(DeviceState.IDLE)

    async def _on_network_error(self, error_message: str = None) -> None:
        """Network error: stop continuous listening and reset device state to IDLE."""
        self.state.set_keep_listening(False)
        await self._cancel_notification()
        try:
            if not self.state.is_idle():
                await self.state.set_device_state(DeviceState.IDLE)
        except Exception as e:
            logger.error(f"Failed to reset device state after network error: {e}", exc_info=True)

    async def _on_device_state_changed(self, data: dict) -> None:
        new_state = data.get("new_state")
        if new_state:
            await self.plugins.notify_device_state_changed(new_state)
            if new_state == DeviceState.LISTENING:
                self._aborted = False

    async def _on_incoming_json(self, json_data: dict) -> None:
        try:
            msg_type = json_data.get("type") if isinstance(json_data, dict) else None
            logger.info(f"Received JSON message: type={msg_type}")

            if msg_type == "tts":
                state = json_data.get("state")
                if state == "start":
                    await self._handle_tts_start()
                elif state == "stop":
                    await self._handle_tts_stop()
            elif msg_type == "notify":
                await self._handle_notify(json_data)

            await self.plugins.notify_incoming_json(json_data)

        except Exception as e:
            logger.error(f"Failed to process JSON message: {e}", exc_info=True)

    async def _handle_tts_start(self) -> None:
        await self._cancel_notification()
        if (
            self.state.keep_listening
            and self.state.listening_mode == ListeningMode.REALTIME
        ):
            await self.state.set_device_state(DeviceState.LISTENING)
        else:
            await self.state.set_device_state(DeviceState.SPEAKING)

    async def _handle_tts_stop(self) -> None:
        # If continuing to listen, send start_listening before clearing queue and updating state
        if not self.state.keep_listening:
            await self.state.set_device_state(DeviceState.IDLE)
            return

        # REALTIME mode is already listening, no need to resend
        if self.state.listening_mode != ListeningMode.REALTIME:
            if self.protocol.is_audio_channel_opened():
                try:
                    await self.protocol.send_start_listening(self.state.listening_mode)
                except Exception as e:
                    logger.warning(
                        f"Failed to restart listening after TTS finished: {e}",
                        exc_info=True,
                    )
            else:
                logger.warning("TTS finished but protocol channel is closed, skipping restart listening")

        try:
            audio_plugin = self.plugins.get_plugin("audio")
            if audio_plugin and audio_plugin.codec:
                await audio_plugin.codec.clear_audio_queue()
        except Exception as e:
            logger.warning(f"Failed to clear audio queue: {e}", exc_info=True)

        await self.state.set_device_state(DeviceState.LISTENING)

    async def _handle_notify(self, message: dict) -> None:
        """Accept one-way server audio notifications only when idle."""
        player = self._notification_player
        if player is None:
            logger.debug("NotificationPlayer not configured, ignoring notify")
            return

        from src.notifications.notification_player import parse_notification

        parsed = parse_notification(message)
        if parsed is None:
            logger.warning("Ignoring malformed notify message")
            return
        audio_url, subtitles = parsed

        async with self._notification_lock:
            if not self.state.is_idle():
                logger.info("Device is busy, ignoring notify")
                return

            generation = None
            try:
                await self.state.set_device_state(DeviceState.NOTIFYING)
                generation = await player.start(
                    audio_url,
                    subtitles,
                    self._on_notification_finished,
                )
            finally:
                if generation is None and self._is_notifying():
                    await self.state.set_device_state(DeviceState.IDLE)
            if generation is None:
                return
            self._notification_generation = generation
            logger.info("Started playing server notification: %s", audio_url)

    async def _on_notification_finished(self, generation: int, success: bool) -> None:
        async with self._notification_lock:
            if generation != self._notification_generation:
                return
            self._notification_generation = 0
            if self._is_notifying():
                await self.state.set_device_state(DeviceState.IDLE)
            logger.info("Notification playback %s", "completed" if success else "failed")

    def _is_notifying(self) -> bool:
        return getattr(self.state, "device_state", None) == DeviceState.NOTIFYING

    async def _cancel_notification(self) -> None:
        """Interrupt notify; caller may subsequently transition to listen / TTS / idle."""
        player = self._notification_player
        if player is None:
            return
        async with self._notification_lock:
            if not player.is_active and not self._is_notifying():
                return
            self._notification_generation = 0
            await player.stop()
            if self._is_notifying():
                await self.state.set_device_state(DeviceState.IDLE)

    # -------------------------
    # Operations
    # -------------------------
    async def connect_protocol(self) -> bool:
        if self.protocol.is_audio_channel_opened():
            return True

        opened = await self.protocol.connect()
        if opened:
            await self.plugins.notify_protocol_connected(self.protocol.protocol)
        return opened

    async def _on_protocol_reconnect_request(self, _=None) -> None:
        """MCP tool list changed after saving settings: disconnect and reconnect if connected so the server re-lists tools.

        Refreshes only the protocol/tool view without resuming a listening session (avoids entering 'Listening' after saving settings).
        """
        try:
            if not self.protocol.is_audio_channel_opened():
                logger.info("MCP tool configuration updated (currently disconnected, will take effect on next connection)")
                return
            logger.info("MCP tool configuration updated, reconnecting protocol...")
            # Interrupt ongoing listen/speak state so keep_listening does not carry over after reconnect
            self.state.set_keep_listening(False)
            self._aborted = False
            self._keep_idle_on_channel_open = True
            try:
                await self.protocol.disconnect()
                ok = await self.connect_protocol()
            except Exception:
                self._keep_idle_on_channel_open = False
                raise
            if ok:
                # Double safeguard: force back to IDLE even if OPENED callback order is unexpected
                if not self.state.is_idle():
                    await self.state.set_device_state(DeviceState.IDLE)
                logger.info("Protocol reconnected successfully (new tools/list will take effect on handshake, staying idle)")
            else:
                self._keep_idle_on_channel_open = False
                logger.warning("Protocol reconnection failed, please reconnect manually")
        except Exception as e:
            self._keep_idle_on_channel_open = False
            logger.error(f"Protocol reconnection failed: {e}", exc_info=True)

    async def start_listening(self, mode: ListeningMode) -> None:
        await self._cancel_notification()
        ok = await self.connect_protocol()
        if not ok:
            return

        self.state.set_listening_mode(mode)
        self.state.set_keep_listening(mode != ListeningMode.MANUAL)
        await self.protocol.send_start_listening(mode)
        await self.state.set_device_state(DeviceState.LISTENING)

    async def stop_listening(self) -> None:
        self.state.set_keep_listening(False)
        await self.protocol.send_stop_listening()
        await self.state.set_device_state(DeviceState.IDLE)

    async def start_listening_manual(self) -> None:
        await self._cancel_notification()
        ok = await self.connect_protocol()
        if not ok:
            return

        self.state.set_keep_listening(False)

        if self.state.is_speaking():
            logger.info("Sending abort while speaking")
            await self.protocol.send_abort_speaking(None)
            await self.state.set_device_state(DeviceState.IDLE)

        await self.protocol.send_start_listening(ListeningMode.MANUAL)
        await self.state.set_device_state(DeviceState.LISTENING)

    async def stop_listening_manual(self) -> None:
        await self.protocol.send_stop_listening()
        await self.state.set_device_state(DeviceState.IDLE)

    async def start_auto_conversation(self) -> None:
        await self._cancel_notification()
        ok = await self.connect_protocol()
        if not ok:
            return

        mode = (
            ListeningMode.REALTIME
            if self.state.aec_enabled
            else ListeningMode.AUTO_STOP
        )
        self.state.set_listening_mode(mode)
        self.state.set_keep_listening(True)

        await self.protocol.send_start_listening(mode)
        await self.state.set_device_state(DeviceState.LISTENING)

    async def abort_speaking(self, reason: str) -> None:
        if self._is_notifying():
            await self._cancel_notification()
            return
        # When auto-conversation is keeping listening, return to listening after abort rather than staying idle
        if self._aborted:
            logger.debug(f"Already aborted, ignoring duplicate request: {reason}")
            return

        logger.info(f"Aborting speech output: {reason}")
        self._aborted = True
        self.state.set_aborted(True)
        try:
            if self.protocol.is_audio_channel_opened():
                await self.protocol.send_abort_speaking(reason)
        except Exception as e:
            logger.warning(f"Failed to send abort: {e}", exc_info=True)

        if self.state.keep_listening:
            if self.state.listening_mode != ListeningMode.REALTIME:
                if self.protocol.is_audio_channel_opened():
                    try:
                        await self.protocol.send_start_listening(
                            self.state.listening_mode
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to restart listening after abort: {e}",
                            exc_info=True,
                        )
            await self.state.set_device_state(DeviceState.LISTENING)
            self._aborted = False
            self.state.set_aborted(False)
            logger.debug("Resumed continuous listening after abort")
        else:
            await self.state.set_device_state(DeviceState.IDLE)
