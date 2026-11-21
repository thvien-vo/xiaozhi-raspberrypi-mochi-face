"""Audio device manager.

Responsibilities:
- Device discovery and selection
- Configuration persistence (by device name rather than ID)
- Device information queries
"""

from dataclasses import dataclass

from src.constants.constants import AudioConfig
from src.logging import get_logger
from src.utils.audio_utils import find_device_by_name, select_audio_device
from src.utils.config_manager import ConfigManager

logger = get_logger()


@dataclass
class DeviceConfig:
    """Device configuration dataclass"""

    input_device_id: int
    output_device_id: int
    input_sample_rate: int
    output_sample_rate: int
    input_channels: int
    output_channels: int
    input_frame_size: int
    output_frame_size: int


class AudioDeviceManager:
    """Audio device manager (stateless, pure logic)"""

    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager

    def load_or_detect_devices(self) -> DeviceConfig:
        """Load configuration or auto-detect devices (matching by name)

        Returns:
            DeviceConfig: Device configuration

        Raises:
            RuntimeError: Unable to find available audio devices
        """
        audio_config = self.config.get_config("AUDIO_DEVICES", {}) or {}

        input_device_name = audio_config.get("input_device_name")
        output_device_name = audio_config.get("output_device_name")

        # 1. Try finding devices by name
        input_info = None
        output_info = None

        if input_device_name:
            logger.info(f"Looking for input device: {input_device_name}")
            input_info = find_device_by_name("input", input_device_name)
            if input_info:
                logger.info(f"Found input device: {input_info['name']} (ID: {input_info['index']})")
            else:
                logger.warning(f"Device '{input_device_name}' not found, will reselect")

        if output_device_name:
            logger.info(f"Looking for output device: {output_device_name}")
            output_info = find_device_by_name("output", output_device_name)
            if output_info:
                logger.info(f"Found output device: {output_info['name']} (ID: {output_info['index']})")
            else:
                logger.warning(f"Device '{output_device_name}' not found, will reselect")

        # 2. Strict names: a configured device that is missing is an error, never a silent
        # fallback to another device (a webcam microphone, say)
        if audio_config.get("strict_names"):
            for kind, name, info in (
                ("input", input_device_name, input_info),
                ("output", output_device_name, output_info),
            ):
                if name and not info:
                    raise RuntimeError(f"Configured {kind} audio device '{name}' not found")

        # 2b. Auto-select new device if lookup by name failed
        if not input_info:
            logger.info("Auto-selecting input device...")
            input_info = select_audio_device("input")
            if not input_info:
                raise RuntimeError("No available input device found")

        if not output_info:
            logger.info("Auto-selecting output device...")
            output_info = select_audio_device("output")
            if not output_info:
                raise RuntimeError("No available output device found")

        # 3. Fixed mono for input (protocol requirement + avoids array driver latency), keep native channel count for output
        input_channels = 1
        output_channels = output_info["channels"]

        device_input_sample_rate = input_info["sample_rate"]
        device_output_sample_rate = output_info["sample_rate"]
        # Some USB speakerphones list 8/16 kHz but really clock playback at 48 kHz (a
        # Jabra Speak 510 plays 16 kHz audio three times too fast on a Pi 5), which starves
        # the output FIFO and sounds like harsh stuttering. Let the config force a rate.
        forced_rate = self.config.get_config("AUDIO_DEVICES.force_output_sample_rate")
        if forced_rate:
            logger.info(
                f"Output sample rate forced to {forced_rate}Hz "
                f"(device reports {device_output_sample_rate}Hz)"
            )
            device_output_sample_rate = int(forced_rate)

        logger.info(
            f"Using input device: {input_info['name']} | "
            f"{device_input_sample_rate}Hz {input_channels}ch"
        )
        logger.info(
            f"Using output device: {output_info['name']} | "
            f"{device_output_sample_rate}Hz {output_channels}ch"
        )

        # 4. Save device names (instead of IDs) to configuration. A configured name stays
        # as written: a partial name like "Jabra" must survive ALSA renumbering the card
        # (the full name contains "hw:2,0") and a boot with the device unplugged.
        if not input_device_name or not output_device_name:
            self.config.update_config(
                "AUDIO_DEVICES.input_device_name",
                input_device_name or input_info["name"],
            )
            self.config.update_config("AUDIO_DEVICES.input_sample_rate", device_input_sample_rate)
            self.config.update_config("AUDIO_DEVICES.input_channels", input_channels)
            self.config.update_config(
                "AUDIO_DEVICES.output_device_name",
                output_device_name or output_info["name"],
            )
            self.config.update_config("AUDIO_DEVICES.output_sample_rate", device_output_sample_rate)
            self.config.update_config("AUDIO_DEVICES.output_channels", output_channels)
            logger.info("Device configuration saved")

        return DeviceConfig(
            input_device_id=input_info["index"],
            output_device_id=output_info["index"],
            input_sample_rate=device_input_sample_rate,
            output_sample_rate=device_output_sample_rate,
            input_channels=input_channels,
            output_channels=output_channels,
            input_frame_size=int(
                device_input_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
            ),
            output_frame_size=int(
                device_output_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
            ),
        )
