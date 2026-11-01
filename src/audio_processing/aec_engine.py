"""AEC engine: real-time wrapper for libs/webrtc_apm (P0 Self far).

Data flow:
- near: captured frame (16kHz mono float32) -> ProcessStream -> echo-cancelled uplink
- far: final PCM actually written to device (TTS + music mix, device sample rate/channels)
       -> downmix -> resample to 16kHz -> ProcessReverseStream

Threading model (critical):
- The output callback thread only performs "downmix + copy into queue" (feed_far, microsecond-level, lock-free);
  resampling and APM calls all occur on the capture thread (draining the far queue first at the start of process_near).
  No lock is shared between the two real-time callbacks, preventing the output callback from stalling if the input side holds a lock while blocked by the GIL.

Design constraints:
- Any library loading or processing failure is bypassed (active=False) without affecting the main call pipeline
- WebRTC APM processes 10ms frames; protocol frames of 20/40/60ms are all integer multiples of 10ms
"""

import ctypes
import sys
import threading
from collections import deque

import numpy as np

from src.logging import get_logger

logger = get_logger()

# Automatically bypass after this many consecutive processing failures to prevent a broken library from stalling audio callbacks
_MAX_CONSECUTIVE_FAILURES = 5
# Maximum pending far queue size (number of output callback blocks, ~20ms/block -> 0.5s);
# prevents accumulation when the capture thread is stopped, dropping oldest when exceeded
_FAR_PENDING_MAX_BLOCKS = 25


