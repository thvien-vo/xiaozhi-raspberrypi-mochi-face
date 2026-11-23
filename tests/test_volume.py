"""Speaker volume: levels (master x channel, mute, saving), the mixer, voice tools, web commands."""

import asyncio
import json
import socket
import threading

import numpy as np
import pytest

from src.audio_codecs.levels import (
    CHANNELS,
    AudioLevels,
    clean_channel,
    clean_level,
    get_audio_levels,
    reset_audio_levels,
    taper,
)
from src.utils.config_manager import initialize_config
from src.web_panel.backend import RobotBackend
from src.web_panel.commands import CommandError, parse_command
from src.web_panel.settings import WebSettings


try:
    initialize_config()
except Exception:
    pass


class FakeCodec:
    output_running = True
    output_device_name = "Jabra test"

    def __init__(self):
        self.gains = None

    def set_output_gains(self, voice=1.0, music=1.0, effects=1.0):
        self.gains = {"voice": voice, "music": music, "effects": effects}


class MemoryConfig:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.writes = 0

    def get_config(self, path, default=None):
        return self.data.get(path, default)

    def update_configs(self, updates):
        self.writes += 1
        self.data.update(updates)
        return True


# ----- Levels -----

def test_effective_gain_is_master_times_channel():
    levels = AudioLevels({"master": 50, "voice": 80, "music": 100, "effects": 20})
    assert levels.gain("voice") == pytest.approx(taper(50) * taper(80))
    assert levels.gain("music") == pytest.approx(taper(50))
    assert levels.gain("effects") == pytest.approx(taper(50) * taper(20))
    assert levels.gain("master") == pytest.approx(taper(50))
    # The taper is multiplicative: master x channel is the taper of the product of percentages
    assert taper(50) * taper(80) == pytest.approx(taper(50 * 80 / 100))


def test_defaults_are_full_and_unmuted():
    levels = AudioLevels()
    assert [levels.get(c) for c in CHANNELS] == [100, 100, 100, 100]
    assert not levels.muted and levels.gain("music") == 1.0


