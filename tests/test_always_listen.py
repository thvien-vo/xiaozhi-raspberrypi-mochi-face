"""FACE.ALWAYS_LISTEN: auto start, restore after any stop, taps interrupt instead of stopping."""

import pytest

from src.constants.constants import AbortReason, DeviceState, ListeningMode
from src.plugins.always_listen import AlwaysListenPlugin
from src.plugins.ui_session import SessionActions


class FakeWorld:
    """Stands in for both PluginContext and PluginCommands."""

    def __init__(self, can_connect=True, always_listen=True):
        self.state = DeviceState.IDLE
        self.channel = False
        self.keep = False
        self.can_connect = can_connect
        self.always_listen = always_listen
        self.calls = []

    # context
    def get_device_state(self):
        return self.state

    def is_audio_channel_opened(self):
        return self.channel

    def is_keep_listening(self):
        return self.keep

    def is_listening(self):
        return self.state == DeviceState.LISTENING

    def is_speaking(self):
        return self.state == DeviceState.SPEAKING

    def get_config(self):
        return self

    def get_config_value(self):  # pragma: no cover
        pass

    # ConfigManager.get_config(path, default) shares the name with the context method
    # above, so dispatch on arguments.
    # commands
    async def connect_protocol(self):
        self.calls.append("connect")
        if self.can_connect:
            self.channel = True
        return self.can_connect

    async def start_listening(self, mode):
        self.calls.append(("listen", mode))
        self.keep = mode != ListeningMode.MANUAL
        self.state = DeviceState.LISTENING

    async def abort_speaking(self, reason):
        self.calls.append(("abort", reason))
        self.state = DeviceState.LISTENING

    def drop(self):
        """Server closes the session / network error: channel gone, back to idle."""
        self.channel = False
        self.keep = False
        self.state = DeviceState.IDLE


class Config:
    def __init__(self, values):
        self.values = values

    def get_config(self, path, default=None):
        return self.values.get(path, default)


def make_ctx(world, **config):
    """Context whose get_config() returns a ConfigManager-like object."""

    class Ctx:
        def __getattr__(self, name):
            return getattr(world, name)

        def get_config(self):
            return Config(config)

    return Ctx()


async def make_plugin(world, **config):
    plugin = AlwaysListenPlugin(backoff=(1, 2, 5, 10, 30), grace_s=0.5, stable_s=10)
    plugin._ctx = make_ctx(world, **config)
    plugin._cmd = world
    return plugin


async def test_starts_listening_without_a_tap():
    world = FakeWorld()
    plugin = await make_plugin(world, **{"AEC_OPTIONS.ENABLED": False})
    await plugin.tick(0.0)  # Noticed, waiting out the grace time
    assert world.state == DeviceState.IDLE
    await plugin.tick(0.6)
    assert world.state == DeviceState.LISTENING
    assert world.keep and world.channel
    # AEC off -> auto_stop
    assert ("listen", ListeningMode.AUTO_STOP) in world.calls


async def test_uses_realtime_when_aec_is_on():
    world = FakeWorld()
    plugin = await make_plugin(world, **{"AEC_OPTIONS.ENABLED": True})
    await plugin.tick(0.0)
    await plugin.tick(0.6)
    assert ("listen", ListeningMode.REALTIME) in world.calls


async def test_resumes_after_server_closes_session():
    world = FakeWorld()
    plugin = await make_plugin(world)
    await plugin.tick(0.0)
    await plugin.tick(0.6)
    await plugin.tick(15.0)  # Stable for a while, backoff reset
    world.drop()
    world.calls.clear()
    await plugin.tick(20.0)
    await plugin.tick(20.6)
    assert world.state == DeviceState.LISTENING
    assert "connect" in world.calls


async def test_speaking_counts_as_listening_and_is_left_alone():
    world = FakeWorld()
    plugin = await make_plugin(world)
    await plugin.tick(0.0)
    await plugin.tick(0.6)
    world.state = DeviceState.SPEAKING
    world.calls.clear()
    for t in (1, 2, 3):
        await plugin.tick(float(t))
    assert world.calls == []


async def test_backoff_grows_while_network_is_down_and_never_floods():
    world = FakeWorld(can_connect=False)
    plugin = await make_plugin(world)
    attempts = []
    t = 0.0
    while t < 120.0:
        before = world.calls.count("connect")
        await plugin.tick(t)
        if world.calls.count("connect") > before:
            attempts.append(round(t, 2))
        t += 0.25
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert attempts[0] == 0.5
    assert gaps[:4] == [1.0, 2.0, 5.0, 10.0]
    assert all(g == 30.0 for g in gaps[4:])
    assert len(attempts) < 12  # Not dozens of attempts in two minutes


