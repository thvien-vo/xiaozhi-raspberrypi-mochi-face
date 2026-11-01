"""State manager.

Centralizes device state management and broadcasts state changes via the event bus.
"""

import asyncio
from typing import TYPE_CHECKING

from src.constants.constants import DeviceState, ListeningMode
from src.core.event_bus import EventBus, Events
from src.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger()


class StateManager:
    """Device state manager.

    Responsibilities:
    - Manage device state (IDLE, LISTENING, SPEAKING)
    - Manage listening mode (REALTIME, AUTO_STOP, MANUAL)
    - Manage session state (keep_listening, aec_enabled)
    - Broadcast state changes via the event bus

    Usage:
        state = StateManager(event_bus)

        # Set state (automatically broadcasts)
        await state.set_device_state(DeviceState.LISTENING)

        # Read state
        if state.is_listening():
            ...
    """

    def __init__(self, event_bus: EventBus, aec_enabled: bool = True):
        self._event_bus = event_bus
        self._lock = asyncio.Lock()

        # Device state
        self._device_state: DeviceState = DeviceState.IDLE

        # AEC configuration
        self._aec_enabled: bool = aec_enabled

        # Listening mode: default mode determined by AEC configuration
        self._listening_mode: ListeningMode = (
            ListeningMode.REALTIME if aec_enabled else ListeningMode.AUTO_STOP
        )

        # Session state
        self._keep_listening: bool = False

        # Abort flag
        self._aborted: bool = False

    # -------------------------
    # Device state
    # -------------------------
    @property
    def device_state(self) -> DeviceState:
        """
        Get current device state.
        """
        return self._device_state

    async def set_device_state(self, state: DeviceState) -> None:
        """Set device state.

        Args:
            state: New device state

        Broadcasts via the event bus if the state changes.
        """
        async with self._lock:
            if self._device_state == state:
                return

            old_state = self._device_state
            self._device_state = state
            logger.info(f"Device state changed: {old_state} -> {state}")

            # Reset abort flag
            if state == DeviceState.LISTENING:
                self._aborted = False

        # Broadcast outside the lock to avoid deadlocks
        await self._event_bus.emit(
            Events.DEVICE_STATE_CHANGED,
            {"old_state": old_state, "new_state": state},
        )

    def is_idle(self) -> bool:
        """
        Whether the device is in the idle state.
        """
        return self._device_state == DeviceState.IDLE

    def is_listening(self) -> bool:
        """
        Whether the device is currently listening.
        """
        return self._device_state == DeviceState.LISTENING

    def is_speaking(self) -> bool:
        """
        Whether the device is currently speaking.
        """
        return self._device_state == DeviceState.SPEAKING

    # -------------------------
    # Listening mode
    # -------------------------
    @property
    def listening_mode(self) -> ListeningMode:
        """
        Get current listening mode.
        """
        return self._listening_mode

    def set_listening_mode(self, mode: ListeningMode) -> None:
        """
        Set listening mode.
        """
        self._listening_mode = mode
        logger.debug(f"Listening mode set to: {mode}")

    # -------------------------
    # Session state
    # -------------------------
    @property
    def keep_listening(self) -> bool:
        """
        Whether continuous listening is enabled.
        """
        return self._keep_listening

    def set_keep_listening(self, value: bool) -> None:
        """
        Set continuous listening state.
        """
        self._keep_listening = value
        logger.debug(f"Continuous listening: {value}")

    @property
    def aec_enabled(self) -> bool:
        """
        Whether AEC is enabled.
        """
        return self._aec_enabled

    # -------------------------
    # Abort state
    # -------------------------
    @property
    def aborted(self) -> bool:
        """
        Whether output has been aborted.
        """
        return self._aborted

    def set_aborted(self, value: bool) -> None:
        """
        Set abort state.
        """
        self._aborted = value

    # -------------------------
    # Composite state queries
    # -------------------------
    def should_capture_audio(self) -> bool:
        """Whether audio should be captured.

        Capture is required when:
        1. Currently listening and not aborted
        2. Currently speaking, but AEC is enabled and continuous listening is active in REALTIME mode
        """
        if self._device_state == DeviceState.LISTENING and not self._aborted:
            return True

        return (
            self._device_state == DeviceState.SPEAKING
            and self._aec_enabled
            and self._keep_listening
            and self._listening_mode == ListeningMode.REALTIME
        )

    def get_snapshot(self) -> dict:
        """Get state snapshot.

        Returns a dictionary of all current states for debugging and logging.
        """
        return {
            "device_state": self._device_state,
            "listening_mode": self._listening_mode,
            "keep_listening": self._keep_listening,
            "aec_enabled": self._aec_enabled,
            "aborted": self._aborted,
        }
