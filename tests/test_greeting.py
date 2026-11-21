"""FACE.GREET_ON_START: wait for devices, greet once, listen only after the greeting ends."""

import json
from pathlib import Path

from src.constants.constants import DeviceState, ListeningMode
from src.plugins.always_listen import AlwaysListenPlugin
from src.utils.config_manager import ConfigManager

ROOT = Path(__file__).resolve().parent.parent
GREETING = "Xin chào"


class World:
    """PluginContext + PluginCommands stand-in that behaves like the real session.

    begin_greeting arms continuous listening and sends the detect event but does not open the
    microphone; the server's TTS start/stop (simulated by tts_start/tts_stop) drives the state.
    """

    def __init__(self, can_connect=True):
        self.state = DeviceState.IDLE
        self.channel = False
        self.keep = False
        self.can_connect = can_connect
        self.mic_open = False
        self.greetings = []
        self.calls = []

    # context
    def get_device_state(self):
        return self.state

    def is_audio_channel_opened(self):
        return self.channel

    def is_keep_listening(self):
        return self.keep

    def get_config(self):
        return self

    # config lookup used by the plugin (AEC off, as on the Pi)
    def get_config_value(self, path, default=None):  # pragma: no cover
        return default

    # commands
    async def connect_protocol(self):
        self.calls.append("connect")
        if self.can_connect:
            self.channel = True
        return self.can_connect

    async def begin_greeting(self, mode, text):
        self.calls.append("begin_greeting")
        if not await self.connect_protocol():
            return False
        self.keep = True
        self.greetings.append(text)
        return True

    async def start_listening(self, mode):
        self.calls.append(("listen", mode))
        self.keep = True
        self.state = DeviceState.LISTENING
        self.mic_open = True

    # server side of the greeting
    def tts_start(self):
        self.state = DeviceState.SPEAKING

    def tts_stop(self):
        self.state = DeviceState.LISTENING
        self.mic_open = True

    def drop(self):
        self.channel = False
        self.keep = False
        self.state = DeviceState.IDLE
        self.mic_open = False


class Cfg:
    def get_config(self, path, default=None):
        return False if path == "AEC_OPTIONS.ENABLED" else default


def make(world, tmp_path, *, now=1_000_000.0, audio_ready=None, greet=True, **kw):
    clock = {"t": now}
    plugin = AlwaysListenPlugin(
        backoff=(1, 2, 5, 10, 30),
        grace_s=0.5,
        stable_s=10,
        greet=greet,
        greet_text=GREETING,
        greet_state_file=tmp_path / "last-greeting",
        audio_ready=audio_ready,
        clock=lambda: clock["t"],
        **kw,
    )

    class Ctx:
        def __getattr__(self, name):
            return getattr(world, name)

        def get_config(self):
            return Cfg()

    plugin._ctx = Ctx()
    plugin._cmd = world
    return plugin, clock


async def test_greets_first_and_opens_the_mic_only_after_the_greeting(tmp_path):
    world = World()
    plugin, _ = make(world, tmp_path)
    await plugin.tick(0.0)
    assert world.greetings == [GREETING]
    assert not world.mic_open

    # Waiting for the server to speak: the supervisor must not start listening on its own
    for t in (0.5, 1.0, 3.0, 5.0):
        await plugin.tick(t)
        assert not world.mic_open
    assert ("listen", ListeningMode.AUTO_STOP) not in world.calls

    world.tts_start()
    for t in (6.0, 20.0, 40.0):  # Long greeting: still no microphone
        await plugin.tick(t)
        assert not world.mic_open
    world.tts_stop()
    await plugin.tick(41.0)
    assert world.mic_open
    assert world.greetings == [GREETING]  # Not repeated
    assert not any(isinstance(c, tuple) for c in world.calls)  # No extra start_listening


async def test_greeting_only_once_per_launch(tmp_path):
    world = World()
    plugin, _ = make(world, tmp_path)
    await plugin.tick(0.0)
    world.tts_start()
    world.tts_stop()
    for t in (1.0, 2.0, 3.0):
        await plugin.tick(t)
    assert len(world.greetings) == 1
    assert plugin.greet_count == 1


