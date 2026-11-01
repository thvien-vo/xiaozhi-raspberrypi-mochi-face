import asyncio
import json
import logging
import ssl

import websockets

from src.constants.constants import AudioConfig
from src.logging import get_logger
from src.protocols.protocol import Protocol
from src.utils.config_manager import get_config

# Verify server certificates using the system default CA bundle (do not skip verification)
ssl_context = ssl.create_default_context()

logger = get_logger()


class WebsocketProtocol(Protocol):
    def __init__(self):
        super().__init__()
        # Get ConfigManager instance
        self.config = get_config()
        self.websocket = None
        self.connected = False
        self.hello_received = None  # Initialized to None first
        # Reference to message handler task for cancellation during close
        self._message_task = None

        self.WEBSOCKET_URL = self.config.get_config(
            "SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL"
        )
        access_token = self.config.get_config(
            "SYSTEM_OPTIONS.NETWORK.WEBSOCKET_ACCESS_TOKEN"
        )
        device_id = self.config.get_config("SYSTEM_OPTIONS.DEVICE_ID")
        client_id = self.config.get_config("SYSTEM_OPTIONS.CLIENT_ID")

        self.HEADERS = {
            "Authorization": f"Bearer {access_token}",
            "Protocol-Version": "1",
            "Device-Id": device_id,  # Device MAC address
            "Client-Id": client_id,
        }

    async def connect(self) -> bool:
        """
        Connect to the WebSocket server.
        """
        if self._is_closing:
            logger.warning("Connection is closing, cancelling new connection attempt")
            return False

        try:
            # Create Event at connect time to ensure it is bound to the running event loop
            self.hello_received = asyncio.Event()

            # Determine whether SSL should be used
            current_ssl_context = None
            if self.WEBSOCKET_URL.startswith("wss://"):
                current_ssl_context = ssl_context

            # Establish WebSocket connection (compatible across websockets versions)
            try:
                # Newer signature (in Python 3.11+ / newer websockets)
                self.websocket = await websockets.connect(
                    uri=self.WEBSOCKET_URL,
                    ssl=current_ssl_context,
                    additional_headers=self.HEADERS,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    open_timeout=5,
                    max_size=10 * 1024 * 1024,
                    compression=None,
                    proxy=None,
                )
            except TypeError:
                # Older signature (in earlier websockets versions)
                self.websocket = await websockets.connect(
                    self.WEBSOCKET_URL,
                    ssl=current_ssl_context,
                    extra_headers=self.HEADERS,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    open_timeout=5,
                    max_size=10 * 1024 * 1024,
                    compression=None,
                )

            # Start message handler loop (store task reference so it can be cancelled on close)
            self._message_task = asyncio.create_task(self._message_handler())

            # Start connection monitor
            self._start_connection_monitor()

            # Send client hello message
            hello_message = {
                "type": "hello",
                "version": 1,
                "features": {
                    "mcp": True,
                },
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": AudioConfig.INPUT_SAMPLE_RATE,
                    "channels": AudioConfig.CHANNELS,
                    "frame_duration": AudioConfig.FRAME_DURATION,
                },
            }
            await self.send_text(json.dumps(hello_message))

            # Wait for server hello response
            try:
                await asyncio.wait_for(self.hello_received.wait(), timeout=10.0)
                self.connected = True
                self._reconnect_attempts = 0  # Reset reconnect counter
                logger.info("Connected to WebSocket server")

                # Notify connection state change
                if self._on_connection_state_changed:
                    self._on_connection_state_changed(True, "Connected")

                return True
            except asyncio.TimeoutError:
                logger.error("Timed out waiting for server hello response")
                await self._do_cleanup()
                if self._on_network_error:
                    await self._on_network_error("Timed out waiting for response")
                return False

        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}", exc_info=True)
            await self._do_cleanup()
            if self._on_network_error:
                await self._on_network_error(f"Cannot connect to service: {str(e)}")
            return False

    # ============ Template method implementations ============

    @property
    def _monitor_interval(self) -> float:
        """WSS connection monitor check interval (seconds)."""
        return 5.0

    def _is_connected(self) -> bool:
        """Check whether the WebSocket connection is alive."""
        if not self.websocket:
            return False
        return self.websocket.close_code is None

    async def _do_cleanup(self):
        """WebSocket protocol-specific resource cleanup.

        Cleans up message handler task and WebSocket connection.
        Does not cancel the connection monitor task (handled by base class _handle_connection_loss).
        """
        # Cancel message handler task
        if self._message_task and not self._message_task.done():
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Exception while awaiting cancelled message task: {e}")
        self._message_task = None

        # Close WebSocket connection
        if self.websocket and self.websocket.close_code is None:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket connection: {e}", exc_info=True)

        self.websocket = None

    def get_connection_info(self) -> dict:
        """Get WSS connection info.

        Returns:
            dict: Dictionary containing connection state, reconnect attempts, etc.
        """
        info = super().get_connection_info()
        info.update(
            {
                "connected": self.connected,
                "websocket_closed": (
                    self.websocket.close_code is not None if self.websocket else True
                ),
                "websocket_url": self.WEBSOCKET_URL,
            }
        )
        return info

    async def _message_handler(self):
        """
        Handle incoming WebSocket messages.
        """
        try:
            async for message in self.websocket:
                if self._is_closing:
                    break

                try:
                    if isinstance(message, str):
                        try:
                            data = json.loads(message)
                            msg_type = data.get("type")
                            if msg_type == "hello":
                                # Handle server hello message
                                await self._handle_server_hello(data)
                            else:
                                if self._on_incoming_json:
                                    self._on_incoming_json(data)
                        except json.JSONDecodeError as e:
                            logger.error(f"Invalid JSON message: {message}, error: {e}", exc_info=True)
                    elif isinstance(message, bytes):
                        # Binary message, likely audio
                        if self._on_incoming_audio:
                            self._on_incoming_audio(message)
                except Exception as e:
                    # Log error for individual message and continue processing subsequent messages
                    logger.error(f"Error processing message: {e}", exc_info=True)
                    continue

        except asyncio.CancelledError:
            logger.debug("Message handler task cancelled")
            return
        except websockets.ConnectionClosedOK as e:
            if not self._is_closing:
                logger.info(f"WebSocket connection closed normally by server: {e}")
                await self._handle_connection_loss(
                    f"Server closed connection: {e.code}", clean=True
                )
        except websockets.ConnectionClosedError as e:
            if not self._is_closing:
                logger.info(f"WebSocket connection closed with error: {e}")
                await self._handle_connection_loss(f"Connection error: {e.code} {e.reason}")
        except websockets.InvalidState as e:
            logger.error(f"Invalid WebSocket state: {e}", exc_info=True)
            await self._handle_connection_loss("Invalid connection state")
        except ConnectionResetError:
            logger.warning("Connection reset")
            await self._handle_connection_loss("Connection reset")
        except OSError as e:
            logger.error(f"Network I/O error: {e}", exc_info=True)
            await self._handle_connection_loss("Network I/O error")
        except Exception as e:
            logger.error(f"Message handler loop error: {e}", exc_info=True)
            await self._handle_connection_loss(f"Message handler error: {str(e)}")

    async def send_audio(self, data: bytes):
        """
        Send audio data.
        """
        if not self.is_audio_channel_opened():
            return

        try:
            await self.websocket.send(data)
        except websockets.ConnectionClosedOK as e:
            # Server normally reclaimed session (e.g. closed after TTS finished), not a network error
            logger.info(f"Connection closed normally by server while sending audio: {e}")
            await self._handle_connection_loss(
                f"Server closed while sending audio: {e.code}", clean=True
            )
        except websockets.ConnectionClosedError as e:
            logger.warning(f"Connection closed abnormally while sending audio: {e}")
            await self._handle_connection_loss(f"Failed to send audio: {e.code} {e.reason}")
        except Exception as e:
            logger.error(f"Failed to send audio data: {e}", exc_info=True)
            # Do not invoke network error callback directly here; let _handle_connection_loss handle it
            await self._handle_connection_loss(f"Send audio error: {str(e)}")

    async def send_text(self, message: str):
        """
        Send text message.
        """
        if not self.websocket or self._is_closing:
            logger.warning("WebSocket is not connected or is closing, cannot send message")
            return

        try:
            close_code = self.websocket.close_code
        except Exception:
            close_code = None
        if close_code is not None:
            # 1000/1001/1005 are treated as normal closure (server reclaimed session)
            clean = close_code in (1000, 1001, 1005)
            logger.log(
                logging.INFO if clean else logging.WARNING,
                f"WebSocket already closed (code={close_code}), skipping text send",
            )
            if self.connected:
                await self._handle_connection_loss(
                    f"Failed to send text: connection closed {close_code}", clean=clean
                )
            return

        try:
            await self.websocket.send(message)
        except websockets.ConnectionClosedOK as e:
            # Server normally reclaimed session, not a network error
            logger.info(f"Connection closed normally by server while sending text: {e}")
            if self.connected and not self._is_closing:
                await self._handle_connection_loss(
                    f"Server closed while sending text: {e.code}", clean=True
                )
        except websockets.ConnectionClosedError as e:
            logger.warning(f"Connection closed abnormally while sending text: {e}")
            if self.connected and not self._is_closing:
                await self._handle_connection_loss(
                    f"Send text error: {e.code} {e.reason}"
                )
        except Exception as e:
            logger.error(f"Failed to send text message: {e}", exc_info=True)
            if self.connected and not self._is_closing:
                await self._handle_connection_loss(f"Send text exception: {str(e)}")

    def is_audio_channel_opened(self) -> bool:
        """Check whether the audio channel is open.

        Checks the actual WebSocket state for accurate connection status.
        """
        if not self.websocket or not self.connected or self._is_closing:
            return False

        # Check the actual WebSocket state
        try:
            return self.websocket.close_code is None
        except Exception:
            return False

    async def open_audio_channel(self) -> bool:
        """Establish a WebSocket connection.

        Creates a new WebSocket connection if not already connected.
        Returns:
            bool: Whether the connection succeeded
        """
        if not self.is_audio_channel_opened():
            return await self.connect()
        return True

    async def _handle_server_hello(self, data: dict):
        """
        Handle the server hello message.
        """
        try:
            # Validate transport type
            transport = data.get("transport")
            if not transport or transport != "websocket":
                logger.error(f"Unsupported transport: {transport}")
                return

            # Set hello received event
            self.hello_received.set()

            # Notify that audio channel is opened
            if self._on_audio_channel_opened:
                await self._on_audio_channel_opened()

            logger.info("Successfully processed server hello message")

        except Exception as e:
            logger.error(f"Error processing server hello message: {e}", exc_info=True)
            if self._on_network_error:
                await self._on_network_error(f"Failed to process server response: {str(e)}")

    async def close_audio_channel(self):
        """
        Close the audio channel.
        """
        self._is_closing = True

        try:
            self.connected = False

            # Cancel connection monitor task (managed by base class)
            await self._cancel_monitor_task()

            # Protocol-specific cleanup
            await self._do_cleanup()

            if self._on_audio_channel_closed:
                await self._on_audio_channel_closed()

        except Exception as e:
            logger.error(f"Failed to close audio channel: {e}", exc_info=True)
        finally:
            self._is_closing = False
