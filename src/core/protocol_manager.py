"""Protocol manager.

Wraps communication protocol operations and forwards messages via the event bus.
Audio data goes through a direct channel (bounded queue + single consumer) to avoid per-frame create_task accumulation.
"""

import asyncio
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from src.constants.constants import ListeningMode
from src.core.event_bus import EventBus, Events
from src.logging import get_logger

if TYPE_CHECKING:
    from src.core.task_manager import TaskManager
    from src.protocols.protocol import Protocol

logger = get_logger()

# Audio callback type
AudioCallback = Callable[[bytes], Awaitable[None]]

# Inbound audio bounded queue: drops the oldest frame when full to prevent flooding the event loop with tasks
_INCOMING_AUDIO_QUEUE_SIZE = 64


class ProtocolTransport:
    """Responsible for protocol creation and connection management."""

    def __init__(
        self,
        event_bus: EventBus,
        task_manager: Optional["TaskManager"] = None,
    ):
        self._event_bus = event_bus
        self._task_manager = task_manager
        self._protocol: Optional["Protocol"] = None
        self._connect_lock = asyncio.Lock()
        self._incoming_audio_handler: Optional[AudioCallback] = None

        # Bounded audio queue + single consumer (replaces per-packet create_task)
        self._audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(
            maxsize=_INCOMING_AUDIO_QUEUE_SIZE
        )
        self._audio_consumer_task: Optional[asyncio.Task] = None
        self._audio_consumer_running = False

    def set_task_manager(self, task_manager: "TaskManager") -> None:
        """Inject TaskManager (can be bound after container initialization)."""
        self._task_manager = task_manager

    @property
    def protocol(self) -> Optional["Protocol"]:
        return self._protocol

    def set_protocol(self, protocol_type: str) -> None:
        logger.debug(f"Setting protocol type: {protocol_type}")

        if protocol_type == "mqtt":
            from src.protocols.mqtt_protocol import MqttProtocol

            self._protocol = MqttProtocol(asyncio.get_running_loop())
        else:
            from src.protocols.websocket_protocol import WebsocketProtocol

            self._protocol = WebsocketProtocol()

        self._setup_callbacks()
        self._ensure_audio_consumer()

    def set_audio_handler(self, handler: Optional[AudioCallback]) -> None:
        self._incoming_audio_handler = handler
        self._ensure_audio_consumer()

    def set_idle_checker(self, checker: Callable[[], bool]) -> None:
        if self._protocol:
            self._protocol.set_idle_checker(checker)

    def _setup_callbacks(self) -> None:
        if not self._protocol:
            return

        self._protocol.on_network_error(self._on_network_error)
        self._protocol.on_incoming_json(self._on_incoming_json)
        self._protocol.on_incoming_audio(self._on_incoming_audio)
        self._protocol.on_audio_channel_opened(self._on_audio_channel_opened)
        self._protocol.on_audio_channel_closed(self._on_audio_channel_closed)

    def _spawn(self, coro: Awaitable, name: str) -> None:
        """Prefer TaskManager; otherwise fall back to local create_task and log exceptions."""
        if self._task_manager is not None:
            task = self._task_manager.spawn(coro, name=name)
            if task is None:
                # Shutting down: close unscheduled coroutine to avoid RuntimeWarning
                if asyncio.iscoroutine(coro):
                    coro.close()
            return

        try:
            task = asyncio.create_task(coro, name=name)

            def _on_done(t: asyncio.Task) -> None:
                if t.cancelled():
                    return
                exc = t.exception()
                if exc:
                    logger.error(f"Task {name} exited with exception: {exc}", exc_info=exc)

            task.add_done_callback(_on_done)
        except Exception as e:
            logger.error(f"Failed to create task {name}: {e}", exc_info=True)
            if asyncio.iscoroutine(coro):
                coro.close()

    def _ensure_audio_consumer(self) -> None:
        """Ensure inbound audio consumer is running (idempotent)."""
        if self._audio_consumer_running:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        self._audio_consumer_running = True
        if self._task_manager is not None:
            self._audio_consumer_task = self._task_manager.spawn(
                self._audio_consumer_loop(), name="protocol:audio_consumer"
            )
        else:
            self._audio_consumer_task = asyncio.create_task(
                self._audio_consumer_loop(), name="protocol:audio_consumer"
            )

        if self._audio_consumer_task is None:
            self._audio_consumer_running = False
            return

        def _on_done(t: asyncio.Task) -> None:
            self._audio_consumer_running = False
            self._audio_consumer_task = None
            if t.cancelled():
                return
            # TaskManager.spawn already logs exceptions; log here only when using local create_task
            if self._task_manager is None:
                exc = t.exception()
                if exc:
                    logger.error(f"Audio consumer exited with exception: {exc}", exc_info=exc)

        self._audio_consumer_task.add_done_callback(_on_done)

    async def _audio_consumer_loop(self) -> None:
        """Single consumer serially processes inbound audio to avoid per-frame task explosion."""
        while True:
            data = await self._audio_queue.get()
            if data is None:
                # Poison pill: exit
                return
            try:
                if self._incoming_audio_handler:
                    await self._incoming_audio_handler(data)
                else:
                    await self._event_bus.emit(Events.INCOMING_AUDIO, data)
            except Exception as e:
                logger.error(f"Failed to process inbound audio: {e}", exc_info=True)

    def _enqueue_audio(self, data: bytes) -> None:
        """Bounded enqueue: drop oldest frame if full before inserting newest frame."""
        try:
            self._audio_queue.put_nowait(data)
            return
        except asyncio.QueueFull:
            pass

        # Drop oldest
        try:
            self._audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            self._audio_queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("Inbound audio queue still full, dropping current frame")

    async def _stop_audio_consumer(self) -> None:
        """Stop consumer and clear queue."""
        if self._audio_consumer_task and not self._audio_consumer_task.done():
            try:
                self._audio_queue.put_nowait(None)
            except asyncio.QueueFull:
                # Force free a slot for the poison pill when queue is full
                try:
                    self._audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    self._audio_queue.put_nowait(None)
                except asyncio.QueueFull:
                    self._audio_consumer_task.cancel()

            try:
                await self._audio_consumer_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Exception while waiting for audio consumer to exit: {e}")

        self._audio_consumer_task = None
        self._audio_consumer_running = False

        # Clear remaining items
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _on_network_error(self, error_message: str = None) -> None:
        if error_message:
            logger.error(f"Network error: {error_message}")
        await self._event_bus.emit(Events.NETWORK_ERROR, error_message)

    def _on_incoming_json(self, json_data: dict) -> None:
        self._spawn(
            self._event_bus.emit(Events.INCOMING_JSON, json_data),
            name="protocol:incoming_json",
        )

    def _on_incoming_audio(self, data: bytes) -> None:
        try:
            self._ensure_audio_consumer()
            self._enqueue_audio(data)
        except Exception as exc:
            logger.warning(f"Failed to dispatch audio data: {exc}", exc_info=True)

    async def _on_audio_channel_opened(self) -> None:
        logger.info("Protocol channel opened")
        await self._event_bus.emit(Events.AUDIO_CHANNEL_OPENED)
        await self._event_bus.emit(Events.PROTOCOL_CONNECTED, self._protocol)

    async def _on_audio_channel_closed(self) -> None:
        logger.info("Protocol channel closed")
        await self._event_bus.emit(Events.AUDIO_CHANNEL_CLOSED)
        await self._event_bus.emit(Events.PROTOCOL_DISCONNECTED)

    def is_audio_channel_opened(self) -> bool:
        try:
            return bool(self._protocol and self._protocol.is_audio_channel_opened())
        except Exception:
            logger.debug("Exception occurred while checking audio channel status", exc_info=True)
            return False

    async def connect(self, timeout: float = 12.0) -> bool:
        if self.is_audio_channel_opened():
            return True

        if not self._protocol:
            logger.error("Protocol is not initialized")
            return False

        async with self._connect_lock:
            if self.is_audio_channel_opened():
                return True

            try:
                opened = await asyncio.wait_for(
                    self._protocol.open_audio_channel(),
                    timeout=timeout,
                )
                if not opened:
                    logger.error("Protocol connection failed")
                    return False

                logger.info("Protocol connection established")
                return True

            except asyncio.TimeoutError:
                logger.error("Protocol connection timed out")
                return False
            except Exception as e:
                logger.error(f"Protocol connection error: {e}", exc_info=True)
                return False

    async def start(self) -> bool:
        """Start the persistent control channel of the protocol (if required by the protocol)."""
        if not self._protocol:
            logger.error("Protocol is not initialized")
            return False
        try:
            return bool(await self._protocol.start())
        except Exception as e:
            logger.error(f"Failed to start protocol control channel: {e}", exc_info=True)
            return False

    async def disconnect(self) -> None:
        """End the current audio session while allowing the protocol to reuse its control connection."""
        if self._protocol:
            try:
                await self._protocol.close_audio_channel()
            except Exception as e:
                logger.error(f"Failed to close protocol channel: {e}", exc_info=True)
        await self._stop_audio_consumer()

    async def shutdown(self) -> None:
        """Completely shut down the protocol and its background resources (application exit path)."""
        if self._protocol:
            try:
                await self._protocol.shutdown()
            except Exception as e:
                logger.error(f"Failed to shut down protocol completely: {e}", exc_info=True)
        await self._stop_audio_consumer()


