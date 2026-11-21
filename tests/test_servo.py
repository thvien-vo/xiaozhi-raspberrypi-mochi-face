"""Servo head: angle mapping, serial thread with a fake port, tracking and MCP tools."""

import json
import threading
import time

import pytest

from src.mcp.tools.servo import register_servo_tools
from src.servo import servo_controller as sc
from src.servo.servo_controller import ServoController
from src.servo.settings import ServoSettings, load_servo_settings
from src.servo.tracker import HeadTracker, target_offset
from src.utils.config_manager import ConfigManager
from src.vision.detection_service import DetectionResult
from src.vision.detector import Detection


class FakeSerial:
    """Echoes OK like the firmware; can be 'unplugged'."""

    def __init__(self, boot=b"READY servo_bridge\n"):
        self.rx = bytearray(boot)
        self.written: list[str] = []
        self.unplugged = False
        self.closed = False
        self.lock = threading.Lock()

    @property
    def in_waiting(self):
        if self.unplugged:
            raise OSError("device gone")
        with self.lock:
            return len(self.rx)

    def read(self, n):
        with self.lock:
            data, self.rx = bytes(self.rx[:n]), self.rx[n:]
        return data

    def write(self, data):
        if self.unplugged:
            raise OSError("device gone")
        line = data.decode().strip()
        self.written.append(line)
        with self.lock:
            self.rx += f"OK {line}\n".encode() if line != "bad" else b"ERR x\n"

    def close(self):
        self.closed = True


