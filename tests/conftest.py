import pytest


@pytest.fixture(autouse=True)
def _fresh_audio_levels():
    """The volume levels are process-wide: every test starts from defaults."""
    from src.audio_codecs.levels import reset_audio_levels

    reset_audio_levels()
    yield
    reset_audio_levels()
