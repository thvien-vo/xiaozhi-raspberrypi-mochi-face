"""Regression tests for separate TTS/music queue mixing.

Covers: PcmFifo sample-level semantics and _pull_mixed mixing, ducking, and clipping protection.
Background: TTS and music previously shared a single FIFO, causing frame interleaving ("simultaneous playback + stuttering") during sentence-by-sentence TTS.
"""

import numpy as np
import pytest

from src.utils.config_manager import initialize_config

try:
    initialize_config()
except Exception:
    pass

from src.audio_codecs.audio_buffer import PcmFifo  # noqa: E402
from src.audio_codecs.audio_codec import (  # noqa: E402
    _MUSIC_DUCK_GAIN,
    AudioCodec,
)


class TestPcmFifo:
    def test_pull_basic(self):
        f = PcmFifo(1000)
        f.push(np.ones(300, dtype=np.float32))
        out = f.pull(200)
        assert out.shape == (200,)
        assert np.all(out == 1)
        assert f.size == 100

    def test_pull_pads_zeros_when_short(self):
        f = PcmFifo(1000)
        f.push(np.ones(100, dtype=np.float32))
        out = f.pull(200)
        assert np.all(out[:100] == 1)
        assert np.all(out[100:] == 0)
        assert f.size == 0

    def test_pull_with_count_excludes_padding(self):
        f = PcmFifo(1000)
        f.push(np.ones(100, dtype=np.float32))
        out, count = f.pull_with_count(200)
        assert count == 100
        assert np.all(out[:100] == 1)
        assert np.all(out[100:] == 0)

    def test_pull_empty_returns_none(self):
        f = PcmFifo(1000)
        assert f.pull(10) is None

    def test_push_drops_oldest_over_capacity(self):
        f = PcmFifo(1000)
        f.push(np.zeros(600, dtype=np.float32))
        f.push(np.ones(600, dtype=np.float32))
        assert f.size == 1000
        assert f.dropped == 200
        out = f.pull(1000)
        # The oldest 200 zeros are dropped, leaving 400 zeros + 600 ones
        assert np.all(out[:400] == 0)
        assert np.all(out[400:] == 1)

    def test_clear(self):
        f = PcmFifo(1000)
        f.push(np.ones(500, dtype=np.float32))
        assert f.clear() == 500
        assert f.size == 0
        assert f.pull(10) is None

    def test_multichannel_flattened(self):
        f = PcmFifo(1000)
        f.push(np.ones((100, 2), dtype=np.float32))
        assert f.size == 200


class TestMixing:
    @pytest.fixture()
    def codec(self):
        return AudioCodec()

    def test_music_only_full_gain(self, codec):
        n = codec._mix_chunk
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert np.allclose(out, 0.4)

    def test_tts_only(self, codec):
        n = codec._mix_chunk
        codec._tts_fifo.push(np.full(n, 0.5, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert np.allclose(out, 0.5)

    def test_notification_has_voice_priority_and_reports_drain_after_output(
        self, codec
    ):
        n = codec._mix_chunk
        progress = []
        drained = []
        codec.set_notification_playback_callbacks(
            progress.append, lambda: drained.append(True)
        )
        codec._notification_fifo.push(np.full(n, 0.5, dtype=np.float32))
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))

        out = codec._pull_mixed(n)
        assert np.allclose(out, 0.5 + _MUSIC_DUCK_GAIN * 0.4)
        assert progress == [n]

        codec._pull_mixed(n)
        codec._notify_notification_drained_after_output()
        assert drained == []
        codec._notify_notification_drained_after_output()
        assert drained == [True]

    def test_tts_and_music_mixed_with_duck(self, codec):
        n = codec._mix_chunk
        codec._tts_fifo.push(np.full(n, 0.5, dtype=np.float32))
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert np.allclose(out, 0.5 + _MUSIC_DUCK_GAIN * 0.4)

    def test_duck_hangover_then_recover(self, codec):
        n = codec._mix_chunk
        # After one TTS chunk, music remains ducked during the hangover period
        codec._tts_fifo.push(np.full(n, 0.5, dtype=np.float32))
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        codec._pull_mixed(n)

        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert np.allclose(out, _MUSIC_DUCK_GAIN * 0.4)

        # Full gain recovers once hangover expires
        for _ in range(15):
            codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
            out = codec._pull_mixed(n)
        assert np.allclose(out, 0.4)

    def test_clip_protection(self, codec):
        n = codec._mix_chunk
        codec._tts_fifo.push(np.full(n, 0.9, dtype=np.float32))
        codec._music_fifo.push(np.full(n, 0.9, dtype=np.float32))
        out = codec._pull_mixed(n)
        assert out.max() <= 1.0

    def test_both_empty_returns_none(self, codec):
        assert codec._pull_mixed(codec._mix_chunk) is None

    def test_clear_semantics_are_independent(self, codec):
        n = codec._mix_chunk
        codec._tts_fifo.push(np.full(n, 0.5, dtype=np.float32))
        codec._music_fifo.push(np.full(n, 0.4, dtype=np.float32))
        codec._tts_fifo.clear()
        # Clearing TTS does not affect music
        assert codec._music_fifo.size == n


def test_output_resampling_does_not_fold_high_frequencies_into_the_audio_band():
    import numpy as np

    from src.audio_codecs.audio_converter import AudioConverter

    converter = AudioConverter()
    converter.setup_output_converter(24000, 16000, from_channels=1, to_channels=1)
    # 10 kHz is above the 8 kHz Nyquist of 16 kHz output: it must be removed, not aliased
    t = np.arange(24000) / 24000
    tone = (0.5 * np.sin(2 * np.pi * 10000 * t)).astype(np.float32)
    chunks = []
    for start in range(0, len(tone), 480):
        out = converter.convert_output(tone[start : start + 480], 320)
        if out is not None:
            chunks.append(out)
    audio = np.concatenate(chunks)[4000:]
    assert np.sqrt(np.mean(audio**2)) < 0.01
