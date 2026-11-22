"""Tempo and beat phase of a music file, good enough to dance to.

Onset strength (spectral flux) of the first minute, autocorrelation for the tempo, then a
fine search for the period and phase that line up best with the onsets.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

from src.logging import get_logger

logger = get_logger()

RATE = 11025
HOP = 128
FFT = 512
ANALYZE_SECONDS = 60.0
MIN_BPM, MAX_BPM = 60.0, 190.0
FALLBACK = {"bpm": 100.0, "offset": 0.0, "confidence": 0.0}

_HIDE = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def estimate(samples: np.ndarray, rate: int = RATE) -> dict:
    """{"bpm", "offset" (seconds of the first beat), "confidence" 0..1} of mono samples."""
    x = np.asarray(samples, dtype=np.float32)
    if len(x) < rate * 3 or float(np.abs(x).max(initial=0.0)) < 1e-4:
        return dict(FALLBACK)
    frames = 1 + (len(x) - FFT) // HOP
    window = np.hanning(FFT).astype(np.float32)
    flux = np.zeros(frames, dtype=np.float32)
    previous = None
    for start in range(0, frames, 512):  # In blocks, to keep memory small
        rows = np.arange(start, min(start + 512, frames))
        block = np.stack([x[i * HOP : i * HOP + FFT] for i in rows]) * window
        mag = np.log1p(50.0 * np.abs(np.fft.rfft(block, axis=1)))
        before = mag[:1] if previous is None else previous
        flux[rows] = np.maximum(0.0, np.diff(np.vstack([before, mag]), axis=0)).sum(axis=1)
        previous = mag[-1:]
    fps = rate / HOP
    width = max(3, int(fps * 0.5)) | 1
    env = np.maximum(0.0, flux - np.convolve(flux, np.ones(width) / width, mode="same"))
    if env.std() < 1e-6:
        return dict(FALLBACK)
    env = (env - env.mean()) / env.std()

    n = len(env)
    spectrum = np.fft.rfft(env, 2 * n)
    ac = np.fft.irfft(spectrum * np.conj(spectrum))[:n] / np.arange(n, 0, -1)
    lags = np.arange(int(fps * 60 / MAX_BPM), min(int(fps * 60 / MIN_BPM) + 1, n // 3))
    if len(lags) == 0 or ac[0] <= 0:
        return dict(FALLBACK)
    bpms = 60 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 115.0) / 0.8) ** 2)
    # A neighbourhood of each lag: a period between two frames splits its peak
    smooth = np.convolve(ac, [0.5, 1.0, 0.5], mode="same")
    values = (smooth[lags] + 0.5 * smooth[2 * lags]) * prior
    best = int(np.argmax(values))
    confidence = float(np.clip(ac[lags[best] - 1 : lags[best] + 2].max() / ac[0], 0.0, 1.0))
    if confidence < 0.04:
        return dict(FALLBACK)

    # Refine: the period whose phasor over the whole clip is longest
    t = (np.arange(n) * HOP + FFT / 2) / rate
    around = bpms[best] * np.linspace(0.97, 1.03, 241)
    phasors = np.array([np.sum(env * np.exp(-2j * np.pi * t * b / 60)) for b in around])
    k = int(np.argmax(np.abs(phasors)))
    bpm = float(around[k])
    offset = float((-np.angle(phasors[k]) / (2 * np.pi)) % 1.0) * 60 / bpm
    return {"bpm": round(bpm, 2), "offset": round(offset, 3), "confidence": round(confidence, 3)}


def decode_mono(path: Path, seconds: float = ANALYZE_SECONDS) -> np.ndarray:
    from src.utils.resource_finder import get_ffmpeg_path

    command = [
        get_ffmpeg_path(), "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe", "-threads", "1",
        "-t", f"{seconds:.0f}", "-i", str(path), "-vn", "-sn", "-dn", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-",
    ]
    done = subprocess.run(command, capture_output=True, timeout=60, check=True, **_HIDE)
    return np.frombuffer(done.stdout[: len(done.stdout) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def analyze_file(path: Path) -> dict:
    """Blocking (about a second on the Pi): run it in a worker thread."""
    try:
        return estimate(decode_mono(path))
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.info("Beat analysis failed for %s: %s", Path(path).name, e)
        return dict(FALLBACK)
