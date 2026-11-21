"""Web panel: command parsing, control priority, gestures, recordings, backend, server."""

import asyncio
import json
import socket
import threading
import time

import pytest

from src.mcp.tools.servo import register_servo_tools
from src.servo.motion import (
    SOURCE_TRACKING,
    SOURCE_VOICE,
    SOURCE_WEB,
    ControlArbiter,
    Keyframe,
    MotionLibrary,
    MotionPlayer,
    MotionRecorder,
    clean_name,
    gesture_frames,
    sample,
    validate_frames,
)
from src.servo.servo_controller import ServoController
from src.servo.settings import ServoSettings
from src.web_panel.backend import AppHooks, AppState, RobotBackend
from src.web_panel.commands import CommandError, RateLimiter, parse_command
from src.web_panel.settings import WebSettings, load_web_settings


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeController:
    """Angle API of ServoController without a thread."""

    def __init__(self, settings=None):
        self.settings = settings or ServoSettings(enabled=True)
        self._pan = self._tilt = 0.0
        self.calls = []
        self.connected = True
        self.port = "FAKE"
        self.last_reply = "OK 90 90"
        self.acked_angles = (0.0, 0.0)

    @property
    def angles(self):
        return self._pan, self._tilt

    def set_angles(self, pan=None, tilt=None):
        s = self.settings
        if pan is not None:
            self._pan = max(-s.pan_limit, min(s.pan_limit, pan))
        if tilt is not None:
            self._tilt = max(-s.tilt_limit, min(s.tilt_limit, tilt))
        self.calls.append((self._pan, self._tilt))
        return self._pan, self._tilt

    def nudge(self, dpan=0.0, dtilt=0.0):
        return self.set_angles(self._pan + dpan, self._tilt + dtilt)

    def center(self):
        return self.set_angles(0.0, 0.0)

    def estimated_angles(self):
        return self.angles


class FakeTracker:
    def __init__(self):
        self.enabled = True
        self.holds = []
        self.holding = False

    def hold(self, seconds=None):
        self.holds.append(seconds)

    def set_enabled(self, enabled):
        self.enabled = enabled


# ----- Command parsing -----

def parse(obj, emotions=()):
    return parse_command(json.dumps(obj), 90, 45, emotions)


def test_set_angles_is_clamped_to_limits():
    cmd = parse({"type": "set_angles", "pan": 500, "tilt": -99.5})
    assert cmd.args == {"pan": 90.0, "tilt": -45.0}


def test_set_angles_partial_and_invalid():
    assert parse({"type": "set_angles", "tilt": 10}).args == {"pan": None, "tilt": 10.0}
    for bad in (
        {"type": "set_angles"},
        {"type": "set_angles", "pan": "10"},
        {"type": "set_angles", "pan": True},
        {"type": "set_angles", "pan": float("nan")},
    ):
        with pytest.raises(CommandError):
            parse_command(json.dumps(bad, allow_nan=True), 90, 45)


@pytest.mark.parametrize(
    "raw",
    ["not json", "[1,2]", json.dumps({"type": 5}), json.dumps({"type": "rm -rf"}), "x" * 5000],
)
def test_garbage_is_rejected(raw):
    with pytest.raises(CommandError):
        parse_command(raw, 90, 45)


def test_tracking_params_ranges():
    cmd = parse({"type": "tracking_params", "kp_pan": 1000, "deadband": -1})
    assert cmd.args["kp_pan"] == 40.0 and cmd.args["deadband"] == 0.0 and cmd.args["kp_tilt"] is None
    assert cmd.args["save"] is False
    with pytest.raises(CommandError):
        parse({"type": "tracking_params"})


def test_emotion_gesture_and_names():
    assert parse({"type": "emotion", "name": "Happy"}, ("happy",)).args == {"name": "happy"}
    with pytest.raises(CommandError):
        parse({"type": "emotion", "name": "evil"}, ("happy",))
    with pytest.raises(CommandError):
        parse({"type": "gesture", "name": "backflip"})
    assert parse({"type": "record_stop", "name": "../../etc/passwd"}).args == {"name": "etcpasswd"}
    with pytest.raises(CommandError):
        parse({"type": "play_motion", "name": "../.."})
    with pytest.raises(CommandError):
        parse({"type": "tracking", "enabled": "yes"})


def test_send_text_is_trimmed_and_bounded():
    assert parse({"type": "send_text", "text": "  chào  "}).args == {"text": "chào"}
    assert len(parse({"type": "send_text", "text": "a" * 900}).args["text"]) == 300
    with pytest.raises(CommandError):
        parse({"type": "send_text", "text": "   "})


def test_rate_limiter():
    clock = Clock(0.0)
    limiter = RateLimiter(10, clock=clock)
    assert sum(limiter.allow() for _ in range(25)) == 10
    clock.t = 0.5
    assert sum(limiter.allow() for _ in range(25)) == 5


