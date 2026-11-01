"""
Python ctypes wrapper for the WebRTC Audio Processing Module.
Based on the Unity C# wrapper interface.
"""

import ctypes
import platform
from enum import IntEnum
from pathlib import Path


# Platform-specific library loading
def _get_library_path() -> str:
    """Get the platform-specific library path."""
    current_dir = Path(__file__).parent

    system = platform.system().lower()
    arch = platform.machine().lower()

    # Normalize architecture names
    if arch in ["x86_64", "amd64"]:
        arch = "x64"
    elif arch in ["aarch64", "arm64"]:
        arch = "arm64"
    elif arch in ["i386", "i686", "x86"]:
        arch = "x86"

    if system == "linux":
        lib_path = current_dir / "linux" / arch / "libwebrtc_apm.so"
    elif system == "darwin":
        lib_path = current_dir / "macos" / arch / "libwebrtc_apm.dylib"
    elif system == "windows":
        lib_path = current_dir / "windows" / arch / "libwebrtc_apm.dll"
    else:
        raise RuntimeError(f"Unsupported platform: {system}")

    if not lib_path.exists():
        raise FileNotFoundError(f"Library not found: {lib_path}")

    return str(lib_path)


# Lazy-load library (three platforms: linux / macos / windows)
_lib = None


def _ensure_library_loaded():
    """Load the dynamic library under libs/webrtc_apm/<os>/<arch>/ for the current platform."""
    global _lib

    if _lib is not None:
        return

    _lib = ctypes.CDLL(_get_library_path())
    _init_function_signatures()


# Enumerations
class DownmixMethod(IntEnum):
    """Method for converting multi-channel audio tracks to mono."""

    AVERAGE_CHANNELS = 0
    USE_FIRST_CHANNEL = 1


class NoiseSuppressionLevel(IntEnum):
    """Noise suppression level."""

    LOW = 0
    MODERATE = 1
    HIGH = 2
    VERY_HIGH = 3


class GainController1Mode(IntEnum):
    """AGC1 controller mode."""

    ADAPTIVE_ANALOG = 0
    ADAPTIVE_DIGITAL = 1
    FIXED_DIGITAL = 2


class ClippingPredictorMode(IntEnum):
    """Clipping predictor mode."""

    CLIPPING_EVENT_PREDICTION = 0
    ADAPTIVE_STEP_CLIPPING_PEAK_PREDICTION = 1
    FIXED_STEP_CLIPPING_PEAK_PREDICTION = 2


# Structures
class Pipeline(ctypes.Structure):
    """Audio processing pipeline configuration."""

    _fields_ = [
        ("maximum_internal_processing_rate", ctypes.c_int),
        ("multi_channel_render", ctypes.c_bool),
        ("multi_channel_capture", ctypes.c_bool),
        ("capture_downmix_method", ctypes.c_int),
    ]


class PreAmplifier(ctypes.Structure):
    """Pre-amplifier configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("fixed_gain_factor", ctypes.c_float),
    ]


class AnalogMicGainEmulation(ctypes.Structure):
    """Analog microphone gain emulation configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("initial_level", ctypes.c_int),
    ]


class CaptureLevelAdjustment(ctypes.Structure):
    """Capture level adjustment configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("pre_gain_factor", ctypes.c_float),
        ("post_gain_factor", ctypes.c_float),
        ("mic_gain_emulation", AnalogMicGainEmulation),
    ]


class HighPassFilter(ctypes.Structure):
    """High-pass filter configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("apply_in_full_band", ctypes.c_bool),
    ]


class EchoCanceller(ctypes.Structure):
    """Echo canceller configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("mobile_mode", ctypes.c_bool),
        ("export_linear_aec_output", ctypes.c_bool),
        ("enforce_high_pass_filtering", ctypes.c_bool),
    ]


class NoiseSuppression(ctypes.Structure):
    """Noise suppression configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("noise_level", ctypes.c_int),
        ("analyze_linear_aec_output_when_available", ctypes.c_bool),
    ]


class TransientSuppression(ctypes.Structure):
    """Transient suppression configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
    ]


class ClippingPredictor(ctypes.Structure):
    """Clipping predictor configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("predictor_mode", ctypes.c_int),
        ("window_length", ctypes.c_int),
        ("reference_window_length", ctypes.c_int),
        ("reference_window_delay", ctypes.c_int),
        ("clipping_threshold", ctypes.c_float),
        ("crest_factor_margin", ctypes.c_float),
        ("use_predicted_step", ctypes.c_bool),
    ]


class AnalogGainController(ctypes.Structure):
    """Analog gain controller configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("startup_min_volume", ctypes.c_int),
        ("clipped_level_min", ctypes.c_int),
        ("enable_digital_adaptive", ctypes.c_bool),
        ("clipped_level_step", ctypes.c_int),
        ("clipped_ratio_threshold", ctypes.c_float),
        ("clipped_wait_frames", ctypes.c_int),
        ("predictor", ClippingPredictor),
    ]


class GainController1(ctypes.Structure):
    """AGC1 configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("controller_mode", ctypes.c_int),
        ("target_level_dbfs", ctypes.c_int),
        ("compression_gain_db", ctypes.c_int),
        ("enable_limiter", ctypes.c_bool),
        ("analog_controller", AnalogGainController),
    ]


