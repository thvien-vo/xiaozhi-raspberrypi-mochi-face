import pytest


@pytest.fixture(autouse=True)
def _fresh_audio_levels():
    """The volume levels are process-wide: every test starts from defaults."""
    from src.audio_codecs.levels import reset_audio_levels

    reset_audio_levels()
    yield
    reset_audio_levels()


@pytest.fixture(autouse=True)
def _fresh_listen_filters():
    """STT filter, its gate and the microphone VAD are process-wide too."""
    from src.audio_processing.stt_filter import reset_stt_filter
    from src.audio_processing.vad_gate import reset_mic_vad
    from src.core.stt_gate import reset_stt_gate

    reset_stt_filter()
    reset_stt_gate()
    reset_mic_vad()
    yield
    reset_stt_filter()
    reset_stt_gate()
    reset_mic_vad()
