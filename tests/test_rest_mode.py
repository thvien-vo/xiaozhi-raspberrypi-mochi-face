"""Rest mode ("Nghỉ"): no listening or speaking, no server session, state kept across restarts."""

import asyncio
import json
import threading

import pytest

from src.bootstrap.session import ConversationSession
from src.constants.constants import DeviceState, ListeningMode
from src.core import rest_mode
from src.core.event_bus import EventBus
from src.core.rest_mode import REST_CONFIG_KEY, RestController
from src.core.state_manager import StateManager
from src.plugins.always_listen import AlwaysListenPlugin
from src.plugins.rest import REST_TILT_DEG, HeadRest, ramp_frames, rest_pose
from src.ui.gui.services.face_state import ACTIVITY_ASLEEP, ACTIVITY_AWAKE, FaceStateMachine
from src.web_panel.backend import AppState, RobotBackend
from src.web_panel.commands import CommandError, parse_command


class Config:
    def __init__(self, **values):
        self.values = dict(values)
        self.saved = []

    def get_config(self, path, default=None):
        return self.values.get(path, default)

    def update_config(self, path, value, **_):
        self.values[path] = value
        self.saved.append((path, value))
        return True


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeProtocol:
    def __init__(self):
        self.opened = False
        self.calls = []
        self.protocol = object()

    def is_audio_channel_opened(self):
        return self.opened

    async def connect(self):
        self.calls.append("connect")
        self.opened = True
        return True

    async def disconnect(self):
        self.calls.append("disconnect")
        self.opened = False

    async def send_abort_speaking(self, reason):
        self.calls.append(("abort", reason))

    async def send_start_listening(self, mode):
        self.calls.append(("listen", mode))


class FakeCodec:
    def __init__(self):
        self.cleared = 0

    async def clear_audio_queue(self):
        self.cleared += 1


class FakePlugins:
    def __init__(self):
        self.codec = FakeCodec()

    def get_plugin(self, name):
        return self if name == "audio" else None

    async def notify_device_state_changed(self, _state):
        pass

    async def notify_protocol_connected(self, _protocol):
        pass


def make_session():
    bus = EventBus()
    state = StateManager(bus)
    protocol = FakeProtocol()
    plugins = FakePlugins()
    session = ConversationSession(state, protocol, plugins, bus)
    return session, state, protocol, plugins


def make_controller(session_parts=None, clock=None, **config):
    session, state, protocol, plugins = session_parts or make_session()
    cfg = Config(**config)
    rest = RestController(cfg, state, session, clock=clock or Clock())
    return rest, cfg, session, state, protocol, plugins


# ----- State and session -----


def test_resting_blocks_microphone_even_while_listening():
    state = StateManager(EventBus())
    state._device_state = DeviceState.LISTENING
    assert state.should_capture_audio()
    state.set_resting(True)
    assert not state.should_capture_audio()
    state.set_resting(False)
    assert state.should_capture_audio()


async def test_enter_rest_aborts_speech_and_closes_the_session():
    session, state, protocol, plugins = make_session()
    await protocol.connect()
    state.set_keep_listening(True)
    await state.set_device_state(DeviceState.SPEAKING)
    state.set_resting(True)
    protocol.calls.clear()

    await session.enter_rest()

    assert any(c[0] == "abort" for c in protocol.calls if isinstance(c, tuple))
    assert "disconnect" in protocol.calls
    assert not protocol.opened
    assert plugins.codec.cleared == 1
    assert state.device_state == DeviceState.IDLE
    assert not state.keep_listening


async def test_nothing_connects_while_resting():
    session, state, protocol, _ = make_session()
    state.set_resting(True)
    assert await session.connect_protocol() is False
    await session.start_listening(ListeningMode.REALTIME)
    assert await session.begin_greeting(ListeningMode.REALTIME, "Xin chào") is False
    await session.start_auto_conversation()
    assert protocol.calls == []
    assert state.device_state == DeviceState.IDLE


async def test_connection_that_finishes_after_rest_began_is_closed():
    session, state, protocol, _ = make_session()
    real_connect = protocol.connect

    async def slow_connect():
        ok = await real_connect()
        state.set_resting(True)  # Rest begins while the handshake is running
        return ok

    protocol.connect = slow_connect
    assert await session.connect_protocol() is False
    assert not protocol.opened