def _import_webrtc_apm():
    """Import libs.webrtc_apm, with path fallback for both source and packaged runs."""
    try:
        from libs import webrtc_apm

        return webrtc_apm
    except ImportError:
        from src.utils.resource_finder import get_app_root

        root = str(get_app_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        from libs import webrtc_apm

        return webrtc_apm


class AecEngine:
    """WebRTC APM wrapper: AEC + optional NS/high-pass filter, dual near/far streams."""

    def __init__(
        self,
        near_rate: int = 16000,
        far_rate: int = 48000,
        delay_ms: int = 60,
        enable_preprocess: bool = True,
    ):
        """Initialize and load APM; sets active=False (bypassed) on failure.

        Args:
            near_rate: Capture protocol sample rate (16kHz)
            far_rate: Device output sample rate (far-side resampling source)
            delay_ms: Estimated delay from playback to capture
            enable_preprocess: Whether to also enable high-pass filter + noise suppression
        """
        self._near_rate = int(near_rate)
        self._far_rate = int(far_rate)
        self._delay_ms = int(delay_ms)
        self._frame = self._near_rate // 100  # 10ms
        self._lock = threading.Lock()
        self._active = False
        self._closed = False
        self._fail_count = 0
        self._near_misaligned_logged = False

        self._apm = None
        self._stream_cfg = None
        # Two-stage far buffer: pending is written by output callback (copy only),
        # buffer holds residual 16k samples after resampling on the capture thread
        self._far_pending: deque = deque()
        self._far_buffer = np.empty(0, dtype=np.float32)
        self._far_resampler = None
        self._far_dropped = 0

        # Reusable ctypes frame buffers (10ms int16)
        self._near_in = (ctypes.c_short * self._frame)()
        self._near_out = (ctypes.c_short * self._frame)()
        self._far_in = (ctypes.c_short * self._frame)()
        self._far_out = (ctypes.c_short * self._frame)()

        try:
            self._init_apm(enable_preprocess)
            if self._far_rate != self._near_rate:
                import soxr

                self._far_resampler = soxr.ResampleStream(
                    self._far_rate,
                    self._near_rate,
                    num_channels=1,
                    dtype="float32",
                    quality="QQ",
                )
            self._active = True
            logger.info(
                f"AEC engine enabled | near {self._near_rate}Hz, "
                f"far {self._far_rate}Hz->{self._near_rate}Hz, "
                f"delay {self._delay_ms}ms, preprocess={enable_preprocess}"
            )
        except Exception as e:
            logger.warning(f"AEC engine initialization failed, bypassed: {e}")
            self._release()

    def _init_apm(self, enable_preprocess: bool) -> None:
        """Load dynamic library, apply configuration, and create stream config."""
        apm_mod = _import_webrtc_apm()

        self._apm = apm_mod.WebRTCAudioProcessing()

        config = apm_mod.create_default_config()
        config.echo.enabled = True
        config.echo.mobile_mode = False
        if enable_preprocess:
            config.high_pass.enabled = True
            config.noise_suppress.enabled = True
            config.noise_suppress.noise_level = apm_mod.NoiseSuppressionLevel.MODERATE

        ret = self._apm.apply_config(config)
        if ret != 0:
            raise RuntimeError(f"apply_config returned {ret}")

        # Both near and far are 16kHz mono, sharing a single stream config
        self._stream_cfg = self._apm.create_stream_config(self._near_rate, 1)
        self._apm.set_stream_delay_ms(self._delay_ms)

    @property
    def active(self) -> bool:
        return self._active

    def process_near(self, block: np.ndarray) -> np.ndarray:
        """Process captured frame (16kHz mono float32) and return echo-cancelled data of the same length.

        Returns original data on any failure; automatically bypasses after consecutive failures.
        """
        if not self._active:
            return block

        n = block.shape[0]
        if n % self._frame != 0:
            if not self._near_misaligned_logged:
                self._near_misaligned_logged = True
                logger.warning(f"Capture frame length {n} is not an integer multiple of 10ms, AEC bypassing this path")
            return block

        try:
            i16 = self._float_to_i16(block)
            out = np.empty(n, dtype=np.float32)

            with self._lock:
                if not self._active:
                    return block
                # far precedes near: drain reference data accumulated by output callback to maintain causality
                self._drain_far_locked()
                self._apm.set_stream_delay_ms(self._delay_ms)
                for off in range(0, n, self._frame):
                    ctypes.memmove(
                        self._near_in,
                        i16[off : off + self._frame].ctypes.data,
                        self._frame * 2,
                    )
                    ret = self._apm.process_stream(
                        self._near_in, self._stream_cfg, self._stream_cfg, self._near_out
                    )
                    if ret != 0:
                        raise RuntimeError(f"process_stream returned {ret}")
                    out[off : off + self._frame] = (
                        np.frombuffer(self._near_out, dtype=np.int16).astype(np.float32)
                        / 32768.0
                    )

            self._fail_count = 0
            return out
        except Exception as e:
            self._on_failure("near", e)
            return block

    def feed_far(self, outdata: np.ndarray) -> None:
        """Called from output callback thread: only downmix + copy into queue, no resampling/APM (returns in microseconds).

        Silent frames should also be fed to keep the far stream continuous; heavy processing is done by process_near on the capture thread.
        """
        if not self._active:
            return

        try:
            if outdata.ndim > 1 and outdata.shape[1] > 1:
                mono = outdata.mean(axis=1, dtype=np.float32)
            else:
                # PortAudio reuses outdata memory, must copy
                mono = np.array(outdata, dtype=np.float32).ravel()

            self._far_pending.append(mono)
            # deque operations are atomic under GIL; drop oldest when exceeded (prevents accumulation when capture thread stops)
            while len(self._far_pending) > _FAR_PENDING_MAX_BLOCKS:
                self._far_pending.popleft()
                self._far_dropped += 1
        except Exception:
            pass  # Never raise on the output path

    def _drain_far_locked(self) -> None:
        """Capture thread (holding lock): resample far queue and feed into ProcessReverseStream."""
        while self._far_pending:
            mono = self._far_pending.popleft()
            if self._far_resampler is not None:
                mono = self._far_resampler.resample_chunk(mono, last=False)
            if len(mono):
                self._far_buffer = np.concatenate((self._far_buffer, mono))

        n_frames = len(self._far_buffer) // self._frame
        if n_frames == 0:
            return

        usable = n_frames * self._frame
        i16 = self._float_to_i16(self._far_buffer[:usable])
        self._far_buffer = self._far_buffer[usable:]

        for off in range(0, usable, self._frame):
            ctypes.memmove(
                self._far_in, i16[off : off + self._frame].ctypes.data, self._frame * 2
            )
            ret = self._apm.process_reverse_stream(
                self._far_in, self._stream_cfg, self._stream_cfg, self._far_out
            )
            if ret != 0:
                raise RuntimeError(f"process_reverse_stream returned {ret}")

    def set_delay_ms(self, delay_ms: int) -> None:
        self._delay_ms = int(delay_ms)

    def close(self) -> None:
        """Release APM resources; must be called after audio streams are stopped."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            self._active = False
            self._release()
        logger.info("AEC engine closed")

    def _release(self) -> None:
        self._active = False
        try:
            if self._apm is not None and self._stream_cfg is not None:
                self._apm.destroy_stream_config(self._stream_cfg)
        except Exception:
            pass
        self._stream_cfg = None
        self._apm = None
        self._far_resampler = None
        self._far_pending.clear()
        self._far_buffer = np.empty(0, dtype=np.float32)
        if self._far_dropped:
            logger.debug(f"AEC far dropped {self._far_dropped} blocks in total")

    def _on_failure(self, side: str, err: Exception) -> None:
        self._fail_count += 1
        if self._fail_count >= _MAX_CONSECUTIVE_FAILURES:
            logger.error(
                f"AEC {side} failed {self._fail_count} consecutive times, automatically bypassing: {err}",
                exc_info=True,
            )
            with self._lock:
                self._release()
        else:
            logger.debug(f"AEC {side} processing failed ({self._fail_count}): {err}")

    @staticmethod
    def _float_to_i16(x: np.ndarray) -> np.ndarray:
        return np.clip(x * 32768.0, -32768.0, 32767.0).astype(np.int16)
