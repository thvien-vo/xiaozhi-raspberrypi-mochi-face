"""Pan/tilt head over USB serial to a Yolo UNO (ESP32-S3) running firmware/servo_bridge.

Protocol: one line "<pan> <tilt>\\n" with servo angles 0..180; the board answers
"OK <pan> <tilt>" or "ERR ...", prints "READY servo_bridge" on boot and moves smoothly
by itself. All serial I/O happens in one background thread, so callers (asyncio, Qt,
the tracker) only set targets and never block.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

from src.logging import get_logger
from src.servo.settings import (
    DEFAULT_PORT,
    ESPRESSIF_VID,
    TILT_GEAR_RATIO,
    ServoSettings,
)

logger = get_logger()

SERVO_CENTER = 90
SERVO_MIN, SERVO_MAX = 0, 180
MIN_SEND_INTERVAL_S = 0.05  # At most 20 commands per second
READY_WAIT_S = 2.0
FIRST_REPLY_WAIT_S = 0.5
RECONNECT_S = 2.0
SHUTDOWN_SETTLE_S = 1.0
_LOOP_S = 0.02


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def find_port(configured: str = "") -> str | None:
    """Configured port, else /dev/yolo_uno, else the first port with Espressif's VID."""
    if configured:
        # COM ports and similar are not file paths; trust them as given
        if not configured.startswith("/") or os.path.exists(configured):
            return configured
    if os.path.exists(DEFAULT_PORT):
        return DEFAULT_PORT
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for port in sorted(list_ports.comports(), key=lambda p: p.device):
        if port.vid == ESPRESSIF_VID:
            return port.device
    return None


def open_serial(port: str, baud: int):
    """Open the port without resetting the ESP32-S3.

    Its USB Serial/JTAG resets the chip while RTS is asserted with DTR released. Linux
    asserts both on open, and pyserial's open() then releases DTR before RTS, passing
    through exactly that state (measured: every open printed a boot log and READY). So
    open() is told to leave DTR alone (dsrdtr, which Linux ignores for flow control),
    releases RTS, and DTR is released afterwards: (1,1) -> DTR 1 RTS 0 -> (0,0).
    """
    import serial

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 0  # Non-blocking reads
    ser.write_timeout = 0.5
    if os.name == "posix":
        ser.dsrdtr = True  # Skip the DTR update inside open(); on Windows it is a handshake
    ser.rts = False
    ser.dtr = False
    ser.open()
    ser.dtr = False
    return ser


