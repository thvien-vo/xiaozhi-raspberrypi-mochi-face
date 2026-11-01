"""Event bus.

Provides a decoupled communication mechanism between components.
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from src.logging import get_logger

logger = get_logger()


# Predefined event names
class Events:
    """
    Predefined event constants.
    """

    # Device state
    DEVICE_STATE_CHANGED = "device_state_changed"

    # Protocol-related
    PROTOCOL_CONNECTED = "protocol_connected"
    PROTOCOL_DISCONNECTED = "protocol_disconnected"
    INCOMING_JSON = "incoming_json"
    INCOMING_AUDIO = "incoming_audio"

    # Network error
    NETWORK_ERROR = "network_error"

    # Audio channel
    AUDIO_CHANNEL_OPENED = "audio_channel_opened"
    AUDIO_CHANNEL_CLOSED = "audio_channel_closed"
    # AudioCodec lifecycle (AudioPlugin -> MusicPlayer and other subscribers, avoids direct setter calls)
    AUDIO_CODEC_CHANGED = "audio_codec_changed"
    # Request to re-enumerate audio devices (settings page refresh; stops streams before PortAudio reinit)
    # payload: optional asyncio.Future, set_result(list|dict|None) upon completion
    AUDIO_DEVICES_REFRESH_REQUEST = "audio_devices_refresh_request"

    # Application lifecycle
    APP_SHUTDOWN = "app_shutdown"
    # System-level notice (degraded mode banner, etc., payload: str)
    SYSTEM_NOTICE = "system_notice"

    # Music player events
    MUSIC_STATE_CHANGED = "music_state_changed"  # Playback state changed
    MUSIC_LYRICS_UPDATE = "music_lyrics_update"  # Lyrics updated
    MUSIC_PROGRESS_UPDATE = "music_progress_update"  # Progress updated

    # Server one-way notification subtitle (payload: str)
    NOTIFICATION_SUBTITLE = "notification_subtitle"

    # Music control commands (controlling MusicPlayer externally)
    MUSIC_PAUSE_REQUEST = "music_pause_request"  # Request pause (e.g. during TTS)
    MUSIC_RESUME_REQUEST = "music_resume_request"  # Request resume

    # UI actions (UI -> plugin)
    UI_BUTTON_PRESS = "ui_button_press"  # Manual: press
    UI_BUTTON_RELEASE = "ui_button_release"  # Manual: release
    UI_MANUAL_TOGGLE = "ui_manual_toggle"  # Manual: click to start/stop recording
    UI_AUTO_TOGGLE = "ui_auto_toggle"  # Toggle auto/manual
    UI_AUTO_START = "ui_auto_start"  # Auto: start/stop conversation
    UI_ABORT_REQUEST = "ui_abort_request"  # Interrupt/abort
    UI_SEND_TEXT = "ui_send_text"  # Send text
    UI_QUIT_REQUEST = "ui_quit_request"  # Quit
    UI_OPEN_SETTINGS = "ui_open_settings"  # Open settings
    UI_TOGGLE_WINDOW = "ui_toggle_window"  # Show/hide main window (GUI)

    # Configuration change events
    CONFIG_CHANGED = "config_changed"  # Configuration changed (requires hot-reload)
    # After MCP tool exposure changes: disconnect and reconnect protocol so server re-fetches tools/list
    PROTOCOL_RECONNECT_REQUEST = "protocol_reconnect_request"


# Set of known event names: logs a warning on on/emit if a typo is detected (debug-friendly)
_KNOWN_EVENTS: frozenset[str] = frozenset(
    v for k, v in vars(Events).items() if not k.startswith("_") and isinstance(v, str)
)


class EventBus:
    """Event bus.

    Supports asynchronous event handling for loosely coupled inter-component communication.

    Usage:     bus = EventBus()

    # Register handler async def on_state_changed(state):     print(f"State: {state}")

    bus.on(Events.DEVICE_STATE_CHANGED, on_state_changed)

    # Emit event await bus.emit(Events.DEVICE_STATE_CHANGED, DeviceState.LISTENING)

    # Remove handler bus.off(Events.DEVICE_STATE_CHANGED, on_state_changed)
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable[..., Awaitable[None]]]] = defaultdict(
            list
        )

    @staticmethod
    def _warn_if_unknown(event: str, action: str) -> None:
        if event not in _KNOWN_EVENTS:
            logger.warning(
                f"EventBus: {action} unknown event name {event!r} (possible typo; "
                "please use Events.* constants)"
            )

    def on(self, event: str, handler: Callable[..., Awaitable[None]]) -> None:
        """Register event handler.

        Args:
            event: Event name
            handler: Asynchronous handler function
        """
        self._warn_if_unknown(event, "registering")
        if handler not in self._handlers[event]:
            self._handlers[event].append(handler)
            logger.debug(f"EventBus: registered handler {handler.__name__} -> {event}")

    def off(self, event: str, handler: Callable[..., Awaitable[None]]) -> None:
        """Remove event handler.

        Args:
            event: Event name
            handler: Handler function to remove
        """
        if handler in self._handlers[event]:
            self._handlers[event].remove(handler)
            logger.debug(f"EventBus: removed handler {handler.__name__} <- {event}")

    def clear(self, event: str = None) -> None:
        """Clear event handlers.

        Args:
            event: Event name; clears all events when None
        """
        if event is None:
            self._handlers.clear()
            logger.debug("EventBus: cleared all handlers")
        elif event in self._handlers:
            self._handlers[event].clear()
            logger.debug(f"EventBus: cleared all handlers for event {event}")

    async def emit(self, event: str, data: Any = None) -> None:
        """Emit event.

        Calls all registered handlers concurrently.

        Args:
            event: Event name
            data: Event payload
        """
        handlers = list(self._handlers.get(event, []))
        if not handlers:
            # Warn about unknown event names even when there are no subscribers to avoid silent typos
            self._warn_if_unknown(event, "emitting")
            return

        logger.debug(f"EventBus: emitting event {event}, {len(handlers)} handlers")

        # Execute all handlers concurrently
        tasks = []
        for handler in handlers:
            try:
                tasks.append(self._safe_call(handler, data))
            except Exception as e:
                logger.error(
                    f"EventBus: failed to create task for {handler.__name__}: {e}",
                    exc_info=True,
                )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def emit_sequential(self, event: str, data: Any = None) -> None:
        """Emit event sequentially.

        Calls handlers one by one in registration order.

        Args:
            event: Event name
            data: Event payload
        """
        handlers = list(self._handlers.get(event, []))
        for handler in handlers:
            await self._safe_call(handler, data)

    async def _safe_call(
        self, handler: Callable[..., Awaitable[None]], data: Any
    ) -> None:
        """
        Safely invoke handler and catch exceptions.
        """
        try:
            if data is None:
                await handler()
            else:
                await handler(data)
        except Exception as e:
            logger.error(
                f"EventBus: exception in handler {handler.__name__}: {e}",
                exc_info=True,
            )

    def has_handlers(self, event: str) -> bool:
        """
        Check whether an event has registered handlers.
        """
        return bool(self._handlers.get(event))

    def handler_count(self, event: str) -> int:
        """
        Get the number of handlers for an event.
        """
        return len(self._handlers.get(event, []))
