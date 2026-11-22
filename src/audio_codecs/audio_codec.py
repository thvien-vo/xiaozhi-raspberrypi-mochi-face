import asyncio
import threading
import time
from collections.abc import Callable
from typing import Protocol

import numpy as np

from src.audio_codecs.audio_buffer import PcmFifo
from src.audio_codecs.audio_converter import AudioConverter
from src.audio_codecs.opus_codec import OpusCodec, parse_opus_toc
from src.audio_codecs.stream_manager import AudioStreamManager
from src.constants.constants import AudioConfig
from src.logging import get_logger
from src.utils.audio_device import AudioDeviceManager, DeviceConfig
from src.utils.config_manager import get_config

logger = get_logger()

# Mixing gain (ducking) for music when TTS is present, and its hold duration (in 20ms chunks)
_MUSIC_DUCK_GAIN = 0.35
_DUCK_HOLD_CHUNKS = 10
# How fast music comes back after ducking (gain per 20ms chunk); going down is immediate
_MUSIC_RELEASE_STEP = 0.15
# Music writer watermark backpressure target (seconds): smaller makes pause/barge-in more responsive, larger improves jitter tolerance
_MUSIC_BACKLOG_TARGET_S = 0.30
# TTS / notification / music FIFO capacity (seconds), drops oldest when exceeded
_TTS_FIFO_MAX_S = 10.0
_MUSIC_FIFO_MAX_S = 2.0
_NOTIFICATION_FIFO_MAX_S = 2.0
_NOTIFICATION_BACKLOG_TARGET_S = 0.30


class AudioListener(Protocol):
    """Audio listener protocol"""

    def on_audio_data(self, audio_data: np.ndarray) -> None:
        """Receive audio data

        Args:
            audio_data: float32 audio data
        """
        ...


