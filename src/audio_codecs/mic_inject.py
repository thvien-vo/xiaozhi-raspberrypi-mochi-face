"""Test aid: replace the microphone with a WAV file (measurements on the real robot).

Set ``XIAOZHI_MIC_INJECT_FILE=/path/to/file.wav`` (16 kHz, mono, 16-bit) before starting the
app. Every 20 ms microphone frame is then taken from that file instead (looped), after the
device's own format conversion, so the VAD, the encoder and the server see exactly the file.
The file is re-read when it changes on disk (replace it atomically with ``mv``), so silence,
music and speech can be swapped without a restart. While the file is missing, the real
microphone is used. Unset the variable for normal use; the app logs a warning while it is set.
"""

from __future__ import annotations

import os
import wave

import numpy as np

from src.logging import get_logger

logger = get_logger()

ENV = "XIAOZHI_MIC_INJECT_FILE"


class MicInjector:
    def __init__(self, path: str, sample_rate: int = 16000) -> None:
        self.path = path
        self.sample_rate = sample_rate
        self._stamp = None
        self._data: np.ndarray | None = None
        self._pos = 0
        self._checked = 0

    @classmethod
    def from_env(cls, sample_rate: int = 16000) -> MicInjector | None:
        path = os.environ.get(ENV, "").strip()
        if not path:
            return None
        logger.warning(f"{ENV} is set: the microphone is replaced by {path} (test mode)")
        return cls(path, sample_rate)

    def _reload(self) -> None:
        try:
            st = os.stat(self.path)
        except OSError:
            if self._data is not None:
                logger.info("Mic inject: file gone, back to the real microphone")
            self._data, self._stamp = None, None
            return
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        if stamp == self._stamp:
            return
        try:
            with wave.open(self.path, "rb") as w:
                if w.getframerate() != self.sample_rate or w.getnchannels() != 1 or w.getsampwidth() != 2:
                    raise ValueError("need 16 kHz mono 16-bit WAV")
                data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        except Exception as e:
            logger.warning(f"Mic inject: cannot read {self.path}: {e}")
            self._data, self._stamp = None, stamp
            return
        self._data, self._stamp, self._pos = (data if data.size else None), stamp, 0
        logger.info(f"Mic inject: playing {self.path} ({data.size / self.sample_rate:.1f} s, looped)")

    def next_frame(self, real: np.ndarray) -> np.ndarray:
        """The injected frame (same length as the real one), or the real frame."""
        self._checked += 1
        if self._checked % 25 == 1:  # Look at the file twice a second
            self._reload()
        data = self._data
        if data is None:
            return real
        n = len(real)
        out = np.empty(n, dtype=np.float32)
        filled = 0
        while filled < n:
            take = min(n - filled, data.size - self._pos)
            out[filled:filled + take] = data[self._pos:self._pos + take]
            filled += take
            self._pos = (self._pos + take) % data.size
        return out.reshape(np.shape(real))
