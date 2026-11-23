"""Rest plugin: makes the face and the head follow rest mode ("Nghỉ").

The core (src/core/rest_mode.py) stops the listening and speaking and closes the server
session. This plugin does the visible parts:

* face: sleeping expression (closed eyes, zzz) that touches and sounds cannot wake; on
  waking a short happy look instead of a spoken greeting (speaking would use the server's
  chat quota);
* head: person tracking pauses, the head eases to the middle and bows a little
  (REST_TILT_DEG); on waking tracking returns to what it was and the head eases to the middle;
* "go to sleep" by voice: rests once the spoken reply has finished.

Web control of the head and gestures stay available while resting.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.logging import get_logger
from src.plugins.base import Plugin
from src.servo.motion import SOURCE_WEB, Keyframe

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext
    from src.core.rest_mode import RestController

logger = get_logger()

REST_TILT_DEG = -15.0  # Negative tilt looks down
REST_RAMP_S = 2.5
WAKE_RAMP_S = 1.5
WAKE_LOOK_S = 2.5  # How long the happy look lasts after waking
WAKE_EMOTION = "happy"


def rest_pose(tilt_limit: float) -> tuple[float, float]:
    """(pan, tilt) of the resting head: centred, bowed a little but within the limit."""
    return 0.0, max(-abs(tilt_limit), REST_TILT_DEG)


def ramp_frames(pan0: float, tilt0: float, pan1: float, tilt1: float, seconds: float) -> list[Keyframe]:
    """Two keyframes; MotionPlayer eases between them."""
    return [Keyframe(0.0, pan0, tilt0), Keyframe(seconds, pan1, tilt1)]


class HeadRest:
    """Head and tracking side of rest mode (no asyncio, no Qt: unit-testable)."""

    def __init__(self, controller, tracker=None, player=None) -> None:
        self._controller = controller
        self._tracker = tracker
        self._player = player
        self.tracking_before: bool | None = None

    def _move(self, pan: float, tilt: float, seconds: float, name: str) -> None:
        c = self._controller
        if c is None:
            return
        pan0, tilt0 = c.angles
        if self._player is not None:
            if self._player.play(name, ramp_frames(pan0, tilt0, pan, tilt, seconds), SOURCE_WEB):
                return
            logger.info("Rest: head is held by another control, moving it directly")
        c.set_angles(pan, tilt)

    def rest(self) -> None:
        t = self._tracker
        if t is not None:
            if self.tracking_before is None:
                self.tracking_before = t.enabled
            t.set_enabled(False)
        if self._controller is not None:
            pan, tilt = rest_pose(self._controller.settings.tilt_limit)
            self._move(pan, tilt, REST_RAMP_S, "nghỉ")

    def wake(self) -> None:
        t = self._tracker
        if t is not None:
            before, self.tracking_before = self.tracking_before, None
            if before:
                t.set_enabled(True)
        if self._controller is not None:
            self._move(0.0, 0.0, WAKE_RAMP_S, "thức dậy")


class RestPlugin(Plugin):
    name = "rest"
    priority = 72  # After ui (face) and servo (head)
    requires = ["ui"]

    def __init__(self, rest: "RestController", servo=None, tracker=None, player=None) -> None:
        super().__init__()
        self._rest = rest
        self._head = HeadRest(servo, tracker, player) if servo is not None else None
        self._face_model = None
        self._viewport = None
        self._wake_task: asyncio.TimerHandle | None = None

    async def setup(self, ctx: "PluginContext", cmd: "PluginCommands") -> None:
        await super().setup(ctx, cmd)
        self._rest.attach_loop(asyncio.get_running_loop())
        self._rest.add_listener(self._on_rest_changed)

    async def start(self) -> None:
        await super().start()
        ui = self.get_dep("ui")
        self._viewport = getattr(ui, "viewport", None)
        self._face_model = getattr(self._viewport, "face_model", None)
        # Restarted while resting: the face and head follow at once
        await self._rest.apply_initial()

    async def on_incoming_json(self, message: Any) -> None:
        if isinstance(message, dict) and message.get("type") == "tts" and message.get("state") == "stop":
            self._rest.note_tts_stop()

    # ----- Rest changes (app loop = Qt thread in GUI mode) -----

    def _on_rest_changed(self, resting: bool, source: str) -> None:
        self._cancel_wake_look()
        model = self._face_model
        if model is not None and hasattr(model, "set_resting"):
            try:
                model.set_resting(resting)
            except Exception:
                logger.error("Rest: could not change the face", exc_info=True)
        if self._head is not None:
            try:
                self._head.rest() if resting else self._head.wake()
            except Exception:
                logger.error("Rest: could not move the head", exc_info=True)
        if not resting:
            self._wake_look()

    def _wake_look(self) -> None:
        """A short happy look (a face effect, not speech)."""
        viewport = self._viewport
        if viewport is None or self._face_model is None or not hasattr(viewport, "set_emotion"):
            return
        try:
            viewport.set_emotion(WAKE_EMOTION)
        except Exception:
            return
        self._wake_task = asyncio.get_running_loop().call_later(WAKE_LOOK_S, self._end_wake_look)

    def _end_wake_look(self) -> None:
        self._wake_task = None
        model = self._face_model
        if model is not None and model.emotion == WAKE_EMOTION and not self._rest.resting:
            self._viewport.set_emotion("neutral")

    def _cancel_wake_look(self) -> None:
        task, self._wake_task = self._wake_task, None
        if task is not None:
            task.cancel()
