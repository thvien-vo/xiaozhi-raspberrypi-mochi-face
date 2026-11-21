#!/usr/bin/env python3
"""Find which way the servos turn, using the head-mounted webcam.

Moves each servo +15 degrees (servo angle) from center, measures how the scene shifted
with cv2.phaseCorrelate and prints the SERVO.PAN_INVERT / TILT_INVERT that make
"pan +" turn to the robot's left and "tilt +" look up. Stop the app first.

    .venv/bin/python scripts/servo_calibrate.py --camera /dev/video0

Robot turns to its left -> the scene moves right in the raw camera image (dx > 0).
Robot looks up -> the scene moves down (dy > 0).
"""

import argparse
import glob
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.servo_test import read_lines  # noqa: E402
from src.servo.servo_controller import find_port, open_serial  # noqa: E402

STEP = 15
MIN_SHIFT_PX = 3.0


def grab(cap, discard: int = 8) -> np.ndarray:
    frame = None
    for _ in range(discard):  # Flush buffered frames
        ok, frame = cap.read()
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError("camera returned no frame")
    return np.float32(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))


def measure(cap, ser, pan: int, tilt: int, settle: float) -> tuple[float, float, float]:
    ser.write(b"90 90\n")
    time.sleep(settle)
    read_lines(ser, 0.1)
    before = grab(cap)
    ser.write(f"{pan} {tilt}\n".encode())
    time.sleep(settle)
    reply = read_lines(ser, 0.1)
    after = grab(cap)
    window = cv2.createHanningWindow(before.shape[::-1], cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(before, after, window)
    print(f"servo {pan} {tilt}: reply {reply}, shift dx={dx:+.1f}px dy={dy:+.1f}px (response {response:.2f})")
    return dx, dy, response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", default="", help="Video device (default: first by-id index0)")
    parser.add_argument("--port", default="")
    parser.add_argument("--settle", type=float, default=1.5)
    args = parser.parse_args()

    device = args.camera or next(iter(sorted(glob.glob("/dev/v4l/by-id/*-video-index0"))), "/dev/video0")
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not cap.isOpened():
        print(f"Cannot open camera {device}", file=sys.stderr)
        return 1
    port = find_port(args.port)
    if port is None:
        print("No servo board found", file=sys.stderr)
        return 1
    ser = open_serial(port, 115200)
    read_lines(ser, 1.0)
    try:
        pan_dx, pan_dy, _ = measure(cap, ser, 90 + STEP, 90, args.settle)
        tilt_dx, tilt_dy, _ = measure(cap, ser, 90, 90 + STEP, args.settle)
    finally:
        ser.write(b"90 90\n")
        time.sleep(1.0)
        ser.close()
        cap.release()

    result = 0
    if abs(pan_dx) < MIN_SHIFT_PX:
        print("PAN: the image did not move; check the servo power (5 V adapter) and GND")
        result = 2
    else:
        # Servo + moved the scene right -> servo + is the robot's left -> no inversion
        print(f"PAN_INVERT = {pan_dx < 0}  (servo +{STEP} moved the scene {'right' if pan_dx > 0 else 'left'})")
    if abs(tilt_dy) < MIN_SHIFT_PX:
        print("TILT: the image did not move; check the servo power (5 V adapter) and GND")
        result = 2
    else:
        print(f"TILT_INVERT = {tilt_dy < 0}  (servo +{STEP} moved the scene {'down' if tilt_dy > 0 else 'up'})")
    return result


if __name__ == "__main__":
    sys.exit(main())
