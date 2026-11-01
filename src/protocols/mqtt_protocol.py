"""MQTT + UDP audio protocol implementation.

MQTT carries control JSON; UDP carries encrypted audio frames (see mqtt_udp / mqtt_crypto).
"""

from __future__ import annotations

import asyncio
import json
import time

import paho.mqtt.client as mqtt

from src.constants.constants import AudioConfig
from src.logging import get_logger
from src.protocols.mqtt_udp import MqttUdpChannel
from src.protocols.protocol import Protocol
from src.utils.config_manager import get_config

logger = get_logger()

_MQTT_PUBLISH_TIMEOUT = 5.0
_CONTROL_RECONNECT_DELAY = 60.0


def parse_mqtt_endpoint(endpoint: str) -> tuple[str, int]:
    """Parse endpoint: hostname or hostname:port; default port 8883."""
    if not endpoint:
        raise ValueError("endpoint cannot be empty")

    if ":" in endpoint:
        host, port_str = endpoint.rsplit(":", 1)
        try:
            port = int(port_str)
            if port < 1 or port > 65535:
                raise ValueError(f"Port must be between 1 and 65535: {port}")
        except ValueError as e:
            raise ValueError(f"Invalid port number: {port_str}") from e
    else:
        host = endpoint
        port = 8883

    return host, port