async def test_not_greeted_again_within_five_minutes_across_restarts(tmp_path):
    first = World()
    plugin, clock = make(first, tmp_path, now=1_000_000.0)
    await plugin.tick(0.0)
    assert len(first.greetings) == 1
    assert (tmp_path / "last-greeting").read_text().strip() == "1000000"

    # The app crashes and systemd starts a new process 100 s later: stays quiet, just listens
    second = World()
    plugin2, clock2 = make(second, tmp_path, now=1_000_100.0)
    await plugin2.tick(0.0)
    await plugin2.tick(0.6)
    assert second.greetings == []
    assert second.mic_open  # Still listens

    # More than 5 minutes later it greets again
    third = World()
    plugin3, _ = make(third, tmp_path, now=1_000_000.0 + 301)
    await plugin3.tick(0.0)
    assert len(third.greetings) == 1


async def test_clock_set_back_does_not_silence_the_greeting_forever(tmp_path):
    (tmp_path / "last-greeting").write_text("9999999999\n")
    world = World()
    plugin, _ = make(world, tmp_path, now=1_000_000.0)
    await plugin.tick(0.0)
    assert len(world.greetings) == 1


async def test_listens_again_after_a_drop_without_greeting_again(tmp_path):
    world = World()
    plugin, _ = make(world, tmp_path)
    await plugin.tick(0.0)
    world.tts_start()
    world.tts_stop()
    await plugin.tick(1.0)
    await plugin.tick(15.0)  # Stable

    world.drop()  # Server closed the session / WebSocket lost
    world.calls.clear()
    await plugin.tick(20.0)
    await plugin.tick(20.6)
    assert world.mic_open
    assert ("listen", ListeningMode.AUTO_STOP) in world.calls
    assert "begin_greeting" not in world.calls
    assert len(world.greetings) == 1


async def test_waits_for_the_audio_device_before_greeting(tmp_path):
    world = World()
    ready = {"v": False}
    plugin, _ = make(world, tmp_path, audio_ready=lambda: ready["v"])
    for t in (0.0, 5.0, 30.0, 80.0):
        await plugin.tick(t)
    assert world.calls == []  # Not even connecting

    ready["v"] = True
    await plugin.tick(81.0)
    assert world.greetings == [GREETING]


async def test_audio_device_never_ready_skips_greeting_but_keeps_trying_to_listen(tmp_path):
    world = World()
    plugin, _ = make(world, tmp_path, audio_ready=lambda: False)
    await plugin.tick(0.0)
    await plugin.tick(91.0)  # Wait is over
    assert world.greetings == []
    await plugin.tick(92.0)
    await plugin.tick(93.0)
    assert world.mic_open  # Normal supervision took over


async def test_server_unreachable_retries_with_backoff_then_gives_up(tmp_path):
    world = World(can_connect=False)
    plugin, _ = make(world, tmp_path)
    attempts = []
    t = 0.0
    while t < 200.0:
        before = world.calls.count("begin_greeting")
        await plugin.tick(t)
        if world.calls.count("begin_greeting") > before:
            attempts.append(round(t, 2))
        t += 0.25
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert gaps[:4] == [1.0, 2.0, 5.0, 10.0]
    assert all(g == 30.0 for g in gaps[4:])
    assert attempts[-1] < 120  # Gave up greeting; normal restore carries on
    assert world.greetings == []
    assert not (tmp_path / "last-greeting").exists()  # Nothing was said, nothing recorded


async def test_server_that_never_speaks_does_not_leave_the_robot_deaf(tmp_path):
    world = World()
    plugin, _ = make(world, tmp_path)
    await plugin.tick(0.0)
    await plugin.tick(9.0)
    assert not world.mic_open
    await plugin.tick(11.0)  # No TTS within 10 s
    await plugin.tick(12.0)
    await plugin.tick(12.6)
    assert world.mic_open


async def test_connection_lost_during_the_greeting_recovers_without_a_second_greeting(tmp_path):
    world = World()
    plugin, _ = make(world, tmp_path)
    await plugin.tick(0.0)
    world.tts_start()
    await plugin.tick(1.0)
    world.drop()
    for t in (2.0, 2.6, 3.0):
        await plugin.tick(t)
    assert world.mic_open
    assert len(world.greetings) == 1