async def test_notifications_are_ignored_while_resting():
    session, state, _, _ = make_session()
    started = []

    class Player:
        is_active = False

        async def start(self, *a):
            started.append(a)
            return 1

    session.set_notification_player(Player())
    state.set_resting(True)
    await session._handle_notify({"type": "notify", "audio_url": "http://x/a.mp3"})
    assert started == []


# ----- Controller: transitions, debounce, persistence -----


async def test_rest_and_wake_update_state_config_and_listeners():
    clock = Clock()
    rest, cfg, session, state, protocol, plugins = make_controller(clock=clock)
    await protocol.connect()
    seen = []
    rest.add_listener(lambda resting, source: seen.append((resting, source)))

    answer = await rest.set_resting(True, "web")

    assert answer["ok"] and answer["changed"]
    assert rest.resting and state.resting
    assert cfg.values[REST_CONFIG_KEY] is True
    assert not protocol.opened and state.device_state == DeviceState.IDLE
    assert seen == [(True, "web")]
    assert rest.snapshot()["resting"] is True

    clock.t += 2
    answer = await rest.set_resting(False, "web")
    assert answer["ok"]
    assert not rest.resting and not state.resting
    assert cfg.values[REST_CONFIG_KEY] is False
    assert seen[-1] == (False, "web")
    # Waking only allows listening again; reconnecting is the always-listen supervisor's job
    assert not protocol.opened


async def test_same_state_is_not_a_change():
    rest, cfg, *_ = make_controller()
    answer = await rest.set_resting(False)
    assert answer == {"ok": True, "message": "Robot đang thức rồi", "changed": False}
    assert cfg.saved == []


async def test_rapid_toggles_are_ignored_for_a_second():
    clock = Clock()
    rest, cfg, *_ = make_controller(clock=clock)
    assert (await rest.set_resting(True))["ok"]
    clock.t += 0.4
    answer = await rest.set_resting(False)
    assert not answer["ok"] and "nhanh" in answer["message"]
    assert rest.resting
    clock.t += 0.7
    assert (await rest.set_resting(False))["ok"]
    assert not rest.resting


def test_saved_rest_state_is_restored_at_start():
    session, state, *_ = make_session()
    rest = RestController(Config(**{REST_CONFIG_KEY: True}), state, session)
    assert rest.resting and state.resting
    assert rest.snapshot()["source"] == rest_mode.SOURCE_BOOT
    assert not state.should_capture_audio()


async def test_listener_failure_does_not_stop_the_others():
    rest, *_ = make_controller()
    seen = []

    def bad(_r, _s):
        raise RuntimeError("boom")

    rest.add_listener(bad)
    rest.add_listener(lambda r, s: seen.append(r))
    await rest.set_resting(True)
    assert seen == [True]


async def test_request_from_another_thread_flips_the_flag_at_once_and_finishes_on_the_loop():
    rest, cfg, session, state, protocol, _ = make_controller()
    await protocol.connect()
    rest.attach_loop(asyncio.get_running_loop())
    done = asyncio.Event()
    rest.add_listener(lambda r, s: done.set())
    result = {}

    def web_thread():
        result["answer"] = rest.request(True, "web")
        result["flag_now"] = state.resting  # Microphone already stopped when request returns

    t = threading.Thread(target=web_thread)
    t.start()
    await asyncio.get_running_loop().run_in_executor(None, t.join)
    await asyncio.wait_for(done.wait(), 2)
    assert result["answer"]["ok"] and result["flag_now"]
    assert not protocol.opened
    assert cfg.values[REST_CONFIG_KEY] is True


async def test_voice_rest_waits_for_the_spoken_reply(monkeypatch):
    monkeypatch.setattr(rest_mode, "AFTER_REPLY_DELAY_S", 0.05)
    rest, cfg, session, state, protocol, _ = make_controller()
    rest.attach_loop(asyncio.get_running_loop())
    await protocol.connect()
    done = asyncio.Event()
    rest.add_listener(lambda r, s: done.set())

    answer = rest.request_after_reply()
    assert answer["ok"] and rest.pending_after_reply
    assert not rest.resting  # The answer must still be spoken
    await asyncio.sleep(0.02)
    assert not rest.resting

    rest.note_tts_stop()
    await asyncio.wait_for(done.wait(), 2)
    assert rest.resting and not rest.pending_after_reply
    assert rest.snapshot()["source"] == "voice"


