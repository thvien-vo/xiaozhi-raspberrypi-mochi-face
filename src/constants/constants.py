import platform
from enum import Enum


class ListeningMode(str, Enum):
    """
    Listening mode.
    """

    REALTIME = "realtime"
    AUTO_STOP = "auto_stop"
    MANUAL = "manual"


class AbortReason(str, Enum):
    """
    Abort reason.
    """

    NONE = "none"
    WAKE_WORD_DETECTED = "wake_word_detected"
    USER_INTERRUPTION = "user_interruption"


class DeviceState(str, Enum):
    """
    Device state.
    """

    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"
    NOTIFYING = "notifying"


class EventType:
    """
    Event type.
    """

    SCHEDULE_EVENT = "schedule_event"
    AUDIO_INPUT_READY_EVENT = "audio_input_ready_event"
    AUDIO_OUTPUT_READY_EVENT = "audio_output_ready_event"


def get_frame_duration() -> int:
    """Get the device frame duration.

    Prefers reading from initialized configuration; auto-detects based on device
    architecture when no configuration is present.
    Does not read configuration at constants import time.

    Returns:
        int: Frame duration in milliseconds, supports 20/40/60
    """
    try:
        from src.utils.config_manager import get_config

        configured = get_config().get_config(
            "AUDIO_DEVICES.frame_duration"
        )
        if configured in [20, 40, 60]:
            return configured

        # Auto-detect when not configured (backward compatible)
        machine = platform.machine().lower()
        arm_archs = ["arm", "aarch64", "armv7l", "armv6l"]
        is_arm_device = any(arch in machine for arch in arm_archs)

        if is_arm_device:
            # ARM devices (e.g., Raspberry Pi) use larger frame duration to reduce CPU load
            return 60
        else:
            # Other devices (Windows/macOS/Linux x86) have sufficient performance for low latency
            return 20

    except Exception:
        # Return default 20ms if retrieval fails (suitable for most modern devices)
        return 20


class AudioConfig:
    """
    Audio configuration class - protocol layer parameters, independent of device layer (DeviceConfig).

    Default values are declared in the class body; dynamically loaded from ConfigManager via reload().
    No longer forces reload at import time to avoid side effects in the constants module.
    """

    # Fixed server protocol values (do not change with configuration)
    INPUT_SAMPLE_RATE = 16000  # Protocol requirement: 16kHz input
    CHANNELS = 1  # Protocol requirement: mono channel

    # Dynamic values below, re-read from configuration during reload()
    OUTPUT_SAMPLE_RATE: int = 24000
    FRAME_DURATION: int = 20
    INPUT_FRAME_SIZE: int = 320

    @classmethod
    def reload(cls):
        """Reload protocol audio parameters from ConfigManager, supporting runtime hot-reload.

        After Settings UI modifies opus_output_sample_rate / frame_duration,
        call this method so the new values take effect on the next initialize/reload_devices.
        """
        try:
            from src.utils.config_manager import get_config

            config = get_config()
            cls.OUTPUT_SAMPLE_RATE = config.get_config(
                "AUDIO_DEVICES.opus_output_sample_rate", 24000
            )
        except Exception:
            # Retain current/default value when configuration is unavailable
            pass
        cls.FRAME_DURATION = get_frame_duration()
        cls.INPUT_FRAME_SIZE = int(cls.INPUT_SAMPLE_RATE * (cls.FRAME_DURATION / 1000))