def wait_until(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_defaults_match_settings():
    s = load_servo_settings()
    assert s.enabled is False  # Off unless a device preset turns it on
    assert (s.pan_limit, s.tilt_limit, s.manual_hold_sec) == (90, 45, 5)
    assert ConfigManager.DEFAULT_CONFIG["SERVO"]["PORT"] == ""


def test_angle_mapping_invert_offset_limits():
    c = ServoController(ServoSettings())
    assert c.servo_angles(0, 0) == (90, 90)
    assert c.servo_angles(30, 10) == (120, 110)  # Tilt gear 1:2
    c.settings = ServoSettings(pan_invert=True, tilt_invert=True, pan_offset=3, tilt_offset=-2)
    assert c.servo_angles(30, 10) == (63, 68)
    c.settings = ServoSettings(pan_limit=40, tilt_limit=20)
    assert c.set_pan_deg(80) == (40, 0)
    assert c.set_tilt_deg(-90) == (40, -20)
    assert c.nudge(-100, 0) == (-40, -20)
    assert c.center() == (0, 0)


def test_thread_connects_sends_on_change_and_reconnects():
    ports = []

    def opener(port, baud):
        ports.append(FakeSerial())
        return ports[-1]

    c = ServoController(ServoSettings(), opener=opener, port_finder=lambda _p: "COM9")
    c.start()
    try:
        assert wait_until(lambda: c.connected and ports[0].written == ["90 90"])
        c.set_pan_deg(10)
        assert wait_until(lambda: ports[0].written[-1] == "100 90")
        time.sleep(0.15)
        assert ports[0].written.count("100 90") == 1  # Only sends when the angle changes

        ports[0].unplugged = True
        assert wait_until(lambda: len(ports) == 2 and c.connected, 5.0)
        assert wait_until(lambda: ports[1].written[:1] == ["100 90"])  # Target restored
    finally:
        c.shutdown()
    assert ports[1].written[-1] == "90 90" and ports[1].closed


def test_rate_limit():
    ser = FakeSerial(boot=b"")
    c = ServoController(ServoSettings(), opener=lambda p, b: ser, port_finder=lambda _p: "COM9")
    c.start()
    try:
        assert wait_until(lambda: c.connected)
        start = time.monotonic()
        while time.monotonic() - start < 0.5:
            c.nudge(0.5, 0)
            time.sleep(0.002)
    finally:
        c.shutdown()
    assert len(ser.written) <= 0.5 / sc.MIN_SEND_INTERVAL_S + 4


def test_missing_board_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(sc, "RECONNECT_S", 0.01)
    calls = []
    c = ServoController(ServoSettings(), port_finder=lambda _p: calls.append(1))
    warnings = []
    monkeypatch.setattr(sc.logger, "warning", lambda *a, **k: warnings.append(a))
    c.start()
    assert wait_until(lambda: len(calls) > 5)
    c.shutdown()
    assert len(warnings) == 1 and not c.connected


def result(*boxes, w=640, h=480, ts=None, label="person"):
    dets = tuple(Detection(label, 0.9, b) for b in boxes)
    return DetectionResult(dets, w, h, time.monotonic() if ts is None else ts, 10.0)


def test_target_offset_picks_largest_person_and_unmirrors():
    r = result((0, 0, 40, 40), (440, 0, 640, 480))
    r = DetectionResult(r.detections + (Detection("cup", 0.9, (0, 0, 640, 480)),), 640, 480, 0, 1)
    x, y = target_offset(r)
    assert x == pytest.approx(0.69, abs=0.01) and y == pytest.approx(-0.6)
    assert target_offset(r, "horizontal")[0] == pytest.approx(-0.69, abs=0.01)
    assert target_offset(result(label="cup")) is None


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_tracker(**kw):
    clock = Clock()
    ctrl = ServoController(ServoSettings())
    latest = {"r": None}
    tracker = HeadTracker(ctrl, lambda: latest["r"], ServoSettings(**kw), clock=clock)
    return tracker, ctrl, latest, clock


def test_tracker_turns_toward_person_and_respects_deadband():
    tracker, ctrl, latest, clock = make_tracker(ema_alpha=1.0)
    # Person on the right of the raw image: robot turns right (pan -); above: looks up
    latest["r"] = result((560, 0, 640, 100), ts=clock.t)
    tracker.tick()
    pan, tilt = ctrl.angles
    assert pan == pytest.approx(-6.0) and tilt > 0  # Step limited to MAX_STEP_DEG
    tracker.tick()  # Same result: no second step
    assert ctrl.angles[0] == pytest.approx(-6.0)

    clock.t += 0.25
    latest["r"] = result((300, 200, 340, 400), ts=clock.t)  # Centered: inside dead band
    before = ctrl.angles
    tracker.tick()
    assert ctrl.angles == before


def test_tracker_returns_to_center_after_losing_target_and_holds():
    tracker, ctrl, latest, clock = make_tracker(lost_timeout_sec=3.0, manual_hold_sec=5.0)
    ctrl.set_angles(20, 10)
    tracker.tick()
    clock.t += 2.0
    tracker.tick()
    assert ctrl.angles == (20, 10)  # Not yet
    for _ in range(30):
        clock.t += 0.05
        tracker.tick()
    assert ctrl.angles[0] < 20 and ctrl.angles[0] > 0  # Drifting, not jumping
    for _ in range(100):
        clock.t += 0.05
        tracker.tick()
    assert ctrl.angles == (0, 0)

    ctrl.set_angles(30, 0)
    tracker.hold()
    latest["r"] = result((560, 0, 640, 100), ts=clock.t)
    clock.t += 4.0
    tracker.tick()
    assert ctrl.angles == (30, 0)  # Held after a voice command
    clock.t += 1.5
    latest["r"] = result((560, 0, 640, 100), ts=clock.t)
    tracker.tick()
    assert ctrl.angles[0] < 30  # Tracking again


def test_mcp_tools_move_head_and_pause_tracking():
    tools = {}
    tracker, ctrl, latest, clock = make_tracker()
    register_servo_tools(lambda t: tools.__setitem__(t.name, t), ctrl, tracker)
    assert set(tools) == {
        "self.head.turn_left", "self.head.turn_right", "self.head.look_up",
        "self.head.look_down", "self.head.look_straight", "self.head.set_tracking",
    }
    import asyncio

    def call(name, args=None):
        out = json.loads(asyncio.run(tools[name].call(args or {})))
        return json.loads(out["content"][0]["text"])

    r = call("self.head.turn_left")
    assert r["pan_deg"] == 20 and tracker.holding
    assert call("self.head.turn_right", {"degrees": 50})["pan_deg"] == -30
    assert call("self.head.look_up", {"degrees": 60})["tilt_deg"] == 45
    assert call("self.head.look_down", {"degrees": 5})["tilt_deg"] == 40
    assert call("self.head.look_straight")["pan_deg"] == 0
    assert call("self.head.set_tracking", {"enabled": False})["tracking"] is False
    assert call("self.head.set_tracking", {"enabled": True})["tracking"] is True
    assert "chưa kết nối" in r["message"]