class InputVolumeController(ctypes.Structure):
    """Input volume controller configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
    ]


class AdaptiveDigital(ctypes.Structure):
    """Adaptive digital controller configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("headroom_db", ctypes.c_float),
        ("max_gain_db", ctypes.c_float),
        ("initial_gain_db", ctypes.c_float),
        ("max_gain_change_db_per_second", ctypes.c_float),
        ("max_output_noise_level_dbfs", ctypes.c_float),
    ]


class FixedDigital(ctypes.Structure):
    """Fixed digital controller configuration."""

    _fields_ = [
        ("gain_db", ctypes.c_float),
    ]


class GainController2(ctypes.Structure):
    """AGC2 configuration."""

    _fields_ = [
        ("enabled", ctypes.c_bool),
        ("volume_controller", InputVolumeController),
        ("adaptive_controller", AdaptiveDigital),
        ("fixed_controller", FixedDigital),
    ]


class Config(ctypes.Structure):
    """Main configuration structure for WebRTC audio processing."""

    _fields_ = [
        ("pipeline_config", Pipeline),
        ("pre_amp", PreAmplifier),
        ("level_adjustment", CaptureLevelAdjustment),
        ("high_pass", HighPassFilter),
        ("echo", EchoCanceller),
        ("noise_suppress", NoiseSuppression),
        ("transient_suppress", TransientSuppression),
        ("gain_control1", GainController1),
        ("gain_control2", GainController2),
    ]


# Function definitions (lazy initialization)
def _init_function_signatures():
    """Initialize function signatures (called only after library is loaded)."""
    global _lib
    if _lib is None:
        raise RuntimeError("Library not loaded. Call _ensure_library_loaded() first.")

    _lib.WebRTC_APM_Create.argtypes = []
    _lib.WebRTC_APM_Create.restype = ctypes.c_void_p

    _lib.WebRTC_APM_Destroy.argtypes = [ctypes.c_void_p]
    _lib.WebRTC_APM_Destroy.restype = None

    _lib.WebRTC_APM_CreateStreamConfig.argtypes = [ctypes.c_int, ctypes.c_int]
    _lib.WebRTC_APM_CreateStreamConfig.restype = ctypes.c_void_p

    _lib.WebRTC_APM_DestroyStreamConfig.argtypes = [ctypes.c_void_p]
    _lib.WebRTC_APM_DestroyStreamConfig.restype = ctypes.c_void_p

    _lib.WebRTC_APM_ApplyConfig.argtypes = [ctypes.c_void_p, ctypes.POINTER(Config)]
    _lib.WebRTC_APM_ApplyConfig.restype = ctypes.c_int

    _lib.WebRTC_APM_ProcessReverseStream.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_short),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_short),
    ]
    _lib.WebRTC_APM_ProcessReverseStream.restype = ctypes.c_int

    _lib.WebRTC_APM_ProcessStream.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_short),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_short),
    ]
    _lib.WebRTC_APM_ProcessStream.restype = ctypes.c_int

    _lib.WebRTC_APM_SetStreamDelayMs.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _lib.WebRTC_APM_SetStreamDelayMs.restype = None


class WebRTCAudioProcessing:
    """High-level Python wrapper for WebRTC audio processing."""

    def __init__(self):
        """Initialize the audio processing module."""
        # Ensure library is loaded for the current platform
        _ensure_library_loaded()
        _init_function_signatures()

        self._handle = _lib.WebRTC_APM_Create()
        if not self._handle:
            raise RuntimeError("Failed to create WebRTC APM instance")

    def __del__(self):
        """Clean up resources."""
        if hasattr(self, "_handle") and self._handle:
            _lib.WebRTC_APM_Destroy(self._handle)

    def create_stream_config(self, sample_rate: int, num_channels: int) -> int:
        """Create a stream configuration.

        Args:
            sample_rate: Sample rate in Hz (e.g., 16000, 48000)
            num_channels: Number of channels (1 for mono, 2 for stereo)

        Returns:
            Stream configuration handle
        """
        config_handle = _lib.WebRTC_APM_CreateStreamConfig(sample_rate, num_channels)
        if not config_handle:
            raise RuntimeError("Failed to create stream config")
        return config_handle

    def destroy_stream_config(self, config_handle: int) -> None:
        """Destroy a stream configuration."""
        _lib.WebRTC_APM_DestroyStreamConfig(config_handle)

    def apply_config(self, config: Config) -> int:
        """Apply configuration to the audio processing module.

        Args:
            config: Configuration structure

        Returns:
            Status code (0 indicates success)
        """
        return _lib.WebRTC_APM_ApplyConfig(self._handle, ctypes.byref(config))

    def process_reverse_stream(
        self, src: ctypes.Array, src_config: int, dest_config: int, dest: ctypes.Array
    ) -> int:
        """Process reverse stream (render/playback audio).

        Args:
            src: Source audio buffer
            src_config: Source stream configuration handle
            dest_config: Destination stream configuration handle
            dest: Destination audio buffer

        Returns:
            Status code (0 indicates success)
        """
        return _lib.WebRTC_APM_ProcessReverseStream(
            self._handle, src, src_config, dest_config, dest
        )

    def process_stream(
        self, src: ctypes.Array, src_config: int, dest_config: int, dest: ctypes.Array
    ) -> int:
        """Process capture stream (microphone audio).

        Args:
            src: Source audio buffer
            src_config: Source stream configuration handle
            dest_config: Destination stream configuration handle
            dest: Destination audio buffer

        Returns:
            Status code (0 indicates success)
        """
        return _lib.WebRTC_APM_ProcessStream(
            self._handle, src, src_config, dest_config, dest
        )

    def set_stream_delay_ms(self, delay_ms: int) -> None:
        """Set stream delay in milliseconds.

        Args:
            delay_ms: Delay in milliseconds
        """
        _lib.WebRTC_APM_SetStreamDelayMs(self._handle, delay_ms)