class ProtocolGateway:
    """Gateway responsible for sending messages."""

    def __init__(self, transport: ProtocolTransport):
        self._transport = transport

    async def send_audio(self, data: bytes) -> None:
        protocol = self._transport.protocol
        if protocol and self._transport.is_audio_channel_opened():
            await protocol.send_audio(data)
        else:
            logger.debug("Audio channel is not open, skipping audio data transmission")

    async def send_text(self, text: str) -> None:
        protocol = self._transport.protocol
        if protocol:
            await protocol.send_text(text)

    async def send_start_listening(self, mode: ListeningMode) -> None:
        protocol = self._transport.protocol
        if protocol and self._transport.is_audio_channel_opened():
            await protocol.send_start_listening(mode)
        else:
            logger.warning("Audio channel is not open, skipping send_start_listening")

    async def send_stop_listening(self) -> None:
        protocol = self._transport.protocol
        if protocol and self._transport.is_audio_channel_opened():
            await protocol.send_stop_listening()
        else:
            logger.debug("Audio channel is not open, skipping send_stop_listening")

    async def send_abort_speaking(self, reason: str = None) -> None:
        protocol = self._transport.protocol
        if protocol:
            await protocol.send_abort_speaking(reason)

    async def send_wake_word_detected(self, wake_word: str) -> None:
        protocol = self._transport.protocol
        if protocol and self._transport.is_audio_channel_opened():
            await protocol.send_wake_word_detected(wake_word)
        else:
            logger.warning("Audio channel is not open, skipping send_wake_word_detected")

    async def send_iot_descriptors(self, descriptors) -> None:
        protocol = self._transport.protocol
        if protocol:
            await protocol.send_iot_descriptors(descriptors)

    async def send_iot_states(self, states) -> None:
        protocol = self._transport.protocol
        if protocol:
            await protocol.send_iot_states(states)

    async def send_mcp_message(self, payload) -> bool | None:
        protocol = self._transport.protocol
        if protocol:
            return await protocol.send_mcp_message(payload)
        return False