async def test_pending_voice_rest_is_cancelled_by_a_web_wake_toggle():
    rest, *_ = make_controller()
    rest.attach_loop(asyncio.get_running_loop())
    rest.request_after_reply()
    await rest.set_resting(True)  # Rested another way meanwhile
    assert not rest.pending_after_reply


# ----- Always-listen supervisor stays quiet while resting -----


class World:
    def __init__(self, resting):
        self.resting = resting
        self.state = DeviceState.IDLE
        self.channel = False
        self.keep = False
        self.calls = []

    def is_resting(self):
        return self.resting

    def get_device_state(self):
        return self.state

    def is_audio_channel_opened(self):
        return self.channel

    def is_keep_listening(self):
        return self.keep

    def get_config(self):
        return Config()

    async def connect_protocol(self):
        self.calls.append("connect")
        self.channel = True
        return True

    async def start_listening(self, mode):
        self.calls.append("listen")
        self.keep = True
        self.state = DeviceState.LISTENING

    async def begin_greeting(self, mode, text):
        self.calls.append("greet")
        return True


async def test_always_listen_does_not_connect_greet_or_restore_while_resting(tmp_path):
    world = World(resting=True)
    plugin = AlwaysListenPlugin(greet=True, greet_state_file=tmp_path / "g", grace_s=0.5, startup_wait_s=1)
    plugin._ctx, plugin._cmd = world, world
    for t in (0.0, 1.0, 5.0, 60.0):
        await plugin.tick(t)
    assert world.calls == []

    world.resting = False  # Woken from the web: listening returns, with no greeting
    await plugin.tick(61.0)
    await plugin.tick(62.0)
    assert world.calls == ["connect", "listen"]
    assert "greet" not in world.calls


# ----- Face and head -----


def test_resting_face_sleeps_and_nothing_wakes_it():
    clock = Clock()
    face = FaceStateMachine(sleepy_after_s=90, asleep_after_s=240, clock=clock)
    face.set_resting(True)
    assert face.activity == ACTIVITY_ASLEEP
    face.touch()
    face.set_device_state("listening")
    face.on_user_text()
    face.tick()
    assert face.activity == ACTIVITY_ASLEEP
    assert face.visual_state != "thinking"
    face.set_resting(False)
    assert face.activity == ACTIVITY_AWAKE
    face.touch()
    assert face.activity == ACTIVITY_AWAKE


class FakeSettings:
    tilt_limit = 45.0
    pan_limit = 90.0


class FakeController:
    settings = FakeSettings()

    def __init__(self, pan=20.0, tilt=10.0):
        self.angles = (pan, tilt)
        self.set = []

    def set_angles(self, pan=None, tilt=None):
        self.angles = (pan, tilt)
        self.set.append((pan, tilt))
        return self.angles


class FakeTracker:
    def __init__(self, enabled):
        self.enabled = enabled
        self.history = []

    def set_enabled(self, value):
        self.enabled = value
        self.history.append(value)


class FakePlayer:
    def __init__(self, ok=True):
        self.ok = ok
        self.plays = []

    def play(self, name, frames, source):
        self.plays.append((name, frames, source))
        return self.ok


def test_rest_pose_bows_a_little_within_the_limit():
    assert rest_pose(45.0) == (0.0, REST_TILT_DEG)
    assert rest_pose(10.0) == (0.0, -10.0)
    frames = ramp_frames(20, 10, 0, REST_TILT_DEG, 2.5)
    assert frames[0].pan == 20 and frames[-1].tilt == REST_TILT_DEG and frames[-1].t == 2.5


def test_head_rests_pauses_tracking_and_restores_it_on_wake():
    controller, tracker, player = FakeController(), FakeTracker(True), FakePlayer()
    head = HeadRest(controller, tracker, player)
    head.rest()
    assert tracker.enabled is False
    name, frames, _ = player.plays[-1]
    assert (frames[-1].pan, frames[-1].tilt) == (0.0, REST_TILT_DEG)
    head.rest()  # Repeated rest must not forget that tracking was on
    head.wake()
    assert tracker.enabled is True
    assert (player.plays[-1][1][-1].pan, player.plays[-1][1][-1].tilt) == (0.0, 0.0)


