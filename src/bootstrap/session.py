"""Session control: listen/speak state machine and protocol transmission.

Separated from UI-side SessionActions (button labels/modes): this module only handles
application-level session logic such as connect / listen / abort / TTS loopback.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.constants.constants import AbortReason, DeviceState, ListeningMode
from src.core.event_bus import Events
from src.logging import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.protocol_manager import ProtocolManager
    from src.core.state_manager import StateManager
    from src.notifications.notification_player import NotificationPlayer
    from src.plugins.manager import PluginManager

logger = get_logger()

# After dropping a hallucinated turn: reopen listening at the latest this long after the
# abort, whatever the server sent
STT_RESUME_FALLBACK_S = 15.0
# The official server answers an early abort with "tts stop" and may still send the reply
# right after; wait this long after such a stop before calling the turn over
STT_EARLY_STOP_WAIT_S = 3.0


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

    # -------------------------
    # STT hallucination filter (src/core/stt_gate.py)
    # -------------------------
    def attach_stt_gate(self, gate, spawn) -> None:
        """Handle the turns the gate drops; spawn(coro, name) schedules on the app loop."""
        self._stt_gate = gate
        self._spawn = spawn
        self._dropped_turn = 0
        self._resumed_turn = 0
        gate.on_drop = self._on_stt_dropped
        gate.on_resume = lambda turn: self._spawn_safe(self.resume_after_dropped_turn(turn), "stt:resume")
        gate.on_tool_blocked = lambda payload: self._spawn_safe(self._refuse_tool_call(payload), "stt:tool")
        gate.on_late_start = lambda turn: self._spawn_safe(self._abort_dropped_reply(turn), "stt:abort")

    def _spawn_safe(self, coro, name: str) -> None:
        spawn = getattr(self, "_spawn", None)
        task = spawn(coro, name) if spawn is not None else None
        if task is None and asyncio.iscoroutine(coro):
            coro.close()

    def _on_stt_dropped(self, turn: int, text: str, result) -> None:
        self._dropped_turn = turn
        self._spawn_safe(self.drop_hallucinated_turn(turn, text, getattr(result, "label", "")), "stt:drop")

    async def drop_hallucinated_turn(self, turn: int, text: str = "", pattern: str = "") -> None:
        """The server is about to answer a hallucinated sentence: stop it, say nothing.

        The face stays as it is (listening): the gate swallows tts/llm, so no state change
        reaches it. The microphone stays closed until the turn is over (the reply's "tts stop",
        or the watchdog below); then listening resumes.
        """
        if self._event_bus is not None:
            try:
                await self._event_bus.emit(
                    Events.STT_IGNORED, {"text": text, "pattern": pattern, "turn": turn}
                )
            except Exception:
                logger.debug("STT_IGNORED subscribers failed", exc_info=True)
        if self.protocol.is_audio_channel_opened():
            try:
                await self.protocol.send_abort_speaking(None)
                logger.info("STT filter: sent abort for the ignored turn")
            except Exception as e:
                logger.warning(f"STT filter: failed to send abort: {e}", exc_info=True)
        await self._clear_tts_queue()
        # Watchdog: the gate ends the turn on the reply's "tts stop"; end it here when the
        # server only acknowledged the abort and sent no reply, or sent nothing at all
        gate = getattr(self, "_stt_gate", None)
        loop = asyncio.get_running_loop()
        started = loop.time()
        while gate is not None and gate.suppressing and gate.turn == turn:
            age = gate.early_stop_age()
            if age is not None and age >= STT_EARLY_STOP_WAIT_S:
                gate.finish(turn, "no reply after the abort")
                break
            if loop.time() - started >= STT_RESUME_FALLBACK_S:
                gate.finish(turn, "server sent no end of turn")
                break
            await asyncio.sleep(0.1)

    async def _abort_dropped_reply(self, turn: int) -> None:
        if self.protocol.is_audio_channel_opened():
            try:
                await self.protocol.send_abort_speaking(None)
            except Exception as e:
                logger.warning(f"STT filter: failed to send the second abort: {e}")
        await self._clear_tts_queue()

    async def resume_after_dropped_turn(self, turn: int) -> None:
        """Back to normal listening after a dropped turn (once per turn)."""
        if turn <= self._resumed_turn:
            return
        self._resumed_turn = turn
        gate = getattr(self, "_stt_gate", None)
        await self._clear_tts_queue()
        if (
            not self.state.resting
            and self.state.keep_listening
            and self.state.listening_mode != ListeningMode.REALTIME
            and self.protocol.is_audio_channel_opened()
        ):
            try:
                await self.protocol.send_start_listening(self.state.listening_mode)
            except Exception as e:
                logger.warning(f"STT filter: failed to restart listening: {e}", exc_info=True)
        if gate is not None:
            gate.release_mic(turn)
        if self.state.keep_listening and not self.state.resting and not self.state.is_listening():
            await self.state.set_device_state(DeviceState.LISTENING)
        else:
            # Same as entering listening: VAD closed, pre-roll dropped
            try:
                from src.audio_processing.vad_gate import get_mic_vad

                get_mic_vad().reset()
            except Exception:
                pass
        logger.info("STT filter: listening again after the ignored turn")

    async def _refuse_tool_call(self, payload: dict) -> None:
        request_id = payload.get("id")
        if request_id is None:
            return
        reply = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": "Cancelled: the sentence was background noise."}],
                "isError": True,
            },
        }
        try:
            await self.protocol.send_mcp_message(reply)
        except Exception as e:
            logger.warning(f"STT filter: failed to answer the blocked tool call: {e}")

    async def _clear_tts_queue(self) -> None:
        try:
            audio_plugin = self.plugins.get_plugin("audio")
            if audio_plugin and audio_plugin.codec:
                await audio_plugin.codec.clear_audio_queue()
        except Exception as e:
            logger.warning(f"Failed to clear audio queue: {e}", exc_info=True)

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
        if self.state.resting:
            await self.state.set_device_state(DeviceState.IDLE)
            return
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
            if self.state.resting:
                logger.info("Resting, ignoring notify")
                return
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
        # Resting: nothing may open a session with the server (wake word, taps, typed
        # text, the always-listen supervisor and the greeting all come through here)
        if self.state.resting:
            logger.debug("Resting: not connecting to the server")
            return False
        if self.protocol.is_audio_channel_opened():
            return True

        opened = await self.protocol.connect()
        if opened and self.state.resting:
            # Rest started while this connection was being made: close it again
            logger.info("Resting: closing a connection that opened after rest began")
            await self.protocol.disconnect()
            await self.state.set_device_state(DeviceState.IDLE)
            return False
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

    async def begin_greeting(self, mode: ListeningMode, text: str) -> bool:
        """Have the assistant speak first, without opening the microphone.

        Arms continuous listening, then sends the wake-word "detect" event the way a
        hardware device does when it hears its wake word (followed by "listen start", as the
        firmware does). The server answers with TTS; the state stays IDLE/SPEAKING until
        _handle_tts_stop, so no microphone audio is sent until the greeting has been spoken.
        """
        await self._cancel_notification()
        if not await self.connect_protocol():
            return False
        self.state.set_listening_mode(mode)
        self.state.set_keep_listening(True)
        # Opening the channel moves the state to LISTENING, which would stream microphone
        # audio. Hold IDLE until the greeting's TTS starts and ends.
        if self.state.is_listening():
            await self.state.set_device_state(DeviceState.IDLE)
        await self.protocol.send_wake_word_detected(text)
        # A hardware device follows "detect" with "listen start"; the server only answers a
        # detect inside an open listen session. No audio frames go out while the state is not
        # LISTENING, so the microphone is still closed.
        await self.protocol.send_start_listening(mode)
        return True

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

    async def enter_rest(self) -> None:
        """Stop speaking and listening and close the conversation with the server.

        The caller has already set ``state.resting`` so no microphone audio goes out and
        nothing reconnects. Music is left alone (it has its own queue and player).
        """
        self.state.set_keep_listening(False)
        await self._cancel_notification()
        if self.protocol.is_audio_channel_opened():
            try:
                await self.protocol.send_abort_speaking(AbortReason.USER_INTERRUPTION)
            except Exception as e:
                logger.warning(f"Failed to send abort when going to rest: {e}", exc_info=True)
        try:
            audio_plugin = self.plugins.get_plugin("audio")
            if audio_plugin and audio_plugin.codec:
                await audio_plugin.codec.clear_audio_queue()
        except Exception as e:
            logger.warning(f"Failed to clear audio queue when going to rest: {e}", exc_info=True)
        try:
            # Closing the audio channel ends the session on the server: no more STT turns
            await self.protocol.disconnect()
        except Exception as e:
            logger.warning(f"Failed to close the protocol channel when going to rest: {e}", exc_info=True)
        self._aborted = False
        self.state.set_aborted(False)
        await self.state.set_device_state(DeviceState.IDLE)

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
            # Drop speech already queued for playback so the interruption is heard at once
            try:
                audio_plugin = self.plugins.get_plugin("audio")
                if audio_plugin and audio_plugin.codec:
                    await audio_plugin.codec.clear_audio_queue()
            except Exception as e:
                logger.warning(f"Failed to clear audio queue after abort: {e}", exc_info=True)
            await self.state.set_device_state(DeviceState.LISTENING)
            self._aborted = False
            self.state.set_aborted(False)
            logger.debug("Resumed continuous listening after abort")
        else:
            await self.state.set_device_state(DeviceState.IDLE)