def test_web_settings_defaults_and_ranges():
    s = load_web_settings()
    assert s == WebSettings()

    class Cfg:
        def get_config(self, path, default=None):
            return {"WEB.PORT": 999999, "WEB.CAMERA_STREAM_FPS": 0, "WEB.ENABLED": "true"}.get(path, default)

    s = load_web_settings(Cfg())
    assert s.port == 65535 and s.camera_stream_fps == 0.5 and s.enabled is True


# ----- Control priority -----

def test_web_beats_voice_beats_tracking():
    clock = Clock()
    tracker = FakeTracker()
    arb = ControlArbiter(tracker, hold_sec=5.0, clock=clock)
    assert arb.request(SOURCE_TRACKING)
    assert arb.request(SOURCE_VOICE)
    assert arb.request(SOURCE_WEB)
    assert arb.owner == SOURCE_WEB
    assert not arb.request(SOURCE_VOICE)
    assert not arb.request(SOURCE_TRACKING)
    assert tracker.holds[-1] == pytest.approx(5.0)  # Tracking paused for the web's hold
    clock.t += 5.1  # MANUAL_HOLD_SEC without input: the web lets go
    assert arb.owner is None
    assert arb.request(SOURCE_VOICE)
    assert not arb.request(SOURCE_TRACKING)


def test_release_shortens_but_keeps_hold():
    clock = Clock()
    tracker = FakeTracker()
    arb = ControlArbiter(tracker, hold_sec=2.0, clock=clock)
    arb.request(SOURCE_WEB, hold=30.0)
    arb.release(SOURCE_WEB)
    assert arb.remaining() == pytest.approx(2.0)
    assert tracker.holds[-1] == pytest.approx(2.0)


def test_voice_tools_refused_while_web_controls():
    tools = {}
    controller = FakeController()
    clock = Clock()
    arb = ControlArbiter(None, 5.0, clock=clock)
    player = MotionPlayer(controller, arb)
    register_servo_tools(lambda t: tools.__setitem__(t.name, t), controller, None, arbiter=arb, player=player)
    arb.request(SOURCE_WEB)
    result = json.loads(tools["self.head.turn_left"].callback({"degrees": 20}))
    assert result["success"] is False and controller.angles == (0.0, 0.0)
    clock.t += 6
    result = json.loads(tools["self.head.turn_left"].callback({"degrees": 20}))
    assert controller.angles == (20.0, 0.0)
    assert "self.head.gesture" in tools


def test_voice_gesture_runs_on_robot():
    tools = {}
    controller = FakeController()
    arb = ControlArbiter(None, 0.1)
    player = MotionPlayer(controller, arb)
    register_servo_tools(lambda t: tools.__setitem__(t.name, t), controller, None, arbiter=arb, player=player)
    result = json.loads(tools["self.head.gesture"].callback({"name": "nod"}))
    assert result["success"] is True
    assert wait_until(lambda: player.playing is None, 5.0)
    tilts = [t for _, t in controller.calls]
    assert min(tilts) < -8 and controller.angles == (0.0, 0.0)


# ----- Gestures, recordings -----