async def test_recovers_when_network_returns_and_resets_backoff():
    world = FakeWorld(can_connect=False)
    plugin = await make_plugin(world)
    for t in (0.0, 0.5, 1.5, 3.5):
        await plugin.tick(t)
    world.can_connect = True
    await plugin.tick(8.5)
    assert world.state == DeviceState.LISTENING
    for t in (10.0, 19.0, 19.5):  # Stable for 10 s
        await plugin.tick(t)
    assert plugin._failures == 0


async def test_session_dropped_right_after_restore_is_not_a_tight_loop():
    world = FakeWorld()
    plugin = await make_plugin(world)
    await plugin.tick(0.0)
    await plugin.tick(0.6)  # Restored
    world.drop()  # Server closes it again immediately
    world.calls.clear()
    await plugin.tick(0.7)
    await plugin.tick(1.0)
    assert world.calls == []  # Waiting out the backoff
    await plugin.tick(2.0)
    assert world.state == DeviceState.LISTENING


async def test_notifying_is_left_alone():
    world = FakeWorld()
    world.state = DeviceState.NOTIFYING
    plugin = await make_plugin(world)
    for t in (0.0, 1.0, 5.0):
        await plugin.tick(t)
    assert world.calls == []


async def test_supervisor_survives_errors():
    world = FakeWorld()

    async def boom():
        raise RuntimeError("audio error")

    world.connect_protocol = boom
    plugin = await make_plugin(world)
    await plugin.tick(0.0)
    await plugin.tick(0.6)  # Error is caught inside the restore
    assert plugin._failures == 1
    assert world.state == DeviceState.IDLE


async def test_logs_possible_self_hearing(caplog):
    world = FakeWorld()
    plugin = await make_plugin(world)
    messages = []
    import src.plugins.always_listen as mod

    mod.logger = type("L", (), {"warning": lambda self, m, *a, **k: messages.append(m),
                                "info": lambda *a, **k: None,
                                "error": lambda *a, **k: None})()
    await plugin.on_incoming_json({"type": "tts", "state": "sentence_start", "text": "Xin chào bạn"})
    await plugin.on_incoming_json({"type": "stt", "text": "xin chào bạn."})
    assert any("self-hearing" in m for m in messages)
    messages.clear()
    await plugin.on_incoming_json({"type": "stt", "text": "hôm nay trời đẹp quá"})
    assert messages == []


# ---- SessionActions: taps ----


class FakePresenter:
    def __init__(self):
        self.auto = None
        self.text = None

    def set_auto_mode(self, v):
        self.auto = v

    def set_button_text(self, t):
        self.text = t


def make_session(world, always_listen=True):
    cfg = {"FACE.ALWAYS_LISTEN": always_listen, "FACE.AUTO_MODE": True}
    session = SessionActions(make_ctx(world, **cfg), _Cmd(world), FakePresenter())

    class Bus:
        def on(self, *_):
            pass

    session.subscribe(Bus())
    return session


class _Cmd:
    def __init__(self, world):
        self.w = world
        self.stopped = 0

    async def abort_speaking(self, reason):
        await self.w.abort_speaking(reason)

    async def stop_listening(self):
        self.stopped += 1

    async def connect_protocol(self):
        return await self.w.connect_protocol()

    async def start_listening(self, mode):
        await self.w.start_listening(mode)


async def test_tap_while_speaking_interrupts_and_keeps_listening():
    world = FakeWorld()
    world.state, world.channel, world.keep = DeviceState.SPEAKING, True, True
    session = make_session(world)
    await session.auto_session_toggle()
    assert ("abort", AbortReason.USER_INTERRUPTION) in world.calls
    assert world.state == DeviceState.LISTENING
    assert session._cmd.stopped == 0


async def test_tap_while_listening_does_nothing():
    world = FakeWorld()
    world.state, world.channel, world.keep = DeviceState.LISTENING, True, True
    session = make_session(world)
    await session.auto_session_toggle()
    await session.auto_session_toggle()
    assert world.calls == []
    assert world.state == DeviceState.LISTENING
    assert session._cmd.stopped == 0


async def test_mode_cannot_be_switched_off():
    world = FakeWorld()
    session = make_session(world)
    assert session.auto_mode
    await session.auto_toggle()
    assert session.auto_mode


async def test_without_always_listen_tap_still_stops_chat():
    world = FakeWorld()
    world.state, world.channel, world.keep = DeviceState.LISTENING, True, True
    session = make_session(world, always_listen=False)
    await session.auto_session_toggle()
    assert session._cmd.stopped == 1


def test_default_off_and_pi_preset_on():
    import json
    from pathlib import Path

    from src.utils.config_manager import ConfigManager

    assert ConfigManager.DEFAULT_CONFIG["FACE"]["ALWAYS_LISTEN"] is False
    preset = Path(__file__).resolve().parent.parent / "deploy/raspberry-pi/config.pi5.json"
    assert json.loads(preset.read_text(encoding="utf-8"))["FACE"]["ALWAYS_LISTEN"] is True
