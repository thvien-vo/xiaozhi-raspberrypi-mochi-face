"""Servo plugin: runs the pan/tilt head controller and person tracking with the app."""

import asyncio
from typing import TYPE_CHECKING

from src.logging import get_logger
from src.plugins.base import Plugin

if TYPE_CHECKING:
    from src.servo.servo_controller import ServoController
    from src.servo.tracker import HeadTracker
    from src.vision.runtime import VisionRuntime

logger = get_logger()


class ServoPlugin(Plugin):
    name = "servo"
    priority = 70  # After vision, so detection is already running

    def __init__(
        self,
        controller: "ServoController",
        tracker: "HeadTracker | None" = None,
        vision: "VisionRuntime | None" = None,
        player=None,
    ) -> None:
        super().__init__()
        self._player = player
        self._controller = controller
        self._tracker = tracker
        self._vision = vision
        if tracker is not None:
            tracker.set_on_enable(self._ensure_detection)

    def _ensure_detection(self) -> None:
        """Tracking reads YOLO results, so detection must keep running (the GUI preview
        already does that; other modes start it on demand only)."""
        vision = self._vision
        if vision is None or not vision.settings.enabled:
            return
        vision.set_keep_alive(True)
        vision.start()

    async def start(self) -> None:
        await super().start()
        self._controller.start()
        if self._tracker is not None:
            if self._tracker.enabled:
                self._ensure_detection()
            self._tracker.start()
            logger.info("Head tracking thread started (tracking %s)",
                        "on" if self._tracker.enabled else "off")

    def register_resources(self, pool) -> None:
        async def _shutdown():
            if self._player is not None:
                await asyncio.to_thread(self._player.stop)
            if self._tracker is not None:
                await asyncio.to_thread(self._tracker.stop)
            await asyncio.to_thread(self._controller.shutdown)

        pool.register("servo", _shutdown)
