"""Sample-level PCM buffer.

Thread-safe FIFO used for mixing in the output callback.
"""

import threading
from collections import deque

import numpy as np


class PcmFifo:
    """Sample-level PCM FIFO (float32 mono, thread-safe).

    The writer runs in the asyncio thread, and the reader runs in the audio output callback thread;
    used for mixing in the output callback after splitting TTS and music streams.

    - push: drops the oldest samples when capacity is exceeded (recorded in dropped)
    - pull: returns a fixed-length chunk, zero-padding if data is insufficient; returns None if completely empty
    """

    def __init__(self, max_samples: int):
        self._chunks: deque = deque()
        self._offset = 0  # Number of samples consumed in the first chunk
        self._size = 0  # Total number of readable samples
        self._max = int(max_samples)
        self._lock = threading.Lock()
        self.dropped = 0

    @property
    def size(self) -> int:
        return self._size

    def push(self, pcm: np.ndarray) -> None:
        """Append float32 mono data; drop the oldest samples when capacity is exceeded."""
        if pcm.ndim > 1:
            pcm = pcm.reshape(-1)
        if pcm.dtype != np.float32:
            pcm = pcm.astype(np.float32)
        with self._lock:
            self._chunks.append(pcm)
            self._size += len(pcm)
            while self._size > self._max and self._chunks:
                head = self._chunks[0]
                remain = len(head) - self._offset
                drop = min(remain, self._size - self._max)
                self._offset += drop
                self._size -= drop
                self.dropped += drop
                if self._offset >= len(head):
                    self._chunks.popleft()
                    self._offset = 0

    def pull(self, n: int) -> np.ndarray | None:
        """Pull n samples; return None if empty, zero-pad if insufficient."""
        data, _ = self.pull_with_count(n)
        return data

    def pull_with_count(self, n: int) -> tuple[np.ndarray | None, int]:
        """Pull n samples and return the actual number of samples read.

        ``pull`` is retained for backward compatibility. When playback requires accurate
        tracking of notification audio consumption progress, use this method to avoid
        counting trailing zero-padded silence toward media time.
        """
        with self._lock:
            if self._size == 0:
                return None, 0
            out = np.zeros(n, dtype=np.float32)
            filled = 0
            while filled < n and self._chunks:
                head = self._chunks[0]
                avail = len(head) - self._offset
                take = min(avail, n - filled)
                out[filled : filled + take] = head[
                    self._offset : self._offset + take
                ]
                filled += take
                self._offset += take
                self._size -= take
                if self._offset >= len(head):
                    self._chunks.popleft()
                    self._offset = 0
            return out, filled

    def clear(self) -> int:
        """Clear the buffer and return the number of discarded samples."""
        with self._lock:
            count = self._size
            self._chunks.clear()
            self._offset = 0
            self._size = 0
            return count
