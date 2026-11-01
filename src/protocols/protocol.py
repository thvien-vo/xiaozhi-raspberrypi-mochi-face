import asyncio
import json

from src.constants.constants import AbortReason, ListeningMode
from src.logging import get_logger

logger = get_logger()


class Protocol:
    def __init__(self):
        self.session_id = ""
        # Initialize callbacks to None
        self._on_incoming_json = None
        self._on_incoming_audio = None
        self._on_audio_channel_opened = None
        self._on_audio_channel_closed = None
        self._on_network_error = None
        # Connection state change callbacks
        self._on_connection_state_changed = None
        self._on_reconnecting = None

        # Connection state and auto-reconnect (shared, moved up from subclasses)
        self._is_closing = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5  # Default 5 reconnect attempts
        self._auto_reconnect_enabled = False  # Auto-reconnect disabled by default
        self._connection_monitor_task = None
        self._idle_checker = None

    def on_incoming_json(self, callback):
        """
        Set the JSON message receive callback function.
        """
        self._on_incoming_json = callback

    def on_incoming_audio(self, callback):
        """
        Set the audio data receive callback function.
        """
        self._on_incoming_audio = callback

    def on_audio_channel_opened(self, callback):
        """
        Set the audio channel opened callback function.
        """
        self._on_audio_channel_opened = callback

    def on_audio_channel_closed(self, callback):
        """
        Set the audio channel closed callback function.
        """
        self._on_audio_channel_closed = callback

    def on_network_error(self, callback):
        """
        Set the network error callback function.
        """
        self._on_network_error = callback

    def on_connection_state_changed(self, callback):
        """Set the connection state change callback function.

        Args:
            callback: Callback function receiving (connected: bool, reason: str)
        """
        self._on_connection_state_changed = callback

    def on_reconnecting(self, callback):
        """Set the reconnect attempt callback function.

        Args:
            callback: Callback function receiving (attempt: int, max_attempts: int)
        """
        self._on_reconnecting = callback

    async def send_text(self, message):
        """
        Abstract method for sending text messages, must be implemented in subclasses.
        """
        raise NotImplementedError("send_text must be implemented by subclass")

    async def send_audio(self, data: bytes):
        """
        Abstract method for sending audio data, must be implemented in subclasses.
        """
        raise NotImplementedError("send_audio must be implemented by subclass")

    def is_audio_channel_opened(self) -> bool:
        """
        Abstract method for checking whether the audio channel is open, must be implemented in subclasses.
        """
        raise NotImplementedError("is_audio_channel_opened must be implemented by subclass")

    async def open_audio_channel(self) -> bool:
        """
        Abstract method for opening the audio channel, must be implemented in subclasses.
        """
        raise NotImplementedError("open_audio_channel must be implemented by subclass")

    async def start(self) -> bool:
        """Start application-level background capabilities of the protocol.

        WSS control and audio sessions share the same lifetime, so this does nothing by default;
        MQTT can override this to connect only the Broker control channel without opening a UDP audio session.
        """
        return True

    def set_idle_checker(self, checker) -> None:
        """Inject an application idle checker for protocols with persistent control channels to time reconnections."""
        self._idle_checker = checker

    async def close_audio_channel(self):
        """
        Abstract method for closing the audio channel, must be implemented in subclasses.
        """
        raise NotImplementedError("close_audio_channel must be implemented by subclass")

    async def shutdown(self):
        """Completely release protocol resources (application exit path).

        Ends the current audio session first so the protocol can send its normal session close message,
        then closes any remaining control channel. For WSS, the control channel is the WebSocket session,
        so the default hook is a no-op; MQTT overrides the subclass hook to close Broker/Paho resources.
        """
        try:
            await self.close_audio_channel()
        finally:
            await self._shutdown_control_channel()

    async def send_abort_speaking(self, reason):
        """
        Send a message to abort speaking.
        """
        message = {"session_id": self.session_id, "type": "abort"}
        if reason == AbortReason.WAKE_WORD_DETECTED:
            message["reason"] = "wake_word_detected"
        await self.send_text(json.dumps(message))

    async def send_wake_word_detected(self, wake_word):
        """
        Send a message indicating a wake word was detected.
        """
        message = {
            "session_id": self.session_id,
            "type": "listen",
            "state": "detect",
            "text": wake_word,
        }
        await self.send_text(json.dumps(message))

    async def send_start_listening(self, mode):
        """
        Send a message to start listening.
        """
        mode_map = {
            ListeningMode.REALTIME: "realtime",
            ListeningMode.AUTO_STOP: "auto",
            ListeningMode.MANUAL: "manual",
        }
        message = {
            "session_id": self.session_id,
            "type": "listen",
            "state": "start",
            "mode": mode_map[mode],
        }
        await self.send_text(json.dumps(message))

    async def send_stop_listening(self):
        """
        Send a message to stop listening.
        """
        message = {"session_id": self.session_id, "type": "listen", "state": "stop"}
        await self.send_text(json.dumps(message))

    async def send_iot_descriptors(self, descriptors):
        """
        Send IoT device descriptor information.
        """
        try:
            # Parse descriptor data
            if isinstance(descriptors, str):
                descriptors_data = json.loads(descriptors)
            else:
                descriptors_data = descriptors

            # Check if it is an array
            if not isinstance(descriptors_data, list):
                logger.error("IoT descriptors should be an array")
                return

            # Send a separate message for each descriptor
            for i, descriptor in enumerate(descriptors_data):
                if descriptor is None:
                    logger.error(f"Failed to get IoT descriptor at index {i}")
                    continue

                message = {
                    "session_id": self.session_id,
                    "type": "iot",
                    "update": True,
                    "descriptors": [descriptor],
                }

                try:
                    await self.send_text(json.dumps(message))
                except Exception as e:
                    logger.error(
                        f"Failed to send JSON message for IoT descriptor "
                        f"at index {i}: {e}"
                    )
                    continue

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse IoT descriptors: {e}", exc_info=True)
            return

    async def send_iot_states(self, states):
        """
        Send IoT device state information.
        """
        if isinstance(states, str):
            states_data = json.loads(states)
        else:
            states_data = states

        message = {
            "session_id": self.session_id,
            "type": "iot",
            "update": True,
            "states": states_data,
        }
        await self.send_text(json.dumps(message))

    async def send_mcp_message(self, payload) -> bool | None:
        """
        Send an MCP message.
        """
        if isinstance(payload, str):
            payload_data = json.loads(payload)
        else:
            payload_data = payload

        message = {
            "session_id": self.session_id,
            "type": "mcp",
            "payload": payload_data,
        }

        return await self.send_text(json.dumps(message))

    # ============ Connection status check (template method, implemented by subclass) ============

    def _is_connected(self) -> bool:
        """Check whether the connection is alive.

        Subclasses must implement this method to return the health status of the current protocol connection.

        Returns:
            bool: True if the connection is alive, False otherwise
        """
        raise NotImplementedError("_is_connected must be implemented by subclass")

    # ============ Protocol-specific cleanup (template method, implemented by subclass) ============

    async def _do_cleanup(self):
        """Clean up protocol-specific resources (excluding shared state and monitor tasks).

        Subclasses implementing this method should:
        - Close protocol-specific network connections (socket / websocket / mqtt client, etc.)
        - Cancel protocol-specific background tasks (heartbeats, message handlers, etc.)
        - Reset protocol-specific timestamps/state

        Do NOT do the following in this method:
        - Set self.connected = False (handled by base class _handle_connection_loss)
        - Cancel self._connection_monitor_task (handled by base class _handle_connection_loss)
        """
        raise NotImplementedError("_do_cleanup must be implemented by subclass")

    async def _shutdown_control_channel(self):
        """Close any control channel that remains alive after the session ends (called on application exit).

        Most protocols share a single connection between the audio session and control channel,
        which is already released by ``close_audio_channel``. MQTT can override this hook to close
        the reusable Broker connection and its background thread.
        """

    # ============ Connection monitoring (shared, subclass may override _monitor_interval) ============

    @property
    def _monitor_interval(self) -> float:
        """Connection monitor check interval (seconds), overridable by subclasses."""
        return 5.0

    def _start_connection_monitor(self):
        """Start the background connection health monitoring task."""
        if (
            self._connection_monitor_task is None
            or self._connection_monitor_task.done()
        ):
            self._connection_monitor_task = asyncio.create_task(
                self._connection_monitor()
            )

    async def _connection_monitor(self):
        """Connection health status monitoring coroutine.

        Periodically checks self._is_connected() and calls
        self._handle_connection_loss() if a disconnection is detected.
        """
        try:
            while not self._is_closing:
                await asyncio.sleep(self._monitor_interval)

                if not self._is_connected():
                    logger.warning("Connection loss detected")
                    await self._handle_connection_loss("Connection check failed")
                    break

        except asyncio.CancelledError:
            logger.debug("Connection monitor task cancelled")
        except Exception as e:
            logger.error(f"Connection monitor error: {e}", exc_info=True)

    # ============ Auto-reconnect (shared) ============

    def enable_auto_reconnect(self, enabled: bool = True, max_attempts: int = 5):
        """Enable or disable auto-reconnect.

        Args:
            enabled: Whether to enable auto-reconnect
            max_attempts: Maximum number of reconnect attempts
        """
        self._auto_reconnect_enabled = enabled
        if enabled:
            self._max_reconnect_attempts = max_attempts
            logger.info(f"Auto-reconnect enabled, max attempts: {max_attempts}")
        else:
            self._max_reconnect_attempts = 0
            logger.info("Auto-reconnect disabled")

    async def _handle_connection_loss(self, reason: str, *, clean: bool = False):
        """Handle connection loss (shared logic).

        Steps:
        1. Update connection state
        2. Cancel connection monitor task
        3. Call subclass _do_cleanup() to clean up protocol-specific resources
        4. Notify observers (state change, audio channel closed)
        5. Decide whether to auto-reconnect based on configuration

        Args:
            clean: Server closed normally (e.g. session ended). Only reclaims the channel
                   without triggering auto-reconnect or reporting a network error.
        """
        if clean:
            logger.info(f"Connection closed normally by server: {reason}")
        else:
            logger.warning(f"Connection lost: {reason}")

        was_connected = self.connected
        self.connected = False

        # Cancel connection monitor task
        await self._cancel_monitor_task()

        # Notify connection state change
        if self._on_connection_state_changed and was_connected:
            try:
                self._on_connection_state_changed(False, reason)
            except Exception as e:
                logger.error(f"Failed to invoke connection state change callback: {e}", exc_info=True)

        # Call subclass protocol-specific cleanup
        await self._do_cleanup()

        # Notify audio channel closed
        if self._on_audio_channel_closed:
            try:
                await self._on_audio_channel_closed()
            except Exception as e:
                logger.error(f"Failed to invoke audio channel closed callback: {e}", exc_info=True)

        # Normal server close: session end is not a fault; do not reconnect or report an error,
        # and reopen the audio channel on demand during the next interaction
        if clean:
            return

        # Decide whether to attempt auto-reconnect based on configuration
        if (
            not self._is_closing
            and self._auto_reconnect_enabled
            and self._reconnect_attempts < self._max_reconnect_attempts
        ):
            await self._attempt_reconnect(reason)
        else:
            if self._on_network_error:
                if (
                    self._auto_reconnect_enabled
                    and self._reconnect_attempts >= self._max_reconnect_attempts
                ):
                    await self._on_network_error(f"Connection lost and reconnect failed: {reason}")
                else:
                    await self._on_network_error(f"Connection lost: {reason}")

    async def _attempt_reconnect(self, original_reason: str):
        """Attempt auto-reconnect (shared logic).

        Uses exponential backoff and calls subclass connect() to establish the connection.
        """
        self._reconnect_attempts += 1

        # Notify reconnect start
        if self._on_reconnecting:
            try:
                self._on_reconnecting(
                    self._reconnect_attempts, self._max_reconnect_attempts
                )
            except Exception as e:
                logger.error(f"Failed to invoke reconnecting callback: {e}", exc_info=True)

        logger.info(
            f"Attempting auto-reconnect ({self._reconnect_attempts}/{self._max_reconnect_attempts})"
        )

        # Exponential backoff wait, up to 30 seconds
        await asyncio.sleep(min(self._reconnect_attempts * 2, 30))

        try:
            success = await self.connect()
            if success:
                logger.info("Auto-reconnect succeeded")
                if self._on_connection_state_changed:
                    self._on_connection_state_changed(True, "Reconnected")
            else:
                logger.warning(
                    f"Auto-reconnect failed ({self._reconnect_attempts}/{self._max_reconnect_attempts})"
                )
                if self._reconnect_attempts >= self._max_reconnect_attempts:
                    if self._on_network_error:
                        await self._on_network_error(
                            f"Reconnect failed after reaching max attempts: {original_reason}"
                        )
        except Exception as e:
            logger.error(f"Error during reconnect: {e}", exc_info=True)
            if self._reconnect_attempts >= self._max_reconnect_attempts:
                if self._on_network_error:
                    await self._on_network_error(f"Reconnect error: {str(e)}")

    async def _cancel_monitor_task(self):
        """Cancel and wait for the connection monitor task to complete."""
        if self._connection_monitor_task and not self._connection_monitor_task.done():
            self._connection_monitor_task.cancel()
            try:
                await self._connection_monitor_task
            except asyncio.CancelledError:
                pass

    def get_connection_info(self) -> dict:
        """Get connection info (base class implementation, extensible by subclasses).

        Returns:
            dict: Dictionary containing connection state, reconnect attempts, etc.
        """
        return {
            "is_closing": self._is_closing,
            "auto_reconnect_enabled": self._auto_reconnect_enabled,
            "reconnect_attempts": self._reconnect_attempts,
            "max_reconnect_attempts": self._max_reconnect_attempts,
        }