def test_values_are_validated():
    assert clean_level(49.6) == 50 and clean_level(-5) == 0 and clean_level(300) == 100
    for bad in (True, "50", None, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            clean_level(bad)
    with pytest.raises(ValueError):
        clean_channel("bass")
    levels = AudioLevels()
    with pytest.raises(ValueError):
        levels.set("bass", 10)
    with pytest.raises(ValueError):
        levels.set("master", "loud")
    assert levels.get("master") == 100  # Nothing changed
    assert AudioLevels({"master": "x", "voice": 40}).get("master") == 100  # Bad saved values fall back


def test_mute_keeps_the_levels_and_unmute_restores_them():
    levels = AudioLevels({"master": 60, "voice": 90})
    codec = FakeCodec()
    levels.attach(codec)
    before = dict(codec.gains)
    levels.set_muted(True)
    assert levels.muted and codec.gains == {"voice": 0.0, "music": 0.0, "effects": 0.0}
    assert levels.get("master") == 60 and levels.get("voice") == 90
    levels.set_muted(False)
    assert codec.gains == before
    assert levels.toggle_muted() is True and levels.toggle_muted() is False


def test_raising_the_master_while_muted_unmutes_but_other_channels_do_not():
    levels = AudioLevels({"master": 40})
    levels.set_muted(True)
    levels.set("music", 30)
    assert levels.muted
    levels.change("master", -5)
    assert levels.muted
    levels.change("master", 5)
    assert not levels.muted
    levels.set_muted(True)
    levels.set("master", 0)
    assert levels.muted  # 0 is not a wish to hear anything
    levels.set("master", 10)
    assert not levels.muted


def test_change_steps_and_clamps():
    levels = AudioLevels({"master": 95})
    assert levels.change("master", 5) == 100 and levels.change("master", 5) == 100
    assert levels.change("master", -150) == 0
    with pytest.raises(ValueError):
        levels.change("master", float("nan"))


def test_levels_are_applied_to_the_codec_and_reapplied_to_a_new_one():
    levels = AudioLevels({"master": 50, "voice": 100, "music": 100})
    first = FakeCodec()
    levels.attach(first)
    assert first.gains["voice"] == pytest.approx(0.25)
    levels.set("voice", 50)
    assert first.gains["voice"] == pytest.approx(taper(50) * taper(50))
    # The speaker is unplugged and plugged in again: a new codec gets the same levels at once
    levels.attach(None)
    assert not levels.available and "Chưa có loa" in levels.snapshot()["message"]
    levels.set("master", 30)  # Still changeable without a speaker
    second = FakeCodec()
    levels.attach(second)
    assert second.gains["voice"] == pytest.approx(taper(30) * taper(50))
    assert levels.snapshot()["available"] and levels.snapshot()["device"] == "Jabra test"


def test_codec_without_output_gains_still_gets_the_music_level():
    class Old:
        output_running = True

        def set_music_gain(self, gain):
            self.gain = gain

    levels = AudioLevels({"master": 50, "music": 100})
    codec = Old()
    levels.attach(codec)
    assert codec.gain == pytest.approx(0.25)


def test_stream_not_running_is_reported():
    levels = AudioLevels()
    codec = FakeCodec()
    codec.output_running = False
    levels.attach(codec)
    snap = levels.snapshot()
    assert snap["available"] is False and snap["message"]


# ----- Config -----

def test_levels_are_saved_and_read_back():
    config = MemoryConfig()
    levels = AudioLevels.from_config(config)
    levels._save_delay = 0
    levels.set("master", 40)
    levels.set("voice", 70)
    levels.set_muted(True)
    assert config.data["AUDIO_VOLUME.MASTER"] == 40 and config.data["AUDIO_VOLUME.VOICE"] == 70
    assert config.data["AUDIO_VOLUME.MUTED"] is True
    again = AudioLevels.from_config(config)  # A restart
    assert again.get("master") == 40 and again.get("voice") == 70 and again.muted


def test_music_level_falls_back_to_the_old_setting():
    config = MemoryConfig({"MUSIC.VOLUME": 35})
    assert AudioLevels.from_config(config).get("music") == 35
    config.data["AUDIO_VOLUME.MUSIC"] = 60
    assert AudioLevels.from_config(config).get("music") == 60


def test_saving_is_delayed_and_batched():
    config = MemoryConfig()
    levels = AudioLevels.from_config(config)
    levels._save_delay = 0.2
    for v in range(0, 100, 5):
        levels.set("master", v)
    assert config.writes == 0
    done = threading.Event()
    original = levels._save
    levels._save = lambda updates: (original(updates), done.set())
    assert done.wait(2)
    assert config.writes == 1 and config.data["AUDIO_VOLUME.MASTER"] == 95


def test_not_saving_while_dragging():
    config = MemoryConfig()
    levels = AudioLevels.from_config(config)
    levels._save_delay = 0
    levels.set("master", 20, persist=False)
    assert config.writes == 0
    levels.set("master", 25)
    assert config.writes == 1


def test_listeners_hear_every_change():
    levels = AudioLevels()
    heard = []
    levels.add_listener(lambda: heard.append(1))
    levels.set("master", 10)
    levels.set_muted(True)
    levels.attach(FakeCodec())
    assert len(heard) == 3


def test_shared_levels_come_from_the_config(monkeypatch):
    config = MemoryConfig({"AUDIO_VOLUME.MASTER": 33})
    reset_audio_levels()
    levels = get_audio_levels(config)
    assert levels.get("master") == 33 and get_audio_levels() is levels


# ----- Mixer -----

@pytest.fixture
def codec():
    from src.audio_codecs.audio_codec import AudioCodec

    initialize_config()  # Another test may have reset it

    return AudioCodec()


def _block(codec, tts=None, music=None):
    n = codec._mix_chunk
    if tts is not None:
        codec._tts_fifo.push(np.full(n, tts, dtype=np.float32))
    if music is not None:
        codec._music_fifo.push(np.full(n, music, dtype=np.float32))
    return codec._pull_mixed(n)


def test_voice_and_music_are_scaled_separately(codec):
    codec.set_output_gains(voice=0.5, music=0.25)
    codec._voice_level = 0.5  # Settled, no ramp
    codec._music_volume_level = 0.25
    codec._music_level = 1.0
    out = _block(codec, tts=0.4)
    assert np.allclose(out, 0.2)
    codec._duck_hold = 0  # The voice is over: music at its own level
    codec._music_level = 1.0
    out = _block(codec, music=0.8)
    assert np.allclose(out, 0.8 * 0.25)


def test_mix_of_voice_and_music_uses_both_gains_and_ducking(codec):
    from src.audio_codecs.audio_codec import _MUSIC_DUCK_GAIN

    codec.set_output_gains(voice=0.5, music=0.5)
    codec._voice_level = codec._music_volume_level = 0.5
    out = _block(codec, tts=0.4, music=0.4)
    assert np.allclose(out, 0.4 * 0.5 + 0.4 * _MUSIC_DUCK_GAIN * 0.5)


def test_muting_silences_but_still_drains_the_queues(codec):
    codec.set_output_gains(voice=0.0, music=0.0)
    codec._voice_level = codec._music_volume_level = 0.0
    out = _block(codec, tts=0.9, music=0.9)
    assert out is not None and np.allclose(out, 0.0)
    assert codec._tts_fifo.size == 0 and codec._music_fifo.size == 0


def test_a_volume_change_ramps_inside_the_chunk(codec):
    codec.set_output_gains(voice=0.0, music=0.0)  # From full volume down to nothing
    out = _block(codec, tts=1.0)
    assert out[0] == pytest.approx(1.0) and out[-1] == pytest.approx(0.0, abs=1e-6)
    assert np.all(np.diff(out) <= 1e-6)  # No click
    out = _block(codec, tts=1.0)
    assert np.allclose(out, 0.0)


def test_effects_gain_is_kept_for_later(codec):
    codec.set_output_gains(voice=1.0, music=1.0, effects=0.3)
    assert codec.effects_gain == pytest.approx(0.3)


# ----- Voice tools -----

def _tools(levels):
    from src.mcp.tools.volume import register_volume_tools

    tools = {}
    register_volume_tools(lambda t: tools.__setitem__(t.name, t), None, levels)
    return tools


def _call(tools, name, **args):
    reply = json.loads(asyncio.run(tools[name].call(args)))
    return reply["content"][0]["text"]


def test_voice_tool_raises_and_lowers_the_master_by_default():
    levels = AudioLevels({"master": 50})
    tools = _tools(levels)
    assert "60" in _call(tools, "self.audio_speaker.set_volume", change=10)
    assert levels.get("master") == 60
    _call(tools, "self.audio_speaker.set_volume", change=-20)
    assert levels.get("master") == 40
    assert "50" in _call(tools, "self.audio_speaker.set_volume", volume=50)
    assert levels.get("master") == 50 and levels.get("music") == 100
    assert _call(tools, "self.audio_speaker.get_volume") == "50"


def test_voice_tool_mute_unmute_and_channels():
    levels = AudioLevels({"master": 70})
    tools = _tools(levels)
    _call(tools, "self.audio_speaker.set_volume", mute="on")
    assert levels.muted and levels.get("master") == 70
    assert "70" in _call(tools, "self.audio_speaker.set_volume", mute="off") and not levels.muted
    _call(tools, "self.audio_speaker.set_volume", mute="on")
    reply = _call(tools, "self.audio_speaker.set_volume", mute="off", change=-10)  # What the server really sent
    assert not levels.muted and levels.get("master") == 60 and "60" in reply
    _call(tools, "self.audio_speaker.set_volume", volume=30, channel="voice")
    assert levels.get("voice") == 30 and levels.get("master") == 60
    assert "không có" in _call(tools, "self.audio_speaker.set_volume", volume=30, channel="bass")
    assert "mute phải" in _call(tools, "self.audio_speaker.set_volume", mute="maybe")


def test_voice_tool_status_lists_every_channel():
    levels = AudioLevels({"master": 20, "voice": 30})
    levels.attach(FakeCodec())
    status = json.loads(_call(_tools(levels), "self.audio_speaker.get_volume_status"))
    assert status["master"] == 20 and status["voice"] == 30 and status["available"] is True
    assert {"music", "effects", "muted"} <= set(status)


def test_voice_tool_tells_when_there_is_no_speaker():
    levels = AudioLevels()
    assert "chưa có loa" in _call(_tools(levels), "self.audio_speaker.set_volume", volume=40)
    assert levels.get("master") == 40


def test_music_tool_and_the_music_slider_share_one_level():
    from src.mcp.tools.music.music_player import MusicPlayer

    player = MusicPlayer()
    levels = get_audio_levels()
    player.set_volume(35, save=False)
    assert levels.get("music") == 35 and player.volume == 35
    levels.set("music", 80)
    assert player.volume == 80 and player.change_volume(-30)["volume"] == 50


# ----- Web commands -----

def parse(data):
    return parse_command(json.dumps(data), 90, 45)


def test_volume_commands_are_parsed_and_clamped():
    c = parse({"type": "volume", "channel": "voice", "volume": 140, "save": False})
    assert c.type == "volume" and c.args == {"channel": "voice", "volume": 100.0, "save": False}
    assert parse({"type": "volume", "volume": 20}).args["channel"] == "master"
    assert parse({"type": "volume_step", "delta": -5}).args == {"channel": "master", "delta": -5.0}
    assert parse({"type": "mute"}).args == {"muted": None}
    assert parse({"type": "mute", "muted": True}).args == {"muted": True}


@pytest.mark.parametrize("data", [
    {"type": "volume"},
    {"type": "volume", "volume": "50"},
    {"type": "volume", "volume": True},
    {"type": "volume", "volume": 50, "channel": "bass"},
    {"type": "volume", "volume": 50, "channel": 3},
    {"type": "volume_step"},
    {"type": "volume_step", "delta": None},
    {"type": "mute", "muted": "yes"},
])
def test_bad_volume_commands_are_refused(data):
    with pytest.raises(CommandError):
        parse(data)


def make_backend(levels):
    return RobotBackend(levels=levels)


def test_backend_applies_commands_and_reports_state():
    levels = AudioLevels({"master": 50})
    codec = FakeCodec()
    levels.attach(codec)
    backend = make_backend(levels)
    changes = []
    backend.add_listener(lambda: changes.append(1))
    assert backend.handle(parse({"type": "volume", "channel": "music", "volume": 40}))["ok"]
    assert backend.handle(parse({"type": "volume_step", "delta": 5}))["ok"]
    assert backend.handle(parse({"type": "mute", "muted": True}))["message"] == "Đã tắt tiếng"
    snap = backend.snapshot()["volume"]
    assert snap["master"] == 55 and snap["music"] == 40 and snap["muted"] and snap["available"]
    assert backend.handle(parse({"type": "mute"}))["message"] == "Đã bật tiếng"
    assert len(changes) >= 4
    assert codec.gains["music"] == pytest.approx(taper(55) * taper(40))


def test_backend_without_levels_says_so():
    backend = RobotBackend()
    assert backend.snapshot()["volume"] is None
    assert backend.handle(parse({"type": "volume", "volume": 10}))["ok"] is False


def test_backend_keeps_the_value_when_there_is_no_speaker():
    levels = AudioLevels()
    backend = make_backend(levels)
    assert backend.handle(parse({"type": "volume", "volume": 25}))["ok"]
    snap = backend.snapshot()["volume"]
    assert snap["master"] == 25 and snap["available"] is False and snap["message"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_every_browser_hears_a_change_made_by_voice_or_another_browser():
    import aiohttp

    from src.utils.resource_finder import get_app_root
    from src.web_panel.server import WebPanelServer

    levels = AudioLevels({"master": 50})
    levels.attach(FakeCodec())
    backend = make_backend(levels)
    port = free_port()
    server = WebPanelServer(backend, WebSettings(enabled=True, host="127.0.0.1", port=port), get_app_root() / "web_assets")
    assert server.start()

    async def until(ws, check):
        while True:
            msg = await ws.receive_json(timeout=4)
            if msg["type"] == "state" and check(msg["volume"]):
                return msg["volume"]

    async def scenario():
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(f"http://127.0.0.1:{port}/ws") as a, http.ws_connect(f"http://127.0.0.1:{port}/ws") as b:
                await until(a, lambda v: v["master"] == 50)
                await until(b, lambda v: v["master"] == 50)
                await a.send_json({"type": "volume", "channel": "master", "volume": 70, "save": False})
                assert (await until(b, lambda v: v["master"] == 70))["muted"] is False
                # Spoken ("tắt tiếng"): both pages follow
                levels.set_muted(True)
                await until(a, lambda v: v["muted"])
                await until(b, lambda v: v["muted"])
                await b.send_json({"type": "mute", "muted": False, "id": 7})
                await until(a, lambda v: not v["muted"])
                # Not a channel: refused with a message, nothing changes
                await a.send_json({"type": "volume", "channel": "bass", "volume": 5, "id": 9})
                while True:
                    msg = await a.receive_json(timeout=4)
                    if msg["type"] == "result" and msg["id"] == 9:
                        assert not msg["ok"] and "kênh" in msg["message"]
                        break

    try:
        asyncio.run(scenario())
        assert levels.get("master") == 70 and not levels.muted
    finally:
        server.stop()
