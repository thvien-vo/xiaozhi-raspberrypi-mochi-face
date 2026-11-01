"""GPIO input handling.

Wraps gpiozero button monitoring to support Raspberry Pi GPIO button input.
Linux systems only.

Button module specifications:
- Outputs low level when pressed (Active Low)
- Outputs high level when released

Default pin mapping (BCM numbering):
- KEY1: GPIO 17 - Start/Stop conversation
- KEY2: GPIO 27 - Abort current speech
- KEY3: GPIO 22 - Toggle Auto/Manual mode
- KEY4: GPIO 23 - Quit application

Modify DEFAULT_PINS to customize pins.
"""

import sys
import threading
from typing import Callable, List, Optional

from src.logging import get_logger

logger = get_logger()

# Default GPIO pin configuration (BCM numbering)
# Modify according to actual wiring
DEFAULT_PINS: List[int] = [17, 27, 22, 23]


class GPIOInput:
    """GPIO button input handler class.

    Wraps gpiozero's Button class to provide button event callbacks.
    """

    def __init__(
        self,
        pins: Optional[List[int]] = None,
        bounce_time: float = 0.05,
    ):
        """Initialize GPIO input.

        Args:
            pins: List of GPIO pins (BCM numbering), defaults to DEFAULT_PINS
            bounce_time: Debounce time (seconds)
        """
        self._pins = pins or DEFAULT_PINS.copy()
        self._bounce_time = bounce_time
        self._buttons: List = []
        self._callbacks: dict[int, dict[str, Optional[Callable]]] = {}
        self._lock = threading.Lock()
        self._available = False

        # Check platform
        if sys.platform != "linux":
            logger.warning("GPIO mode is only supported on Linux systems")
            return

        # Try importing gpiozero
        try:
            from gpiozero import Button  # type: ignore[import-not-found]

            self._Button = Button
            self._available = True
            logger.info(f"GPIO input initialized successfully, pins: {self._pins}")
        except ImportError:
            logger.error(
                "gpiozero library is not installed, please run: sudo apt install python3-gpiozero python3-rpi.gpio"
            )
        except Exception as e:
            logger.error(f"GPIO initialization failed: {e}", exc_info=True)

    @property
    def available(self) -> bool:
        """Whether GPIO is available."""
        return self._available

    @property
    def pins(self) -> List[int]:
        """Currently configured pin list."""
        return self._pins.copy()

    def setup(
        self,
        on_key1_pressed: Optional[Callable] = None,
        on_key2_pressed: Optional[Callable] = None,
        on_key3_pressed: Optional[Callable] = None,
        on_key4_pressed: Optional[Callable] = None,
    ) -> bool:
        """Set up button callbacks.

        Args:
            on_key1_pressed: KEY1 press callback
            on_key2_pressed: KEY2 press callback
            on_key3_pressed: KEY3 press callback
            on_key4_pressed: KEY4 press callback

        Returns:
            Whether setup succeeded
        """
        if not self._available:
            logger.warning("GPIO unavailable, skipping button setup")
            return False

        callbacks = [on_key1_pressed, on_key2_pressed, on_key3_pressed, on_key4_pressed]

        try:
            for i, pin in enumerate(self._pins):
                # Create button instance (Active Low, internal pull-up)
                button = self._Button(pin, pull_up=True, bounce_time=self._bounce_time)
                self._buttons.append(button)

                # Save callback
                with self._lock:
                    self._callbacks[i] = {
                        "pressed": callbacks[i] if i < len(callbacks) else None,
                    }

                # Set press callback
                if i < len(callbacks) and callbacks[i]:
                    # Capture index via closure
                    def make_handler(idx: int):
                        def handler():
                            with self._lock:
                                cb = self._callbacks.get(idx, {}).get("pressed")
                            if cb:
                                logger.debug(f"KEY{idx + 1} (GPIO{self._pins[idx]}) pressed")
                                cb()

                        return handler

                    button.when_pressed = make_handler(i)

                logger.debug(f"KEY{i + 1} -> GPIO{pin} configured")

            logger.info(f"GPIO button setup complete: {len(self._buttons)} buttons")
            return True

        except Exception as e:
            logger.error(f"GPIO button setup failed: {e}", exc_info=True)
            return False

    def close(self) -> None:
        """Close GPIO resources."""
        for button in self._buttons:
            try:
                button.close()
            except Exception as e:
                logger.warning(f"Failed to close GPIO button: {e}", exc_info=True)

        self._buttons.clear()
        with self._lock:
            self._callbacks.clear()
        logger.info("GPIO resources released")