class ProtocolManager:
    """Exposes a unified interface externally while composing Transport + Gateway internally."""

    def __init__(
        self,
        event_bus: EventBus,
        task_manager: Optional["TaskManager"] = None,
    ):
        self._transport = ProtocolTransport(event_bus, task_manager=task_manager)
        self._gateway = ProtocolGateway(self._transport)

    def set_task_manager(self, task_manager: "TaskManager") -> None:
        self._transport.set_task_manager(task_manager)

    @property
    def protocol(self) -> Optional["Protocol"]:
        return self._transport.protocol

    def set_protocol(self, protocol_type: str) -> None:
        self._transport.set_protocol(protocol_type)

    def set_audio_handler(self, handler: Optional[AudioCallback]) -> None:
        self._transport.set_audio_handler(handler)

    def set_idle_checker(self, checker: Callable[[], bool]) -> None:
        self._transport.set_idle_checker(checker)

    def is_audio_channel_opened(self) -> bool:
        return self._transport.is_audio_channel_opened()

    async def connect(self, timeout: float = 12.0) -> bool:
        return await self._transport.connect(timeout)

    async def start(self) -> bool:
        return await self._transport.start()

    async def disconnect(self) -> None:
        await self._transport.disconnect()

    async def shutdown(self) -> None:
        await self._transport.shutdown()

    async def send_audio(self, data: bytes) -> None:
        await self._gateway.send_audio(data)

    async def send_text(self, text: str) -> None:
        await self._gateway.send_text(text)

    async def send_start_listening(self, mode: ListeningMode) -> None:
        await self._gateway.send_start_listening(mode)

    async def send_stop_listening(self) -> None:
        await self._gateway.send_stop_listening()

    async def send_abort_speaking(self, reason: str = None) -> None:
        await self._gateway.send_abort_speaking(reason)

    async def send_wake_word_detected(self, wake_word: str) -> None:
        await self._gateway.send_wake_word_detected(wake_word)

    async def send_iot_descriptors(self, descriptors) -> None:
        await self._gateway.send_iot_descriptors(descriptors)

    async def send_iot_states(self, states) -> None:
        await self._gateway.send_iot_states(states)

    async def send_mcp_message(self, payload) -> bool | None:
        return await self._gateway.send_mcp_message(payload)