class AudioCodec:
    """Audio codec - coordinator pattern

    Composes components and coordinates data flow

    Data flow:
    - Input: Device (float32) -> Downmix + Resample (float32) -> Opus Encode (float32 -> bytes) -> Network
    - Output: Network -> Opus Decode (bytes -> float32) -> Resample + Upmix (float32) -> Device (float32)
    """

    def __init__(self):
        """Initialize audio codec"""
        # Refresh protocol config (supports applying changes after Settings UI modifications)
        AudioConfig.reload()

        # Components (dependency injection)
        self.device_manager = AudioDeviceManager(get_config())
        self.opus_codec = OpusCodec(
            input_sample_rate=AudioConfig.INPUT_SAMPLE_RATE,
            output_sample_rate=AudioConfig.OUTPUT_SAMPLE_RATE,
            channels=AudioConfig.CHANNELS,
        )
        self.converter = AudioConverter()
        self.stream_manager = None

        # TTS / notification / music stream splitting: notification and TTS are both voice priority, music ducks when either is present.
        self._tts_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _TTS_FIFO_MAX_S)
        )
        self._music_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _MUSIC_FIFO_MAX_S)
        )
        self._notification_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _NOTIFICATION_FIFO_MAX_S)
        )
        self._mix_chunk = int(AudioConfig.OUTPUT_SAMPLE_RATE * 0.02)  # 20ms
        self._duck_hold = 0
        self._music_gain = 1.0  # Music volume
        self._music_ducked = False  # Held down for the whole time the assistant speaks
        self._music_level = 1.0  # Gain applied to the last chunk
        self._last_output_callback = 0.0
        self._notification_had_audio = False
        self._notification_drain_callbacks_remaining = 0
        self._notification_progress_callback: Callable[[int], None] | None = None
        self._notification_drained_callback: Callable[[], None] | None = None

        # Listeners (thread-safe)
        self._encoded_callback: Callable | None = None
        self._audio_listeners: list[AudioListener] = []
        self._listeners_lock = threading.Lock()

        # Device configuration (populated after initialization)
        self.device_config: DeviceConfig | None = None

        # AEC (Self far: uses final PCM from playback callback as reference), created per configuration during initialize
        self._aec = None

        # State flags
        self._is_closing = False
        self._closed = False
        self._server_opus_logged = False
        self._last_output_status_log = 0.0

    async def initialize(self):
        """Initialize all components

        Process:
        1. Load/detect devices
        2. Refresh protocol configuration + initialize Opus
        3. Configure format conversion pipeline
        4. Create audio streams
        5. Start audio streams
        """
        try:
            # 1. Load/detect devices
            self.device_config = self.device_manager.load_or_detect_devices()

            # 2. Refresh protocol configuration and initialize Opus
            AudioConfig.reload()
            self.opus_codec.close()
            self.opus_codec = OpusCodec(
                input_sample_rate=AudioConfig.INPUT_SAMPLE_RATE,
                output_sample_rate=AudioConfig.OUTPUT_SAMPLE_RATE,
                channels=AudioConfig.CHANNELS,
            )
            self.opus_codec.initialize()

            # 3. Configure format conversion pipeline
            self._configure_pipeline()

            # 4. Create AEC per configuration (Self far), automatically bypassed on failure
            self._setup_aec()

            # 5. Create audio streams
            self.stream_manager = AudioStreamManager(self.device_config)
            self.stream_manager.create_streams(
                input_callback=self._input_callback,
                output_callback=self._output_callback,
            )

            # 6. Start audio streams
            self.stream_manager.start()

            logger.info("AudioCodec initialization completed")

        except Exception as e:
            logger.error(f"Failed to initialize audio device: {e}", exc_info=True)
            await self.close()
            raise

    def _input_callback(self, indata, frames, time_info, status):
        """Input callback: Device -> Encode -> Send

        Data flow: Multi-channel/High sample rate -> Downmix -> Resample -> Opus Encode -> Network

        Args:
            indata: float32 audio data, shape (frames, channels)
            frames: Number of frames
            time_info: Time info
            status: Status flags
        """
        if status and "overflow" not in str(status).lower():
            logger.warning(f"Input stream status: {status}")

        if self._is_closing:
            return

        try:
            # 1. Format conversion (downmix + resample)
            # Preserve indata (frames, channels) shape so downmix_to_mono downmixes properly
            audio_converted = self.converter.convert_input(
                indata, AudioConfig.INPUT_FRAME_SIZE
            )
            if audio_converted is None:
                return  # Insufficient data, wait for next frame

            # 1.5 AEC: cancel echo using far reference extracted from playback callback (returns as-is when bypassed)
            if self._aec is not None and self._aec.active:
                audio_converted = self._aec.process_near(audio_converted)

            # 2. Opus encoding (float32 input)
            if self._encoded_callback:
                try:
                    opus_data = self.opus_codec.encode(
                        audio_converted, AudioConfig.INPUT_FRAME_SIZE
                    )
                    self._encoded_callback(opus_data)
                except Exception as e:
                    logger.warning(f"Encoding failed: {e}", exc_info=True)

            # 3. Notify listeners (thread-safe)
            with self._listeners_lock:
                for listener in self._audio_listeners:
                    try:
                        listener.on_audio_data(audio_converted.copy())
                    except Exception as e:
                        logger.warning(f"Listener processing failed: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Input callback error: {e}", exc_info=True)

    def _output_callback(self, outdata, frames, time_info, status):
        """Output callback: Decode -> Convert -> Play

        Data flow: Queue -> Resample -> Upmix -> Device

        Loops pulling chunks from the queue into convert_output until the resampler's
        internal buffer accumulates enough frames or the queue is exhausted. Solves
        stuttering when sample rates are not evenly divisible (e.g., 16kHz -> 44100Hz)
        or when server frame durations do not match.

        Args:
            outdata: float32 output buffer, shape (frames, channels)
            frames: Number of frames
            time_info: Time info
            status: Status flags
        """
        self._last_output_callback = time.monotonic()
        if status:
            # Rate limit: logging (file I/O) inside callback exacerbates underflow, max 1 log per 2 seconds
            now = time.monotonic()
            if now - self._last_output_status_log > 2.0:
                self._last_output_status_log = now
                logger.warning(f"Output stream status: {status}")

        try:
            audio_converted = None

            while audio_converted is None:
                audio_data = self._pull_mixed(self._mix_chunk)
                if audio_data is None:
                    break
                audio_converted = self.converter.convert_output(audio_data, frames)

            if audio_converted is None:
                audio_converted = self.converter.drain_output_buffer(frames)

            if audio_converted is None or len(audio_converted) < frames:
                outdata.fill(0.0)
                if audio_converted is not None and len(audio_converted) > 0:
                    outdata[: len(audio_converted)] = audio_converted
            else:
                outdata[:] = audio_converted[:frames]

            # AEC far: final PCM actually written to device (TTS + music mix, including silence to maintain continuity)
            if self._aec is not None and self._aec.active:
                self._aec.feed_far(outdata)

            self._notify_notification_drained_after_output()

        except Exception as e:
            logger.error(f"Output callback error: {e}", exc_info=True)
            outdata.fill(0.0)

    def _configure_pipeline(self):
        """Configure format conversion pipeline (Device <-> Protocol).

        Sets up input/output conversion chains based on native device parameters and protocol requirements.
        Input: Device (f32, device_rate, device_ch) -> Protocol (f32, 16kHz, 1ch)
        Output: Protocol (f32, opus_out_rate, 1ch) -> Device (f32, device_rate, device_ch)
        """
        self.converter.setup_input_converter(
            from_rate=self.device_config.input_sample_rate,
            to_rate=AudioConfig.INPUT_SAMPLE_RATE,
            from_channels=self.device_config.input_channels,
            to_channels=1,
        )
        self.converter.setup_output_converter(
            from_rate=AudioConfig.OUTPUT_SAMPLE_RATE,
            to_rate=self.device_config.output_sample_rate,
            from_channels=1,
            to_channels=self.device_config.output_channels,
        )

        # Protocol output sample rate may change on config hot-reload; rebuild FIFOs and mix chunk accordingly
        # (Audio streams are stopped at this point, no concurrent reads)
        self._tts_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _TTS_FIFO_MAX_S)
        )
        self._music_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _MUSIC_FIFO_MAX_S)
        )
        self._notification_fifo = PcmFifo(
            int(AudioConfig.OUTPUT_SAMPLE_RATE * _NOTIFICATION_FIFO_MAX_S)
        )
        self._mix_chunk = int(AudioConfig.OUTPUT_SAMPLE_RATE * 0.02)
        self._duck_hold = 0
        self._notification_had_audio = False
        self._notification_drain_callbacks_remaining = 0

    def _pull_mixed(self, n: int) -> np.ndarray | None:
        """Output callback thread: pull n samples from voice/notification/music FIFOs and mix.

        Rules:
        - All three empty -> None (caller enters silence/underflow path)
        - When TTS or notification is present, music ducks by _MUSIC_DUCK_GAIN and holds
          for several chunks across voice frame gaps (prevents ducking gain flutter);
          set_music_ducked() holds it for a whole reply. It then comes back in a short ramp
        """
        tts = self._tts_fifo.pull(n)
        notification, notification_samples = self._notification_fifo.pull_with_count(n)
        music = self._music_fifo.pull(n)

        if notification is not None:
            self._notification_had_audio = True
            self._notification_drain_callbacks_remaining = 0
            callback = self._notification_progress_callback
            if callback and notification_samples:
                try:
                    callback(notification_samples)
                except Exception as e:
                    logger.debug(f"Notification playback progress callback failed: {e}", exc_info=True)
        elif self._notification_had_audio:
            self._notification_had_audio = False
            # The converter may still hold resampled output from the last notification chunk;
            # wait for two device callbacks before notifying completion to ensure the final PCM chunk is written to the sound card.
            self._notification_drain_callbacks_remaining = 2

        if tts is None and notification is None and music is None:
            return None

        if tts is not None or notification is not None:
            self._duck_hold = _DUCK_HOLD_CHUNKS
        elif self._duck_hold > 0:
            self._duck_hold -= 1

        voice = tts
        if notification is not None:
            if voice is None:
                voice = notification
            else:
                voice = np.clip(voice + notification, -1.0, 1.0)

        if music is None:
            return voice
        ducked = voice is not None or self._duck_hold > 0 or self._music_ducked
        music = self._apply_music_level(music, ducked)
        if voice is None:
            return music
        return np.clip(voice + music, -1.0, 1.0)

    def _apply_music_level(self, music: np.ndarray, ducked: bool) -> np.ndarray:
        """Scale a music chunk by volume and ducking (output callback thread)."""
        before = self._music_level
        target = self._music_gain * (_MUSIC_DUCK_GAIN if ducked else 1.0)
        self._music_level = min(target, before + _MUSIC_RELEASE_STEP)
        if self._music_level > before:
            return music * np.linspace(before, self._music_level, len(music), dtype=np.float32)
        return music * np.float32(self._music_level)

    def _notify_notification_drained_after_output(self) -> None:
        """Report that the notification queue is truly drained after device output."""
        if self._notification_drain_callbacks_remaining <= 0:
            return
        self._notification_drain_callbacks_remaining -= 1
        if self._notification_drain_callbacks_remaining > 0:
            return
        callback = self._notification_drained_callback
        if callback:
            try:
                callback()
            except Exception as e:
                logger.debug(f"Notification playback completion callback failed: {e}", exc_info=True)

    def _setup_aec(self):
        """Create/rebuild AEC engine (Self far) based on AEC_OPTIONS.ENABLED.

        Far sample rate may change after device hot-reload, so it must be rebuilt with device_config;
        creation failure does not raise, and the engine bypasses itself (active=False).
        """
        if self._aec is not None:
            self._aec.close()
            self._aec = None

        try:
            config = get_config()
            if not bool(config.get_config("AEC_OPTIONS.ENABLED", False)):
                logger.info("AEC is disabled (AEC_OPTIONS.ENABLED=false)")
                return

            from src.audio_processing.aec_engine import AecEngine

            frame_delay = config.get_config("AEC_OPTIONS.FRAME_DELAY", 3)
            self._aec = AecEngine(
                near_rate=AudioConfig.INPUT_SAMPLE_RATE,
                far_rate=self.device_config.output_sample_rate,
                # FRAME_DELAY is in protocol frames; convert to ms and add base output latency
                delay_ms=40 + int(frame_delay) * AudioConfig.FRAME_DURATION,
                enable_preprocess=bool(
                    config.get_config("AEC_OPTIONS.ENABLE_PREPROCESS", True)
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to create AEC engine, bypassed: {e}", exc_info=True)
            self._aec = None

    # === Music ===

    def set_music_gain(self, gain: float) -> None:
        """Music volume as a linear factor (0..1)."""
        self._music_gain = min(1.0, max(0.0, float(gain)))

    def set_music_ducked(self, ducked: bool) -> None:
        """Hold music low for as long as the assistant speaks (gaps between sentences too)."""
        self._music_ducked = bool(ducked)

    @property
    def music_backlog_samples(self) -> int:
        """Music written but not yet handed to the sound card."""
        return self._music_fifo.size

    @property
    def output_running(self) -> bool:
        """The output stream is open and the sound card keeps asking for audio."""
        stream = getattr(self.stream_manager, "output_stream", None)
        return stream is not None and time.monotonic() - self._last_output_callback < 2.0

    @property
    def output_device_name(self) -> str:
        if self.device_config is None:
            return ""
        try:
            import sounddevice as sd

            return str(sd.query_devices(self.device_config.output_device_id)["name"])
        except Exception:
            return ""

    # === Public interface (backward compatible) ===

    @property
    def aec_active(self) -> bool:
        """Whether the AEC engine is present and active (depended on by music parallel playback policy, etc.)."""
        aec = self._aec
        return bool(aec is not None and aec.active)

    def set_encoded_callback(self, callback: Callable[[bytes], None]):
        """Set encoded audio callback

        Args:
            callback: Callback function receiving Opus encoded data
        """
        self._encoded_callback = callback
        if callback:
            logger.info("Encoded audio callback set")
        else:
            logger.info("Encoded audio callback cleared")

    def add_audio_listener(self, listener: AudioListener):
        """Add audio listener (thread-safe)

        Args:
            listener: Listener object implementing the AudioListener protocol
        """
        with self._listeners_lock:
            if listener not in self._audio_listeners:
                self._audio_listeners.append(listener)
                logger.info(f"Added audio listener: {listener.__class__.__name__}")

    def remove_audio_listener(self, listener: AudioListener):
        """Remove audio listener (thread-safe)

        Args:
            listener: Listener object to remove
        """
        with self._listeners_lock:
            if listener in self._audio_listeners:
                self._audio_listeners.remove(listener)
                logger.info(f"Removed audio listener: {listener.__class__.__name__}")

    async def write_audio(self, opus_data: bytes):
        """Decode and play audio (Opus -> Speaker)

        Automatically detects frame duration from the Opus TOC byte without relying on client configuration.

        Args:
            opus_data: Opus encoded data
        """
        try:
            toc_info = parse_opus_toc(opus_data)
            if toc_info is None:
                return

            if not self._server_opus_logged:
                self._server_opus_logged = True
                logger.info(
                    f"Server Opus parameters: "
                    f"{toc_info['mode']} {toc_info['bandwidth_hz']} | "
                    f"frame duration {toc_info['duration_ms']}ms "
                    f"({toc_info['frame_ms']}ms x {toc_info['num_frames']})"
                )

            frame_size = int(
                AudioConfig.OUTPUT_SAMPLE_RATE * toc_info["duration_ms"] / 1000
            )
            audio_float32 = self.opus_codec.decode(opus_data, frame_size)

            self._tts_fifo.push(audio_float32)

        except Exception as e:
            logger.warning(f"Failed to write audio: {e}", exc_info=True)

    async def write_pcm_direct(self, pcm_float32: np.ndarray):
        """Write music PCM (float32, used by MusicPlayer) with watermark backpressure.

        Waits for playback consumption if the music buffer exceeds the target watermark after writing --
        this is the sole pacing source for the music pipeline (decoder clock drifts after pause and cannot be relied upon).
        Backpressure caps at 2 seconds as a fallback, and FIFO capacity drops the oldest samples to prevent unbounded growth.
        """
        self._music_fifo.push(pcm_float32)

        target = int(AudioConfig.OUTPUT_SAMPLE_RATE * _MUSIC_BACKLOG_TARGET_S)
        for _ in range(100):
            if self._is_closing or self._music_fifo.size <= target:
                break
            await asyncio.sleep(0.02)

    def set_notification_playback_callbacks(
        self,
        progress_callback: Callable[[int], None] | None = None,
        drained_callback: Callable[[], None] | None = None,
    ) -> None:
        """Set notification playback callbacks (called from audio thread; callbacks must forward in a thread-safe manner)."""
        self._notification_progress_callback = progress_callback
        self._notification_drained_callback = drained_callback
        self._notification_had_audio = False
        self._notification_drain_callbacks_remaining = 0

    async def write_notification_pcm(self, pcm_float32: np.ndarray):
        """Write notification PCM and provide decoding backpressure based on notification queue watermark."""
        self._notification_fifo.push(pcm_float32)

        target = int(
            AudioConfig.OUTPUT_SAMPLE_RATE * _NOTIFICATION_BACKLOG_TARGET_S
        )
        for _ in range(100):
            if self._is_closing or self._notification_fifo.size <= target:
                break
            await asyncio.sleep(0.02)

    async def clear_audio_queue(self):
        """Clear TTS playback queue (used on interruption/abort; music queue is unaffected)."""
        self._server_opus_logged = False
        self.converter.clear_output_buffer()
        count = self._tts_fifo.clear()
        if count > 0:
            logger.info(f"Cleared TTS queue, discarded {count} samples")

    async def clear_music_queue(self):
        """Clear music playback queue (used on stop/seek; TTS queue is unaffected)."""
        count = self._music_fifo.clear()
        if count > 0:
            logger.info(f"Cleared music queue, discarded {count} samples")

    async def clear_notification_queue(self):
        """Clear notification playback queue (used when cancelling notifications; TTS/music unaffected)."""
        count = self._notification_fifo.clear()
        self._notification_had_audio = False
        self._notification_drain_callbacks_remaining = 0
        if count > 0:
            logger.info(f"Cleared notification queue, discarded {count} samples")

    async def reinitialize_stream(self, is_input: bool = True):
        """Reinitialize audio stream (supports hot-plugging)

        Args:
            is_input: True=input stream, False=output stream

        Returns:
            bool: Whether reinitialization succeeded
        """
        if not self.stream_manager:
            return False

        if is_input:
            return self.stream_manager.reinitialize_stream(
                is_input=True, input_callback=self._input_callback
            )
        else:
            return self.stream_manager.reinitialize_stream(
                is_input=False, output_callback=self._output_callback
            )

    def stop_streams_for_enumeration(self) -> None:
        """Stop sounddevice streams held by this codec before hot-plug enumeration.

        Must call ``reload_devices()`` or ``create_streams`` afterwards, otherwise capture/playback will remain stopped.
        """
        if self.stream_manager:
            self.stream_manager.stop()
            logger.info("AudioCodec: stopped audio streams (for device enumeration)")

    async def reload_devices(self, *, reenumerate: bool = True):
        """Hot-reload audio device configuration

        Process:
        1. Stop current audio streams
        2. (Optional) Reinitialize PortAudio and re-enumerate (helps detect newly connected Bluetooth devices)
        3. Reload device configuration + protocol configuration
        4. Rebuild format converter + Opus codec
        5. Recreate and start audio streams

        Args:
            reenumerate: Whether to force refresh the PortAudio device table after stopping streams

        Returns:
            bool: Whether reload succeeded
        """
        logger.info("AudioCodec: starting audio device hot-reload...")

        try:
            # 1. Stop current streams (must happen before PortAudio reinit)
            if self.stream_manager:
                self.stream_manager.stop()
                logger.debug("AudioCodec: stopped current audio streams")

            # 2. Hot-plug: rebuild PortAudio context before matching by name
            if reenumerate:
                from src.utils.audio_utils import refresh_portaudio_devices

                refresh_portaudio_devices(reinitialize=True)

            # 3. Reload device configuration
            self.device_manager.config.reload_config()
            self.device_config = self.device_manager.load_or_detect_devices()
            logger.info(
                "AudioCodec: new device configuration - Input ID: "
                f"{self.device_config.input_device_id}, Output ID: "
                f"{self.device_config.output_device_id}"
            )

            # 4. Refresh protocol configuration and rebuild Opus codec
            AudioConfig.reload()
            self.opus_codec.close()
            self.opus_codec = OpusCodec(
                input_sample_rate=AudioConfig.INPUT_SAMPLE_RATE,
                output_sample_rate=AudioConfig.OUTPUT_SAMPLE_RATE,
                channels=AudioConfig.CHANNELS,
            )
            self.opus_codec.initialize()

            # 5. Rebuild format converter
            self.converter.clear_buffers()
            self._configure_pipeline()

            # 5.5 Rebuild AEC (far sample rate follows new output device, filter state reset)
            self._setup_aec()

            # 6. Recreate audio streams
            self.stream_manager = AudioStreamManager(self.device_config)
            self.stream_manager.create_streams(
                input_callback=self._input_callback,
                output_callback=self._output_callback,
            )

            # 7. Start audio streams
            self.stream_manager.start()

            logger.info("AudioCodec: audio device hot-reload completed")
            return True

        except Exception as e:
            logger.error(f"AudioCodec: audio device hot-reload failed: {e}", exc_info=True)
            return False

    async def close(self):
        """Close audio codec"""
        self._is_closing = True

        try:
            # 1. Stop audio streams
            if self.stream_manager:
                self.stream_manager.stop()

            # 2. Clear queues
            await self.clear_audio_queue()
            await self.clear_music_queue()
            await self.clear_notification_queue()

            # 2.5 Release AEC (must be after streams are stopped)
            if self._aec is not None:
                self._aec.close()
                self._aec = None

            # 3. Release converter (including soxr resamplers)
            self.converter.close()

            # 4. Release Opus
            self.opus_codec.close()

            # 5. Clear listeners
            with self._listeners_lock:
                self._audio_listeners.clear()

            logger.info("AudioCodec closed")
            self._closed = True

        except Exception as e:
            logger.error(f"Failed to close audio codec: {e}", exc_info=True)
        finally:
            self._is_closing = False

    def __del__(self):
        """Destructor - perform synchronous cleanup"""
        # Skip if already closed or currently closing
        if getattr(self, "_closed", False) or getattr(self, "_is_closing", False):
            return

        logger.warning("AudioCodec was not closed properly, performing emergency cleanup (recommend using async close())")

        try:
            # 1. Stop audio streams (synchronous)
            if self.stream_manager:
                self.stream_manager.stop()

            # 2. Clear queues (synchronous version)
            count = (
                self._tts_fifo.clear()
                + self._music_fifo.clear()
                + self._notification_fifo.clear()
            )
            if count > 0:
                logger.debug(f"Destructor cleared {count} audio samples")

            # 3. Release converter (synchronous, including soxr resamplers)
            if self.converter:
                self.converter.close()

            # 4. Release Opus (synchronous)
            if self.opus_codec:
                self.opus_codec.close()

            # 5. Clear listeners (synchronous)
            try:
                with self._listeners_lock:
                    self._audio_listeners.clear()
            except Exception as e:
                logger.warning(
                    f"Failed to clear audio listeners (lock may be corrupted): {e}", exc_info=True
                )

            logger.debug("AudioCodec destructor cleanup completed")

        except Exception as e:
            logger.error(f"Destructor cleanup failed: {e}", exc_info=True)
