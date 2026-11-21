#!/usr/bin/env python3
"""Talk to the servo board directly: a few small moves around center, then back.

    .venv/bin/python scripts/servo_test.py                 # /dev/yolo_uno or VID 0x303a
    .venv/bin/python scripts/servo_test.py --port /dev/ttyACM0
    .venv/bin/python scripts/servo_test.py --reopen 5      # check that opening does not reset it

Stop the app first (systemctl --user stop xiaozhi): only one program can hold the port.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.servo.servo_controller import find_port, open_serial  # noqa: E402

# Servo angles, all within 20 degrees of center
MOVES = [(90, 90), (105, 90), (75, 90), (90, 100), (90, 80), (90, 90)]


def read_lines(ser, seconds: float) -> list[str]:
    deadline = time.monotonic() + seconds
    buf = b""
    while time.monotonic() < deadline:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
        time.sleep(0.02)
    return [line.strip() for line in buf.decode("utf-8", "replace").splitlines() if line.strip()]


def send(ser, pan: int, tilt: int, wait: float) -> list[str]:
    ser.write(f"{pan} {tilt}\n".encode("ascii"))
    lines = read_lines(ser, wait)
    print(f"-> {pan} {tilt}   <- {' | '.join(lines) or '(no reply)'}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", default="", help="Serial port (default: auto)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--wait", type=float, default=1.0, help="Seconds after each move")
    parser.add_argument("--reopen", type=int, default=0, help="Open/close N extra times, count READY")
    args = parser.parse_args()

    port = find_port(args.port)
    if port is None:
        print("No servo board found (/dev/yolo_uno, USB VID 0x303a)", file=sys.stderr)
        return 1
    print(f"Port: {port}")

    ser = open_serial(port, args.baud)
    boot = read_lines(ser, 2.0)
    print(f"After open: {' | '.join(boot) or '(nothing, board did not reset)'}")
    ok = 0
    try:
        for pan, tilt in MOVES:
            lines = send(ser, pan, tilt, args.wait)
            ok += any(line == f"OK {pan} {tilt}" for line in lines)
    finally:
        ser.close()

    resets = sum("READY" in line for line in boot)
    for i in range(args.reopen):
        ser = open_serial(port, args.baud)
        lines = read_lines(ser, 1.5)
        lines += send(ser, 90, 90, 0.3)
        ser.close()
        r = sum("READY" in line for line in lines)
        resets += r
        print(f"reopen {i + 1}: {'RESET (READY seen)' if r else 'no reset'}")
        time.sleep(0.3)

    print(f"OK replies: {ok}/{len(MOVES)}, resets seen: {resets}")
    return 0 if ok == len(MOVES) else 2


if __name__ == "__main__":
    sys.exit(main())