async def test_greeting_disabled_just_listens(tmp_path):
    world = World()
    plugin, _ = make(world, tmp_path, greet=False)
    await plugin.tick(0.0)
    await plugin.tick(0.6)
    assert world.greetings == []
    assert world.mic_open


def test_defaults_off_and_pi_preset_on():
    face = ConfigManager.DEFAULT_CONFIG["FACE"]
    assert face["GREET_ON_START"] is False
    assert face["ALWAYS_LISTEN"] is False
    assert face["GREET_TEXT"]
    preset = json.loads((ROOT / "deploy/raspberry-pi/config.pi5.json").read_text(encoding="utf-8"))
    assert preset["FACE"]["GREET_ON_START"] is True
    assert preset["FACE"]["ALWAYS_LISTEN"] is True
    # Wake word stays off, software AEC stays off
    assert preset["WAKE_WORD_OPTIONS"]["USE_WAKE_WORD"] is False
    assert preset["AEC_OPTIONS"]["ENABLED"] is False
    # The server rejects long text on the detect channel ("only for wake words")
    assert len(preset["FACE"]["GREET_TEXT"]) <= 16


def test_from_config_reads_face_settings():
    class Config:
        def get_config(self, path, default=None):
            return {
                "FACE.GREET_ON_START": True,
                "FACE.GREET_TEXT": "Chào bạn",
                "FACE.GREET_MIN_INTERVAL_S": 120,
            }.get(path, default)

    class Audio:
        audio_ready = True

    plugin = AlwaysListenPlugin.from_config(Config(), Audio())
    assert plugin._greet and plugin._greet_text == "Chào bạn"
    assert plugin._greet_min_interval_s == 120
    assert plugin._audio_ready() is True


# ---- the real ConversationSession: the microphone must stay shut during the greeting ----


class _Plugins:
    def get_plugin(self, name):
        return None

    async def notify_device_state_changed(self, state):
        pass

    async def notify_protocol_connected(self, protocol):
        pass

    async def notify_incoming_json(self, data):
        pass


class _Protocol:
    """Opening the channel emits audio_channel_opened, as the real WebSocket protocol does."""

    def __init__(self, bus):
        self.bus = bus
        self.open = False
        self.sent = []
        self.protocol = object()

    def is_audio_channel_opened(self):
        return self.open

    async def connect(self):
        self.open = True
        await self.bus.emit("audio_channel_opened")
        return True

    async def send_wake_word_detected(self, text):
        self.sent.append(("detect", text))

    async def send_start_listening(self, mode):
        self.sent.append(("listen", mode))


async def test_session_greeting_keeps_the_microphone_shut_until_tts_stops():
    from src.bootstrap.session import ConversationSession
    from src.core.event_bus import EventBus, Events
    from src.core.state_manager import StateManager

    bus = EventBus()
    state = StateManager(bus, aec_enabled=False)
    protocol = _Protocol(bus)
    session = ConversationSession(state, protocol, _Plugins(), bus)
    session.bind_events(bus)

    assert await session.begin_greeting(ListeningMode.AUTO_STOP, GREETING)
    # Like the firmware: detect, then listen start (the server answers inside a listen session)
    assert protocol.sent == [("detect", GREETING), ("listen", ListeningMode.AUTO_STOP)]
    assert state.device_state == DeviceState.IDLE
    assert not state.should_capture_audio()  # The mic is not streaming to the server

    await session._on_incoming_json({"type": "tts", "state": "start"})
    assert state.device_state == DeviceState.SPEAKING
    assert not state.should_capture_audio()  # Does not hear its own greeting

    await session._on_incoming_json({"type": "tts", "state": "stop"})
    assert state.device_state == DeviceState.LISTENING
    assert state.should_capture_audio()
    assert protocol.sent[-1] == ("listen", ListeningMode.AUTO_STOP)
    assert len(protocol.sent) == 3
    assert state.keep_listening  # And it keeps listening after every reply
