"""Activation verification code announcement module.

Announces the activation verification code using pre-recorded WAV sound effects, used only during the device activation process.
Does not depend on FFmpeg and works in clean environments without system FFmpeg.
"""

import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from src.logging import get_logger
from src.utils.resource_finder import get_app_root

logger = get_logger()

# Audio assets directory
_ASSETS_DIR = get_app_root() / "assets" / "sounds"
# Default asset sample rate (aligned with pre-bundled WAVs in assets/sounds)
_DEFAULT_SAMPLE_RATE = 24000


class ActivationAnnouncer:
    """Activation verification code announcer."""

    def __init__(self, locale: str = "zh-CN"):
        self._locale = locale
        self._stop_flag = threading.Event()
        self._play_thread: threading.Thread | None = None

    def _get_sound_path(self, name: str) -> Path | None:
        """Get sound effect file path (WAV only)."""
        sound_file = _ASSETS_DIR / self._locale / f"{name}.wav"
        if sound_file.exists():
            return sound_file
        # Fall back to zh-CN
        if self._locale != "zh-CN":
            fallback = _ASSETS_DIR / "zh-CN" / f"{name}.wav"
            if fallback.exists():
                return fallback
        return None

    def _load_wav(self, file_path: Path) -> tuple[np.ndarray, int] | None:
        """Load WAV as float32 mono and return (samples, sample_rate).

        Args:
            file_path: WAV file path.

        Returns:
            (float32 audio, sample_rate), or None on failure.
        """
        try:
            with wave.open(str(file_path), "rb") as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)

            if sample_width == 2:
                audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            elif sample_width == 4:
                audio = (
                    np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
                )
            elif sample_width == 1:
                # 8-bit PCM is unsigned
                audio = (
                    np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0
                ) / 128.0
            else:
                logger.error(f"Unsupported WAV bit depth: {sample_width * 8} bit ({file_path})")
                return None

            if channels > 1:
                audio = audio.reshape(-1, channels).mean(axis=1)

            return audio, sample_rate
        except Exception as e:
            logger.error(f"Failed to load WAV {file_path}: {e}", exc_info=True)
            return None

    def _play_sounds(self, names: list[str]) -> None:
        """Play sequence of sound effects (executed in worker thread)."""
        for name in names:
            if self._stop_flag.is_set():
                logger.debug("Announcement interrupted")
                break

            sound_path = self._get_sound_path(name)
            if not sound_path:
                logger.warning(f"Sound effect file does not exist: {name}")
                continue

            loaded = self._load_wav(sound_path)
            if loaded is None or self._stop_flag.is_set():
                continue

            audio, sample_rate = loaded
            if sample_rate <= 0:
                sample_rate = _DEFAULT_SAMPLE_RATE

            try:
                sd.play(audio, sample_rate)
                # Wait in small intervals to respond quickly to interruption
                while sd.get_stream().active:
                    if self._stop_flag.is_set():
                        sd.stop()
                        break
                    self._stop_flag.wait(0.05)
            except Exception as e:
                logger.error(f"Playback failed: {e}", exc_info=True)

    def announce(self, code: str) -> None:
        """Announce verification code (non-blocking).

        Args:
            code: Verification code string, e.g. "123456"
        """
        if not code or not code.isdigit():
            logger.warning(f"Invalid verification code: {code}")
            return

        # Stop any previous announcement
        self.stop()

        # Build playback sequence: activation prompt + individual digits
        sounds = ["activation"] + list(code)

        logger.info(f"Announcing verification code: {code}")

        self._stop_flag.clear()
        self._play_thread = threading.Thread(
            target=self._play_sounds,
            args=(sounds,),
            daemon=True,
            name="ActivationAnnouncer",
        )
        self._play_thread.start()

    def stop(self) -> None:
        """Stop announcement."""
        self._stop_flag.set()

        # Stop audio playback
        try:
            sd.stop()
        except Exception as e:
            logger.debug(f"Failed to stop audio playback: {e}")

        # Wait for thread to finish
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=1)

        self._play_thread = None


# Global instance
_announcer: ActivationAnnouncer | None = None


def announce_activation_code(code: str, locale: str = "zh-CN") -> None:
    """Announce activation verification code.

    Args:
        code: Verification code string
        locale: Language/locale code
    """
    global _announcer
    if _announcer is None:
        _announcer = ActivationAnnouncer(locale)
    _announcer.announce(code)


def stop_announcement() -> None:
    """Stop verification code announcement."""
    global _announcer
    if _announcer:
        _announcer.stop()