class MqttProtocol(Protocol):
    def __init__(self, loop):
        super().__init__()
        self.loop = loop
        self.config = get_config()
        self.mqtt_client = None
        self.connected = False
        # Futures scheduled from threads to avoid fire-and-forget create_task
        self._pending_futures: set = set()

        # MQTT connection activity monitoring
        self._last_activity_time = None
        self._keep_alive_interval = 60
        self._connection_timeout = 120

        # MQTT configuration (injected via OTA)
        self.endpoint = None
        self.client_id = None
        self.username = None
        self.password = None
        self.publish_topic = None
        self.subscribe_topic = None

        # UDP audio channel
        self._udp = MqttUdpChannel(loop)
        self._udp.set_audio_handler(self._on_udp_audio)

        self.server_hello_event = asyncio.Event()
        self._control_connect_lock = asyncio.Lock()
        self._control_reconnect_task: asyncio.Task | None = None

    # ---- Legacy compatibility properties (diagnostics / tests) ----
    @property
    def udp_socket(self):
        return self._udp.socket

    @property
    def udp_running(self) -> bool:
        return self._udp.running

    @property
    def udp_server(self) -> str:
        return self._udp.server

    @property
    def udp_port(self) -> int:
        return self._udp.port

    def _on_udp_audio(self, audio_data: bytes) -> None:
        """On event loop thread: forward UDP frame to protocol on_incoming_audio."""
        cb = self._on_incoming_audio
        if not cb:
            return
        if asyncio.iscoroutinefunction(cb):
            self.loop.create_task(cb(audio_data))
        else:
            cb(audio_data)

    def _schedule_coro(self, coro, name: str = "mqtt") -> None:
        """Safely schedule a coroutine onto the event loop from any thread and log exceptions."""
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        def _track_future(fut) -> None:
            self._pending_futures.add(fut)

            def _done(f) -> None:
                self._pending_futures.discard(f)
                try:
                    exc = f.exception()
                except (asyncio.CancelledError, Exception):
                    return
                if exc:
                    logger.error(f"MQTT scheduled task {name} failed: {exc}", exc_info=exc)

            fut.add_done_callback(_done)

        if running_loop is self.loop:
            try:
                task = self.loop.create_task(coro, name=f"mqtt:{name}")
                _track_future(task)
            except Exception as e:
                logger.error(f"MQTT failed to create task {name}: {e}", exc_info=True)
                if asyncio.iscoroutine(coro):
                    coro.close()
            return

        try:
            fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
            _track_future(fut)
        except Exception as e:
            logger.error(f"MQTT cross-thread scheduling failed {name}: {e}", exc_info=True)
            if asyncio.iscoroutine(coro):
                coro.close()

    async def connect(self):
        """Legacy compatibility call: connect MQTT control channel then establish UDP audio session."""
        return await self.open_audio_channel()

    async def start(self) -> bool:
        """On application startup, establish only the MQTT control connection without sending hello or opening UDP."""
        if self._is_closing:
            return False
        connected = await self._connect_control_channel()
        if not connected:
            self._schedule_control_reconnect()
        return connected

    async def _connect_control_channel(self) -> bool:
        """Connect and subscribe to the Broker control channel without changing audio session state."""
        if self._is_closing:
            logger.warning("Connection is closing, cancelling new connection attempt")
            return False

        if self.mqtt_client and self.mqtt_client.is_connected():
            return True

        async with self._control_connect_lock:
            if self.mqtt_client and self.mqtt_client.is_connected():
                return True
            return await self._create_control_connection()

    async def _create_control_connection(self) -> bool:
        """Create a new Paho client and wait for CONNACK."""

        try:
            mqtt_config = self.config.get_config("SYSTEM_OPTIONS.NETWORK.MQTT_INFO")
            logger.debug(f"MQTT config: {mqtt_config}")
            self.endpoint = mqtt_config.get("endpoint")
            self.client_id = mqtt_config.get("client_id")
            self.username = mqtt_config.get("username")
            self.password = mqtt_config.get("password")
            self.publish_topic = mqtt_config.get("publish_topic")
            self.subscribe_topic = mqtt_config.get("subscribe_topic")
            logger.info(f"Loaded MQTT config from OTA server: {self.endpoint}")
        except Exception as e:
            logger.warning(f"Failed to load MQTT config from OTA server: {e}", exc_info=True)

        if (
            not self.endpoint
            or not self.username
            or not self.password
            or not self.publish_topic
        ):
            logger.error("MQTT configuration is incomplete")
            if self._on_network_error:
                await self._on_network_error("MQTT configuration is incomplete")
            return False

        if self.subscribe_topic == "null":
            self.subscribe_topic = None
            logger.info("Subscribe topic is null, will not subscribe to any topic")

        if self.mqtt_client:
            self._disconnect_mqtt_client()

        try:
            host, port = parse_mqtt_endpoint(self.endpoint)
            use_tls = port == 8883
            logger.info(
                f"Parsed endpoint: {self.endpoint} -> host: {host}, port: {port}, "
                f"TLS: {use_tls}"
            )
        except ValueError as e:
            logger.error(f"Failed to parse endpoint: {e}", exc_info=True)
            if self._on_network_error:
                await self._on_network_error(f"Failed to parse endpoint: {e}")
            return False

        # Broker auto-reconnect is managed by the application; Paho handles only a single connection and I/O thread.
        self.mqtt_client = mqtt.Client(
            client_id=self.client_id, reconnect_on_failure=False
        )
        self.mqtt_client.username_pw_set(self.username, self.password)

        if use_tls:
            try:
                self.mqtt_client.tls_set(
                    ca_certs=None,
                    certfile=None,
                    keyfile=None,
                    cert_reqs=mqtt.ssl.CERT_REQUIRED,
                    tls_version=mqtt.ssl.PROTOCOL_TLS,
                )
                logger.info("Configured TLS encrypted connection (certificate verification enabled)")
            except Exception as e:
                logger.error(
                    f"TLS configuration failed, cannot securely connect to MQTT server: {e}",
                    exc_info=True,
                )
                if self._on_network_error:
                    await self._on_network_error(f"TLS configuration failed: {str(e)}")
                return False
        else:
            logger.info("Using non-TLS connection")

        connect_future = self.loop.create_future()

        def settle_connect(success: bool, error: Exception | None = None) -> None:
            """Settle the Future created for this connect() attempt at most once.

            Paho network callbacks run on a separate thread. Late callbacks after timeout,
            cancellation, or from a previous client may arrive after the Future is done
            and must be ignored to avoid leaking exceptions into the asyncio event loop.
            """
            if connect_future.done():
                logger.debug("Ignoring late MQTT connect result for settled future")
                return
            if success:
                connect_future.set_result(True)
            else:
                connect_future.set_exception(error or RuntimeError("MQTT connection failed"))

        def on_connect_callback(client, userdata, flags, rc, properties=None):
            if rc == 0:
                logger.info("Connected to MQTT server")
                self._last_activity_time = time.time()
                self.loop.call_soon_threadsafe(settle_connect, True)
            else:
                logger.error(f"Failed to connect to MQTT server, return code: {rc}")
                self.loop.call_soon_threadsafe(
                    settle_connect,
                    False,
                    Exception(f"Failed to connect to MQTT server, return code: {rc}"),
                )

        def on_message_callback(client, userdata, msg):
            try:
                self._last_activity_time = time.time()
                payload = msg.payload.decode("utf-8")
                self._handle_mqtt_message(payload)
            except Exception as e:
                logger.error(f"Error handling MQTT message: {e}", exc_info=True)

        def on_disconnect_callback(client, userdata, rc):
            try:
                # Late callbacks from an old cleaned-up/replaced client must not interfere with the current connection.
                if client is not self.mqtt_client:
                    return
                if self._is_closing:
                    return
                reason = (
                    "MQTT disconnected normally" if rc == 0 else f"MQTT disconnected: {rc}"
                )
                if rc == 0:
                    logger.info(reason)
                else:
                    logger.warning(reason)
                self._schedule_coro(
                    self._handle_broker_disconnect(reason),
                    name=f"broker_disconnect:rc={rc}",
                )
            except Exception as e:
                logger.error(f"Failed to handle MQTT disconnect: {e}", exc_info=True)

        def on_publish_callback(client, userdata, mid):
            self._last_activity_time = time.time()

        def on_subscribe_callback(client, userdata, mid, granted_qos):
            logger.info(f"Subscribed to topic: {self.subscribe_topic}")
            self._last_activity_time = time.time()

        self.mqtt_client.on_connect = on_connect_callback
        self.mqtt_client.on_message = on_message_callback
        self.mqtt_client.on_disconnect = on_disconnect_callback
        self.mqtt_client.on_publish = on_publish_callback
        self.mqtt_client.on_subscribe = on_subscribe_callback

        try:
            logger.info(f"Connecting to MQTT server: {host}:{port}")
            self.mqtt_client.connect_async(
                host, port, keepalive=self._keep_alive_interval
            )
            self.mqtt_client.loop_start()

            await asyncio.wait_for(connect_future, timeout=10.0)

            if self.subscribe_topic:
                self.mqtt_client.subscribe(self.subscribe_topic, qos=1)
            logger.info("MQTT control connection established")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to MQTT server: {e}", exc_info=True)
            # Stop Paho network thread on initial connection failure to prevent late callbacks from bypassing application state.
            await self._do_cleanup()
            if self._on_network_error:
                await self._on_network_error(f"Failed to connect to MQTT server: {e}")
            return False

    async def _open_udp_audio_session(self) -> bool:
        """Negotiate and open a UDP audio session over the connected MQTT control channel."""
        self.server_hello_event = asyncio.Event()
        self.session_id = ""
        self._udp.reset_session()

        hello_message = {
            "type": "hello",
            "version": 3,
            "features": {"mcp": True},
            "transport": "udp",
            "audio_params": {
                "format": "opus",
                "sample_rate": AudioConfig.INPUT_SAMPLE_RATE,
                "channels": AudioConfig.CHANNELS,
                "frame_duration": AudioConfig.FRAME_DURATION,
            },
        }

        if not await self.send_text(json.dumps(hello_message)):
            logger.error("Failed to send hello message")
            return False

        try:
            await asyncio.wait_for(self.server_hello_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("Timed out waiting for server hello message")
            if self._on_network_error:
                await self._on_network_error("Timed out waiting for response")
            return False

        try:
            self._udp.start()
            self.connected = True
            self._reconnect_attempts = 0
            self._start_connection_monitor()

            if self._on_audio_channel_opened:
                await self._on_audio_channel_opened()
            if self._on_connection_state_changed:
                self._on_connection_state_changed(True, "Connected")
            return True
        except Exception as e:
            logger.error(f"Failed to create UDP socket: {e}", exc_info=True)
            self._udp.reset_session()
            if self._on_network_error:
                await self._on_network_error(f"Failed to create UDP connection: {e}")
            return False

    def _handle_mqtt_message(self, payload):
        """Handle MQTT JSON messages (thread callback)."""
        try:
            data = json.loads(payload)
            msg_type = data.get("type")

            if msg_type == "goodbye":
                session_id = data.get("session_id")
                if not session_id or session_id == self.session_id:
                    asyncio.run_coroutine_threadsafe(self._handle_goodbye(), self.loop)
                return

            if msg_type == "hello":
                logger.debug(f"Server connection returned initialization config: {data}")
                transport = data.get("transport")
                if transport != "udp":
                    logger.error(f"Unsupported transport: {transport}")
                    return

                self.session_id = data.get("session_id", "")

                udp = data.get("udp")
                if not udp:
                    logger.error("UDP configuration is missing")
                    return

                self._udp.configure(
                    server=udp.get("server"),
                    port=udp.get("port"),
                    aes_key=udp.get("key"),
                    aes_nonce=udp.get("nonce"),
                )

                logger.info(
                    f"Received server hello response, UDP server: "
                    f"{self._udp.server}:{self._udp.port}"
                )

                self.loop.call_soon_threadsafe(self.server_hello_event.set)

                return

            if msg_type == "mcp":
                mcp_payload = data.get("payload")
                if isinstance(mcp_payload, dict):
                    logger.info(
                        "[MQTT MCP] Received server request: method=%s, id=%s",
                        mcp_payload.get("method"),
                        mcp_payload.get("id"),
                    )
                else:
                    logger.warning("[MQTT MCP] Received invalid MCP payload")

            if self._on_incoming_json:

                def process_json(json_data=data):
                    if asyncio.iscoroutinefunction(self._on_incoming_json):
                        coro = self._on_incoming_json(json_data)
                        if coro is not None:
                            asyncio.run_coroutine_threadsafe(coro, self.loop)
                    else:
                        self._on_incoming_json(json_data)

                self.loop.call_soon_threadsafe(process_json)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON data: {payload}")
        except Exception as e:
            logger.error(f"Error handling MQTT message: {e}", exc_info=True)

    async def send_text(self, message):
        if not self.mqtt_client:
            logger.error("MQTT client is not initialized")
            return False

        try:
            result = self.mqtt_client.publish(self.publish_topic, message, qos=0)
            if result.rc not in (mqtt.MQTT_ERR_SUCCESS, mqtt.MQTT_ERR_AGAIN):
                logger.error("Failed to enqueue MQTT message: rc=%s", result.rc)
                return False

            # Paho network loop runs independently of asyncio; wait in a worker thread
            # to avoid blocking event handling during MCP handshakes and bound wait time.
            await asyncio.to_thread(
                result.wait_for_publish, timeout=_MQTT_PUBLISH_TIMEOUT
            )
            if not result.is_published():
                logger.error("MQTT message publish timed out: %.1fs", _MQTT_PUBLISH_TIMEOUT)
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to send MQTT message: {e}", exc_info=True)
            if self._on_network_error:
                await self._on_network_error(f"Failed to send MQTT message: {e}")
            return False

    async def send_audio(self, audio_data):
        try:
            return self._udp.send_audio(audio_data)
        except Exception as e:
            logger.error(f"Failed to send audio data: {e}", exc_info=True)
            if self._on_network_error:
                self._schedule_coro(
                    self._on_network_error(f"Failed to send audio data: {e}"),
                    name="send_audio_network_error",
                )
            return False

    async def open_audio_channel(self):
        if self.is_audio_channel_opened():
            return True
        if not await self._connect_control_channel():
            self._schedule_control_reconnect()
            return False
        return await self._open_udp_audio_session()

    async def close_audio_channel(self):
        """End the current UDP session while keeping the MQTT control connection for reuse."""
        self._is_closing = True
        try:
            await self._cancel_monitor_task()
            if (
                self.session_id
                and self.mqtt_client
                and self.mqtt_client.is_connected()
            ):
                goodbye_msg = {"type": "goodbye", "session_id": self.session_id}
                await self.send_text(json.dumps(goodbye_msg))
            await self._close_udp_audio_session()
        except Exception as e:
            logger.error(f"Error closing audio channel: {e}", exc_info=True)
        finally:
            self._is_closing = False

    def is_audio_channel_opened(self) -> bool:
        if not self.connected or self._is_closing:
            return False
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return False
        return self._udp.is_ready()

    async def _handle_goodbye(self):
        """Server ended session: release UDP only without sending goodbye back."""
        try:
            await self._cancel_monitor_task()
            await self._close_udp_audio_session()
        except Exception as e:
            logger.error(f"Error handling goodbye message: {e}", exc_info=True)

    async def _close_udp_audio_session(self) -> None:
        """Release UDP session state without touching the reusable MQTT broker connection."""
        was_open = self.connected or self._udp.is_ready()
        self._udp.reset_session()
        self.connected = False
        self.session_id = ""
        logger.info("UDP audio session closed, keeping MQTT control connection")

        if was_open and self._on_audio_channel_closed:
            await self._on_audio_channel_closed()

    async def _handle_broker_disconnect(self, reason: str) -> None:
        """Broker disconnected: close UDP session and schedule MQTT control channel reconnect."""
        if self.mqtt_client is None:
            return

        was_audio_open = self.connected or self._udp.is_ready()
        await self._cancel_monitor_task()
        self._disconnect_mqtt_client()

        if was_audio_open:
            await self._close_udp_audio_session()
        else:
            self._udp.reset_session()
            self.session_id = ""

        if not self._is_closing and self._on_network_error:
            await self._on_network_error(f"Connection lost: {reason}")
        if not self._is_closing:
            self._schedule_control_reconnect()

    def _can_reconnect_control(self) -> bool:
        checker = self._idle_checker
        if checker is None:
            return True
        try:
            return bool(checker())
        except Exception as e:
            logger.debug(f"Failed to check idle state for MQTT reconnect: {e}", exc_info=True)
            return False

    def _schedule_control_reconnect(self) -> None:
        """Schedule ESP32-style fixed 60-second Broker reconnect only when idle."""
        task = self._control_reconnect_task
        if task is not None and not task.done():
            return
        self._control_reconnect_task = asyncio.create_task(
            self._control_reconnect_loop(), name="mqtt:control_reconnect"
        )

    async def _control_reconnect_loop(self) -> None:
        current_task = asyncio.current_task()
        try:
            while not self._is_closing:
                await asyncio.sleep(_CONTROL_RECONNECT_DELAY)
                if self._is_closing:
                    return
                if not self._can_reconnect_control():
                    logger.info("MQTT control connection reconnect waiting for device to become idle")
                    continue
                logger.info("MQTT control connection disconnected, starting fixed-interval reconnect")
                if await self._connect_control_channel():
                    logger.info("MQTT control connection reconnected (UDP session not opened)")
                    return
        except asyncio.CancelledError:
            logger.debug("MQTT control connection reconnect task cancelled")
        finally:
            if self._control_reconnect_task is current_task:
                self._control_reconnect_task = None

    async def _cancel_control_reconnect_task(self) -> None:
        task = self._control_reconnect_task
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._control_reconnect_task = None

    def _disconnect_mqtt_client(self) -> None:
        """Stop Paho network thread and discard MQTT client (called only on full cleanup paths)."""
        client = self.mqtt_client
        self.mqtt_client = None
        self._last_activity_time = None
        if not client:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception as e:
            logger.warning(f"Error disconnecting MQTT client: {e}", exc_info=True)

    async def _shutdown_control_channel(self):
        """Close the reusable MQTT control connection when the application exits."""
        await self._cancel_control_reconnect_task()
        self._disconnect_mqtt_client()

    def _stop_udp_receiver(self):
        """Stop UDP receiver (used by disconnect callbacks, etc.)."""
        self._udp.stop()

    def __del__(self):
        task = getattr(self, "_control_reconnect_task", None)
        if task is not None and not task.done():
            task.cancel()
        try:
            self._udp.stop()
        except Exception:
            pass
        self._disconnect_mqtt_client()

    # ============ Template method implementations ============

    @property
    def _monitor_interval(self) -> float:
        return 30.0

    def _is_connected(self) -> bool:
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return False
        if self._last_activity_time:
            if time.time() - self._last_activity_time > self._connection_timeout:
                return False
        return True

    async def _do_cleanup(self):
        self._udp.reset_session()
        self.connected = False
        self.session_id = ""
        self._disconnect_mqtt_client()

    def get_connection_info(self) -> dict:
        info = super().get_connection_info()
        info.update(
            {
                "connected": self.connected,
                "mqtt_connected": (
                    self.mqtt_client.is_connected() if self.mqtt_client else False
                ),
                "last_activity_time": self._last_activity_time,
                "keep_alive_interval": self._keep_alive_interval,
                "connection_timeout": self._connection_timeout,
                "mqtt_endpoint": self.endpoint,
                "udp_server": (
                    f"{self._udp.server}:{self._udp.port}" if self._udp.server else None
                ),
                "session_id": self.session_id,
            }
        )
        return info