def test_head_leaves_tracking_off_if_it_was_off():
    tracker = FakeTracker(False)
    head = HeadRest(FakeController(), tracker, FakePlayer())
    head.rest()
    head.wake()
    assert tracker.enabled is False


def test_head_moves_directly_when_another_control_holds_it():
    controller = FakeController()
    head = HeadRest(controller, None, FakePlayer(ok=False))
    head.rest()
    assert controller.set == [(0.0, REST_TILT_DEG)]


# ----- Web: command, backend, snapshot -----


def test_rest_command_is_validated():
    cmd = parse_command(json.dumps({"type": "rest", "enabled": True}), 90, 45)
    assert cmd.type == "rest" and cmd.args == {"enabled": True}
    for bad in ({"type": "rest"}, {"type": "rest", "enabled": "yes"}, {"type": "rest", "enabled": 1}):
        with pytest.raises(CommandError):
            parse_command(json.dumps(bad), 90, 45)


async def test_backend_rest_command_snapshot_and_guards():
    rest, cfg, session, state, protocol, _ = make_controller()
    rest.attach_loop(asyncio.get_running_loop())
    done = asyncio.Event()
    rest.add_listener(lambda r, s: done.set())
    app = AppState(text_supported=True)
    sent = []
    from src.web_panel.backend import AppHooks

    backend = RobotBackend(app_state=app, rest=rest, hooks=AppHooks(send_text=sent.append, face_wake=lambda: sent.append("wake")))
    changes = []
    backend.add_listener(lambda: changes.append(1))

    assert backend.snapshot()["rest"]["resting"] is False
    answer = backend.handle(parse_command(json.dumps({"type": "rest", "enabled": True}), 90, 45))
    assert answer["ok"]
    await asyncio.wait_for(done.wait(), 2)
    assert backend.snapshot()["rest"]["resting"] is True
    assert changes  # Browsers are told

    for kind, args in (("send_text", {"text": "xin chào"}), ("face_wake", {}), ("tracking", {"enabled": True})):
        result = backend.handle(parse_command(json.dumps({"type": kind, **args}), 90, 45))
        assert result["ok"] is False and "nghỉ" in result["message"], kind
    assert sent == []

    # Too soon to wake
    answer = backend.handle(parse_command(json.dumps({"type": "rest", "enabled": False}), 90, 45))
    assert answer["ok"] is False


def test_backend_without_rest_reports_it():
    backend = RobotBackend()
    assert backend.snapshot()["rest"] is None
    assert backend.handle(parse_command(json.dumps({"type": "rest", "enabled": True}), 90, 45))["ok"] is False


# ----- Voice tool -----


async def test_go_to_rest_tool_defers_the_rest():
    from src.mcp.tools.rest.register import go_to_rest

    rest, *_ = make_controller()
    rest.attach_loop(asyncio.get_running_loop())
    out = json.loads(go_to_rest(rest))
    assert out["success"] and "ngủ ngon" in out["message"]
    assert not rest.resting and rest.pending_after_reply

    await rest.set_resting(True)
    assert json.loads(go_to_rest(rest))["message"] == "Robot đang nghỉ rồi."


# ----- Qt face model -----


def test_face_model_sleeps_and_drops_the_sentence_it_was_showing():
    import os

    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["tests", "-platform", "offscreen"])
    assert app is not None
    from src.ui.gui.models import FaceModel, load_face_config

    class Defaults:
        def get_config(self, path, default=None):
            return default

    model = FaceModel(load_face_config(Defaults()))
    model.set_speech_text("tts", "Xin chào, bạn cần giúp gì không?")
    model.set_emotion("happy")
    serial = model.speechSerial
    model.set_resting(True)
    assert model.activity == "asleep"
    assert model.speechText == "" and model.speechSerial == serial + 1  # QML restarts empty
    assert model.emotion == "neutral" and not model.speechActive
    model.interact()
    model.set_speech_text("stt", "alo")
    model.set_music_bpm(120)
    assert model.activity == "asleep"
    model.set_resting(False)
    assert model.activity == "awake"