def create_default_config() -> Config:
    """Create a configuration with default settings."""
    config = Config()

    # Pipeline configuration
    config.pipeline_config.maximum_internal_processing_rate = 48000
    config.pipeline_config.multi_channel_render = False
    config.pipeline_config.multi_channel_capture = False
    config.pipeline_config.capture_downmix_method = DownmixMethod.AVERAGE_CHANNELS

    # Pre-amplifier
    config.pre_amp.enabled = False
    config.pre_amp.fixed_gain_factor = 1.0

    # Level adjustment
    config.level_adjustment.enabled = False
    config.level_adjustment.pre_gain_factor = 1.0
    config.level_adjustment.post_gain_factor = 1.0
    config.level_adjustment.mic_gain_emulation.enabled = False
    config.level_adjustment.mic_gain_emulation.initial_level = 255

    # High-pass filter
    config.high_pass.enabled = False
    config.high_pass.apply_in_full_band = True

    # Echo canceller
    config.echo.enabled = False
    config.echo.mobile_mode = False
    config.echo.export_linear_aec_output = False
    config.echo.enforce_high_pass_filtering = True

    # Noise suppression
    config.noise_suppress.enabled = False
    config.noise_suppress.noise_level = NoiseSuppressionLevel.MODERATE
    config.noise_suppress.analyze_linear_aec_output_when_available = False

    # Transient suppression
    config.transient_suppress.enabled = False

    # AGC1
    config.gain_control1.enabled = False
    config.gain_control1.controller_mode = GainController1Mode.ADAPTIVE_ANALOG
    config.gain_control1.target_level_dbfs = 3
    config.gain_control1.compression_gain_db = 9
    config.gain_control1.enable_limiter = True

    # AGC1 analog controller
    config.gain_control1.analog_controller.enabled = True
    config.gain_control1.analog_controller.startup_min_volume = 0
    config.gain_control1.analog_controller.clipped_level_min = 70
    config.gain_control1.analog_controller.enable_digital_adaptive = True
    config.gain_control1.analog_controller.clipped_level_step = 15
    config.gain_control1.analog_controller.clipped_ratio_threshold = 0.1
    config.gain_control1.analog_controller.clipped_wait_frames = 300

    # Clipping predictor
    config.gain_control1.analog_controller.predictor.enabled = False
    config.gain_control1.analog_controller.predictor.predictor_mode = (
        ClippingPredictorMode.CLIPPING_EVENT_PREDICTION
    )
    config.gain_control1.analog_controller.predictor.window_length = 5
    config.gain_control1.analog_controller.predictor.reference_window_length = 5
    config.gain_control1.analog_controller.predictor.reference_window_delay = 5
    config.gain_control1.analog_controller.predictor.clipping_threshold = -1.0
    config.gain_control1.analog_controller.predictor.crest_factor_margin = 3.0
    config.gain_control1.analog_controller.predictor.use_predicted_step = True

    # AGC2
    config.gain_control2.enabled = False
    config.gain_control2.volume_controller.enabled = False
    config.gain_control2.adaptive_controller.enabled = False
    config.gain_control2.adaptive_controller.headroom_db = 5.0
    config.gain_control2.adaptive_controller.max_gain_db = 50.0
    config.gain_control2.adaptive_controller.initial_gain_db = 15.0
    config.gain_control2.adaptive_controller.max_gain_change_db_per_second = 6.0
    config.gain_control2.adaptive_controller.max_output_noise_level_dbfs = -50.0
    config.gain_control2.fixed_controller.gain_db = 0.0
    return config


__all__ = [
    "WebRTCAudioProcessing",
    "Config",
    "create_default_config",
    "DownmixMethod",
    "NoiseSuppressionLevel",
    "GainController1Mode",
    "ClippingPredictorMode",
]