class ServoController:
    """Head angles in, servo commands out. Thread-safe; never raises to callers."""

    def __init__(
        self,
        settings: ServoSettings,
        *,
        opener: Callable[[str, int], object] = open_serial,
        port_finder: Callable[[str], str | None] = find_port,
    ) -> None:
        self.settings = settings
        self._opener = opener
        self._port_finder = port_finder
        self._lock = threading.Lock()
        self._pan = 0.0
        self._tilt = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._serial = None
        self._port: str | None = None
        self._last_sent: tuple[int, int] | None = None
        self._last_send_time = 0.0
        self._last_reply: str | None = None
        self._rx = bytearray()
        self._warned_missing = False

    # ----- Angle API (head degrees) -----

    @property
    def angles(self) -> tuple[float, float]:
        with self._lock:
            return self._pan, self._tilt

    @property
    def connected(self) -> bool:
        return self._serial is not None

    @property
    def port(self) -> str | None:
        return self._port

    @property
    def last_reply(self) -> str | None:
        return self._last_reply

    def set_angles(self, pan: float | None = None, tilt: float | None = None) -> tuple[float, float]:
        s = self.settings
        with self._lock:
            if pan is not None:
                self._pan = _clamp(float(pan), -s.pan_limit, s.pan_limit)
            if tilt is not None:
                self._tilt = _clamp(float(tilt), -s.tilt_limit, s.tilt_limit)
            result = (self._pan, self._tilt)
        self._wake.set()
        return result

    def set_pan_deg(self, pan: float) -> tuple[float, float]:
        return self.set_angles(pan=pan)

    def set_tilt_deg(self, tilt: float) -> tuple[float, float]:
        return self.set_angles(tilt=tilt)

    def nudge(self, dpan: float = 0.0, dtilt: float = 0.0) -> tuple[float, float]:
        with self._lock:
            pan, tilt = self._pan + dpan, self._tilt + dtilt
        return self.set_angles(pan, tilt)

    def center(self) -> tuple[float, float]:
        return self.set_angles(0.0, 0.0)

    def servo_angles(self, pan: float | None = None, tilt: float | None = None) -> tuple[int, int]:
        """Servo command for head angles (the current target by default)."""
        s = self.settings
        if pan is None or tilt is None:
            cur_pan, cur_tilt = self.angles
            pan = cur_pan if pan is None else pan
            tilt = cur_tilt if tilt is None else tilt
        pan_sign = -1.0 if s.pan_invert else 1.0
        tilt_sign = -1.0 if s.tilt_invert else 1.0
        pan_servo = SERVO_CENTER + s.pan_offset + pan_sign * pan
        tilt_servo = SERVO_CENTER + s.tilt_offset + tilt_sign * tilt * TILT_GEAR_RATIO
        return (
            int(round(_clamp(pan_servo, SERVO_MIN, SERVO_MAX))),
            int(round(_clamp(tilt_servo, SERVO_MIN, SERVO_MAX))),
        )

    # ----- Lifecycle -----

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="servo-serial", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        """Center the head, give it time to get there, then close the port. Blocking."""
        if self._thread is None:
            return
        target = self.servo_angles(0.0, 0.0)
        self.center()
        if self.connected:
            deadline = time.monotonic() + 0.5
            while self._last_sent != target and self.connected and time.monotonic() < deadline:
                time.sleep(_LOOP_S)
            if self._last_sent == target:
                time.sleep(SHUTDOWN_SETTLE_S)
        self._stop.set()
        self._wake.set()
        self._thread.join(3.0)
        self._thread = None
        self._close()
        logger.info("Servo controller stopped")

    # ----- Serial thread -----

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._serial is None and not self._connect():
                self._stop.wait(RECONNECT_S)
                continue
            try:
                self._poll_replies()
                self._send_if_due()
            except Exception as e:  # SerialException, OSError on unplug
                self._drop(e)
                continue
            self._wake.wait(_LOOP_S)
            self._wake.clear()

    def _connect(self) -> bool:
        port = self._port_finder(self.settings.port)
        if port is None:
            if not self._warned_missing:
                self._warned_missing = True
                logger.warning(
                    "Servo board not found (SERVO.PORT=%r, %s, USB VID 0x%04x); "
                    "the head will not move until it is plugged in",
                    self.settings.port,
                    DEFAULT_PORT,
                    ESPRESSIF_VID,
                )
            return False
        try:
            ser = self._opener(port, self.settings.baud)
        except Exception as e:
            if not self._warned_missing:
                self._warned_missing = True
                logger.warning("Cannot open servo port %s: %s", port, e)
            return False

        self._serial = ser
        self._port = port
        self._rx.clear()
        try:
            ready = self._wait_for(lambda line: line.startswith("READY"), READY_WAIT_S)
            self._last_sent = None
            self._send_if_due(force=True)
            reply = self._wait_for(lambda line: line.startswith(("OK", "ERR")), FIRST_REPLY_WAIT_S)
        except Exception as e:
            self._drop(e)
            return False
        self._warned_missing = False
        logger.info(
            "Servo board connected on %s (%s, first reply: %s)",
            port,
            "READY seen" if ready else "no READY, already running",
            reply or "none",
        )
        return True

    def _wait_for(self, match: Callable[[str], bool], timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stop.is_set():
            for line in self._read_lines():
                if match(line):
                    return line
            time.sleep(_LOOP_S)
        return None

    def _read_lines(self) -> list[str]:
        ser = self._serial
        waiting = ser.in_waiting
        if waiting:
            self._rx += ser.read(waiting)
        lines = []
        while b"\n" in self._rx:
            raw, _, rest = self._rx.partition(b"\n")
            self._rx = bytearray(rest)
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            self._handle_line(line)
            lines.append(line)
        if len(self._rx) > 256:  # Garbage without newlines
            self._rx.clear()
        return lines

    def _handle_line(self, line: str) -> None:
        self._last_reply = line
        if line.startswith("ERR"):
            logger.warning("Servo board error: %s", line)
        elif line.startswith("READY"):
            logger.info("Servo board (re)started: %s", line)
            self._last_sent = None  # It went back to 90 90; resend the target
        else:
            logger.debug("Servo board: %s", line)

    def _poll_replies(self) -> None:
        self._read_lines()
        if self._port and self._port.startswith("/") and not os.path.exists(self._port):
            raise OSError(f"{self._port} disappeared")

    def _send_if_due(self, force: bool = False) -> None:
        target = self.servo_angles()
        if target == self._last_sent:
            return
        now = time.monotonic()
        if not force and now - self._last_send_time < MIN_SEND_INTERVAL_S:
            return  # The loop comes back within _LOOP_S
        self._serial.write(f"{target[0]} {target[1]}\n".encode("ascii"))
        self._last_sent = target
        self._last_send_time = now

    def _drop(self, error: Exception) -> None:
        logger.warning("Servo board disconnected (%s): %s", self._port, error)
        self._close()
        self._warned_missing = False

    def _close(self) -> None:
        ser, self._serial = self._serial, None
        self._last_sent = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
