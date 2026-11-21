#!/usr/bin/env python3
"""Run the web panel alone, with a simulated servo board, camera and app.

    python scripts/web_panel_dev.py [--port 8080] [--password secret] [--https]

The servo board is a fake serial port that answers like firmware/servo_bridge; the
camera draws a moving "person" with a YOLO-style box; tracking follows it; the face,
listening state and chat are simulated (a sent text gets an echo reply). Recordings go
to a temporary folder unless --data-dir is given. Nothing touches real hardware.
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
import threading
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.servo.motion import ControlArbiter, MotionLibrary, MotionPlayer  # noqa: E402
from src.servo.servo_controller import ServoController  # noqa: E402
from src.servo.settings import ServoSettings  # noqa: E402
from src.servo.tracker import HeadTracker  # noqa: E402
from src.vision.detection_service import DetectionResult  # noqa: E402
from src.vision.detector import Detection  # noqa: E402
from src.web_panel.backend import AppHooks, AppState, RobotBackend  # noqa: E402
from src.web_panel.camera import CameraFeed  # noqa: E402
from src.web_panel.server import WebPanelServer  # noqa: E402
from src.web_panel.settings import WebSettings  # noqa: E402

EMOTIONS = (
    "angry", "confident", "confused", "cool", "crying", "delicious", "embarrassed", "funny",
    "happy", "kissy", "laughing", "loving", "neutral", "relaxed", "sad", "shocked", "silly",
    "sleepy", "surprised", "thinking", "winking",
)


class FakeBoard:
    """A serial port that behaves like servo_bridge: READY on open, OK <p> <t> per line."""

    def __init__(self) -> None:
        self.rx = bytearray(b"READY servo_bridge\n")
        self.lock = threading.Lock()
        self.servo = [90, 90]

    @property
    def in_waiting(self) -> int:
        with self.lock:
            return len(self.rx)

    def read(self, n: int) -> bytes:
        with self.lock:
            data, self.rx = bytes(self.rx[:n]), self.rx[n:]
        return data

    def write(self, data: bytes) -> None:
        p, t = (int(v) for v in data.decode().split())
        self.servo = [max(0, min(180, p)), max(0, min(180, t))]
        with self.lock:
            self.rx += f"OK {self.servo[0]} {self.servo[1]}\n".encode()

    def close(self) -> None:
        pass


class FakeWorld:
    """A person walking left and right in front of the robot; the 'camera' is on the head,
    so where the person appears depends on the head's real angle."""

    W, H = 640, 480
    HFOV, VFOV = 70.0, 52.0

    def __init__(self, controller: ServoController) -> None:
        self.controller = controller
        self.seq = 0
        self.lock = threading.Lock()
        self.result: DetectionResult | None = None
        self.start = time.monotonic()

    def person_angles(self) -> tuple[float, float]:
        t = time.monotonic() - self.start
        return 35 * math.sin(t / 6.0), 5 + 6 * math.sin(t / 3.7)

    def frame(self) -> np.ndarray:
        head = self.controller.estimated_angles() or self.controller.angles
        img = np.zeros((self.H, self.W, 3), np.uint8)
        # Background: wall with vertical stripes that slide as the head turns
        img[:] = (58, 52, 46)
        shift = head[0] * self.W / self.HFOV
        for i in range(-30, 30):
            x = int(self.W / 2 + i * 60 + shift) % (self.W + 60) - 30
            cv2.line(img, (x, 0), (x, self.H), (72, 66, 60), 2)
        horizon = int(self.H * 0.62 + head[1] * self.H / self.VFOV)
        cv2.rectangle(img, (0, horizon), (self.W, self.H), (40, 44, 50), -1)
        pa, ta = self.person_angles()
        cx = self.W / 2 - (pa - head[0]) * self.W / self.HFOV
        cy = self.H / 2 - (ta - head[1]) * self.H / self.VFOV
        box = (cx - 70, cy - 60, cx + 70, cy + 260)
        cv2.ellipse(img, (int(cx), int(cy)), (42, 52), 0, 0, 360, (170, 190, 220), -1)
        cv2.rectangle(img, (int(cx - 70), int(cy + 55)), (int(cx + 70), int(cy + 260)), (80, 120, 200), -1)
        cv2.putText(img, "DEV - camera gia lap", (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
        x1, y1, x2, y2 = (max(0.0, box[0]), max(0.0, box[1]), min(self.W - 1.0, box[2]), min(self.H - 1.0, box[3]))
        dets = (Detection("person", 0.91, (x1, y1, x2, y2)),) if x2 - x1 > 20 and y2 - y1 > 20 else ()
        with self.lock:
            self.result = DetectionResult(dets, self.W, self.H, time.monotonic(), 12.0)
        return img

    def get_frame(self, after: int, timeout: float):
        time.sleep(1 / 15)
        self.seq += 1
        return self.frame(), self.seq

    def latest(self):
        with self.lock:
            return self.result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--password", default="")
    ap.add_argument("--https", action="store_true", help="Also serve HTTPS on --https-port")
    ap.add_argument("--https-port", type=int, default=8443)
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--no-servo", action="store_true", help="Run as if SERVO.ENABLED were false")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="xz-web-"))
    settings = ServoSettings(enabled=True, tracking_enabled=False)
    board = FakeBoard()
    controller = None if args.no_servo else ServoController(
        settings, opener=lambda port, baud: board, port_finder=lambda configured: "FAKE"
    )
    tracker = arbiter = player = camera = None
    if controller is not None:
        world = FakeWorld(controller)
        tracker = HeadTracker(controller, world.latest, settings)
        arbiter = ControlArbiter(tracker, settings.manual_hold_sec)
        player = MotionPlayer(controller, arbiter)
        camera = CameraFeed(world.get_frame, lambda: (world.latest() or DetectionResult((), 1, 1, 0, 0)).detections, 8)
        controller.start()
        tracker.start()

    state = AppState(emotions=EMOTIONS, auto_mode=False, face_available=True, text_supported=True, protocol_connected=True)

    def fake_reply(text: str) -> None:
        def run():
            state.update(device_state="listening")
            time.sleep(0.6)
            state.update(device_state="speaking", emotion="happy")
            state.add_chat("robot", f"(giả lập) Mình nghe bạn nói: {text}")
            time.sleep(1.5)
            state.update(device_state="idle", emotion="neutral")
        threading.Thread(target=run, daemon=True).start()

    hooks = AppHooks(
        set_emotion=lambda name: print(f"[dev] face emotion -> {name}"),
        set_auto_mode=lambda on: state.update(auto_mode=on),
        send_text=fake_reply,
        face_wake=lambda: print("[dev] face wake"),
    )
    backend = RobotBackend(
        controller, tracker, arbiter=arbiter, player=player,
        library=MotionLibrary(data_dir / "motions"), app_state=state, hooks=hooks, camera=camera,
        save_setting=lambda path, value: print(f"[dev] save {path} = {value}"),
    )
    web = WebSettings(
        enabled=True, host=args.host, port=args.port, password=args.password,
        camera_stream_fps=8, https_enabled=args.https, https_port=args.https_port,
    )
    server = WebPanelServer(backend, web, ROOT / "web_assets", cert_dir=data_dir / "web_cert")
    if not server.start():
        print("Server failed:", server.start_error, file=sys.stderr)
        return 1
    print(f"Web panel (dev, fake servo): http://{args.host}:{args.port}/  data: {data_dir}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        if tracker:
            tracker.stop()
        if controller:
            controller.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