def wait_until(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("name", ["nod", "shake", "look_around", "surprised", "dance"])
def test_gestures_stay_in_limits_and_return(name):
    frames = gesture_frames(name, 85.0, 40.0, 90.0, 45.0)
    assert frames[0] == Keyframe(0.0, 85.0, 40.0)
    assert (frames[-1].pan, frames[-1].tilt) == (85.0, 40.0)
    assert all(-90 <= f.pan <= 90 and -45 <= f.tilt <= 45 for f in frames)
    assert all(b.t >= a.t for a, b in zip(frames, frames[1:]))


def test_sample_interpolates():
    frames = [Keyframe(0, 0, 0), Keyframe(1, 10, -10)]
    assert sample(frames, -1) == (0, 0)
    assert sample(frames, 0.5) == pytest.approx((5, -5))
    assert sample(frames, 9) == (10, -10)


def test_recorder_and_library_roundtrip(tmp_path):
    clock = Clock(0.0)
    rec = MotionRecorder(clock)
    rec.start(0, 0)
    for i in range(1, 6):
        clock.t = i * 0.1
        rec.add(i * 10, -i)
    clock.t = 0.12 + 0.5  # Stop later: the last pose is held
    frames = rec.stop()
    assert frames[0] == Keyframe(0.0, 0, 0) and frames[-1].t == pytest.approx(0.62)
    lib = MotionLibrary(tmp_path / "motions")
    assert lib.save("Chào hỏi 1", frames) == "Chào hỏi 1"
    assert [m["name"] for m in lib.list()] == ["Chào hỏi 1"]
    loaded = lib.load("Chào hỏi 1", 30, 45)
    assert max(f.pan for f in loaded) == 30  # Clamped to the current limit on load
    assert lib.delete("Chào hỏi 1") and lib.list() == []
    assert not lib.delete("Chào hỏi 1")


def test_names_cannot_escape_the_folder(tmp_path):
    lib = MotionLibrary(tmp_path / "m")
    saved = lib.save("../../x", [Keyframe(0, 0, 0), Keyframe(1, 1, 1)])
    assert (tmp_path / "m" / f"{saved}.json").is_file()
    for bad in ("", "..", "///", "   "):
        with pytest.raises(ValueError):
            clean_name(bad)


def test_validate_frames_rejects_bad_data():
    with pytest.raises(ValueError):
        validate_frames([], 90, 45)
    with pytest.raises(ValueError):
        validate_frames([[0, "x", 1]], 90, 45)
    frames = validate_frames([[2, 0, 0], [0, 200, -200], [999, 1, 1]], 90, 45)
    assert frames == [Keyframe(0, 90, -45), Keyframe(2, 0, 0)]


def test_player_stops_when_web_takes_over():
    controller = FakeController()
    arb = ControlArbiter(None, 5.0)
    player = MotionPlayer(controller, arb)
    frames = [Keyframe(0, 0, 0), Keyframe(10, 80, 0)]
    assert player.play("long", frames, SOURCE_VOICE)
    time.sleep(0.15)
    arb.request(SOURCE_WEB)
    assert wait_until(lambda: player.playing is None)
    # A voice motion cannot start while the web holds the head
    assert not player.play("again", frames, SOURCE_VOICE)


# ----- Backend -----

def make_backend(tmp_path, **kw):
    controller = FakeController()
    tracker = FakeTracker()
    hooks = AppHooks(set_emotion=kw.get("emotion"), send_text=kw.get("text"))
    state = AppState(emotions=("neutral", "happy"), face_available=True, text_supported=True)
    backend = RobotBackend(
        controller, tracker, library=MotionLibrary(tmp_path / "motions"), app_state=state, hooks=hooks
    )
    return backend, controller, tracker


def run(backend, obj):
    return backend.handle(parse_command(json.dumps(obj), backend.pan_limit, backend.tilt_limit, backend.app.emotions))


def test_backend_moves_and_pauses_tracking(tmp_path):
    backend, controller, tracker = make_backend(tmp_path)
    assert run(backend, {"type": "set_angles", "pan": 20, "tilt": 5})["ok"]
    assert controller.angles == (20.0, 5.0)
    assert tracker.holds and backend.arbiter.owner == SOURCE_WEB
    assert run(backend, {"type": "center"})["ok"] and controller.angles == (0.0, 0.0)
    run(backend, {"type": "tracking", "enabled": False})
    assert tracker.enabled is False
    run(backend, {"type": "tracking_params", "kp_pan": 3, "deadband": 0.2})
    assert controller.settings.kp_pan == 3 and controller.settings.deadband == 0.2
    snap = backend.snapshot()
    assert snap["servo"]["target"] == {"pan": 0.0, "tilt": 0.0}
    assert snap["tracking"]["kp_pan"] == 3 and snap["tracking"]["enabled"] is False
    json.dumps(snap)  # Must be serializable


def test_backend_records_web_moves(tmp_path):
    backend, controller, _ = make_backend(tmp_path)
    run(backend, {"type": "record_start"})
    for i in range(5):
        run(backend, {"type": "set_angles", "pan": i * 5, "tilt": 0})
        time.sleep(0.06)
    result = run(backend, {"type": "record_stop", "name": "thử"})
    assert result["ok"], result
    assert [m["name"] for m in backend.snapshot()["motion"]["library"]] == ["thử"]
    run(backend, {"type": "center"})
    backend.arbiter = ControlArbiter(None, 0.0)  # Let playback start right away
    backend.player = MotionPlayer(controller, backend.arbiter)
    assert run(backend, {"type": "play_motion", "name": "thử"})["ok"]
    assert wait_until(lambda: backend.player.playing is None)
    assert controller.angles[0] == pytest.approx(20.0)
    assert run(backend, {"type": "delete_motion", "name": "thử"})["ok"]
    assert not run(backend, {"type": "play_motion", "name": "thử"})["ok"]


def test_backend_face_and_text_hooks(tmp_path):
    seen = []
    backend, _, _ = make_backend(tmp_path, emotion=seen.append, text=seen.append)
    assert run(backend, {"type": "emotion", "name": "happy"})["ok"]
    assert run(backend, {"type": "send_text", "text": "xin chào"})["ok"]
    assert seen == ["happy", "xin chào"]
    assert backend.app.snapshot()["emotion"] == "happy"
    assert backend.app.chat_since(0)[-1]["role"] == "user"
    backend.app.always_listen = True
    assert not run(backend, {"type": "auto_mode", "enabled": False})["ok"]


def test_backend_without_servo(tmp_path):
    backend = RobotBackend(None, None, library=MotionLibrary(tmp_path))
    with pytest.raises(CommandError):
        run(backend, {"type": "set_angles", "pan": 1})
    assert backend.snapshot()["servo"] == {"enabled": False, "connected": False}


# ----- Controller: reported and estimated angles -----

class EchoSerial:
    def __init__(self):
        self.rx = bytearray(b"READY servo_bridge\n")
        self.lock = threading.Lock()

    @property
    def in_waiting(self):
        with self.lock:
            return len(self.rx)

    def read(self, n):
        with self.lock:
            data, self.rx = bytes(self.rx[:n]), self.rx[n:]
        return data

    def write(self, data):
        with self.lock:
            self.rx += b"OK " + data.strip() + b"\n"

    def close(self):
        pass


def test_controller_reports_acked_and_ramps_estimate():
    settings = ServoSettings(enabled=True, tilt_invert=True)
    ctl = ServoController(settings, opener=lambda p, b: EchoSerial(), port_finder=lambda c: "X")
    assert ctl.estimated_angles() is None
    ctl.start()
    try:
        assert wait_until(lambda: ctl.connected)
        ctl.set_angles(60, 20)
        assert wait_until(lambda: ctl.acked_angles == (60.0, 20.0))
        first = ctl.estimated_angles()
        assert first[0] < 60  # Firmware ramps at 150 servo degrees per second
        assert wait_until(lambda: ctl.estimated_angles() == (60.0, 20.0), 2.0)
        assert ctl.head_angles(*ctl.servo_angles(-33, 12)) == (-33.0, 12.0)
    finally:
        ctl.shutdown()


# ----- Server (real aiohttp on a free port) -----

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_websocket_roundtrip(tmp_path):
    import aiohttp

    from src.utils.resource_finder import get_app_root
    from src.web_panel.server import WebPanelServer

    backend, controller, _ = make_backend(tmp_path)
    port = free_port()
    server = WebPanelServer(backend, WebSettings(enabled=True, host="127.0.0.1", port=port), get_app_root() / "web_assets")
    assert server.start()

    async def scenario():
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/") as r:
                assert r.status == 200 and "Xiaozhi" in await r.text()
            async with http.get(f"http://127.0.0.1:{port}/static/xiaozhi_robot.glb") as r:
                assert r.status == 200 and (await r.read())[:4] == b"glTF"
            async with http.ws_connect(f"http://127.0.0.1:{port}/ws", headers={"Origin": "http://evil.example"}) as ws:
                pass
    with pytest.raises(Exception):
        asyncio.run(scenario())  # Cross-origin WebSocket refused

    async def scenario_ok():
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                hello = await ws.receive_json(timeout=3)
                assert hello["type"] == "hello"
                state = await ws.receive_json(timeout=3)
                assert state["type"] == "state"
                await ws.send_json({"type": "set_angles", "pan": 15, "tilt": -5, "id": 1})
                while True:
                    msg = await ws.receive_json(timeout=3)
                    if msg["type"] == "result":
                        assert msg["ok"] and msg["id"] == 1
                        break
                await ws.send_str("garbage")
                while True:
                    msg = await ws.receive_json(timeout=3)
                    if msg["type"] == "result":
                        assert not msg["ok"]
                        break
                # Flooding is rate limited
                for _ in range(80):
                    await ws.send_json({"type": "ping"})
                limited = False
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not limited:
                    msg = await ws.receive_json(timeout=3)
                    limited = msg.get("rate_limited", False)
                assert limited

    try:
        asyncio.run(scenario_ok())
        assert controller.angles == (15.0, -5.0)
    finally:
        server.stop()


def test_server_password(tmp_path):
    import aiohttp

    from src.utils.resource_finder import get_app_root
    from src.web_panel.server import WebPanelServer

    backend, _, _ = make_backend(tmp_path)
    port = free_port()
    settings = WebSettings(enabled=True, host="127.0.0.1", port=port, password="bí mật")
    server = WebPanelServer(backend, settings, get_app_root() / "web_assets")
    assert server.start()

    async def scenario():
        base = f"http://127.0.0.1:{port}"
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            async with http.get(f"{base}/api/state") as r:
                assert r.status == 401
            async with http.get(f"{base}/", allow_redirects=False) as r:
                assert r.status == 302 and r.headers["Location"] == "/login"
            async with http.post(f"{base}/login", data={"password": "bí mật"}, allow_redirects=False) as r:
                assert r.status == 302 and r.headers["Location"] == "/"
            async with http.get(f"{base}/api/state") as r:
                assert r.status == 200

    try:
        asyncio.run(scenario())
    finally:
        server.stop()
