"""Web panel plugin: serves the control page from inside the app (WEB.ENABLED).

The HTTP/WebSocket server runs on its own thread (src/web_panel/server.py). This plugin
feeds it the app's state (listening/speaking, recognized and spoken text, the face's
emotion) and turns the page's requests for the face, the auto mode and text messages
into calls on the app's own event loop, which is also the Qt thread in GUI mode.

The face on the page mirrors the screen: the expression comes from the FaceModel's own
signals (so voice, web, auto mode and listening/speaking changes all show), the eye color
from the shared EyeColorState, and while a page watches, FaceSnapshotService grabs small
JPEGs of the face item (~10 a second) that the page puts on the 3D model's screen.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.logging import get_logger
from src.plugins.base import Plugin

if TYPE_CHECKING:
    from src.bootstrap.container import ServiceContainer
    from src.bootstrap.protocols import PluginCommands, PluginContext

logger = get_logger()

# The emotion names the server sends and the robot face can draw
FALLBACK_EMOTIONS = (
    "neutral", "happy", "laughing", "funny", "sad", "angry", "crying", "loving",
    "embarrassed", "surprised", "shocked", "thinking", "winking", "cool", "relaxed",
    "delicious", "kissy", "confident", "sleepy", "silly", "confused",
)


def known_emotions() -> tuple[str, ...]:
    try:
        from src.ui.gui.services.emotion_service import EmotionService

        return tuple(EmotionService.KNOWN_EMOTIONS)
    except Exception:  # No PySide6 (CLI-only install)
        return tuple(sorted(FALLBACK_EMOTIONS))


class WebPanelPlugin(Plugin):
    name = "web_panel"
    priority = 80  # After ui, vision and servo
    requires = ["ui"]

    def __init__(self, config, servo=None, tracker=None, arbiter=None, player=None, vision=None, mode: str = "gui", music_player=None, rest=None) -> None:
        super().__init__()
        self._rest = rest
        self._config = config
        self._servo = servo
        self._tracker = tracker
        self._arbiter = arbiter
        self._player = player
        self._vision = vision
        self._mode = mode
        self._music_player = music_player
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self.backend = None
        self._face_model = None
        self._snapshots = None  # FaceSnapshotService, made on first viewer
        self._frames = None

    @classmethod
    def from_container(cls, container: ServiceContainer) -> WebPanelPlugin:
        return cls(
            container.config,
            servo=container.servo,
            tracker=container.head_tracker,
            arbiter=container.head_arbiter,
            player=container.head_player,
            vision=container.vision,
            mode=getattr(container, "_mode", "gui"),
            music_player=container.music_player,
            rest=container.rest,
        )

    # ----- Setup -----

    async def setup(self, ctx: PluginContext, cmd: PluginCommands) -> None:
        await super().setup(ctx, cmd)
        from src.audio_codecs.levels import get_audio_levels
        from src.core.event_bus import Events
        from src.servo.motion import MotionLibrary
        from src.utils.resource_finder import get_app_root, get_user_data_dir
        from src.web_panel.backend import AppHooks, AppState, RobotBackend
        from src.web_panel.camera import vision_camera_feed
        from src.web_panel.music import MusicControl
        from src.web_panel.server import WebPanelServer
        from src.web_panel.settings import load_web_settings

        self._loop = asyncio.get_running_loop()
        settings = load_web_settings(self._config)
        data_dir = get_user_data_dir()
        ui = self.get_dep("ui")
        viewport = getattr(ui, "viewport", None)
        face_available = self._mode == "gui" and hasattr(viewport, "set_device_state")

        from src.ui.face_color import get_eye_colors
        from src.web_panel.face_stream import FaceFrameHub

        eye_colors = get_eye_colors(self._config)
        if eye_colors.save is None:
            eye_colors.save = self._save_setting
        self._face_model = getattr(viewport, "face_model", None) if face_available else None
        if self._face_model is not None and hasattr(viewport, "create_face_snapshot") and settings.face_stream_fps > 0:
            self._frames = FaceFrameHub(on_active=self._face_snapshots_active, fps=settings.face_stream_fps)

        state = AppState(
            emotions=known_emotions(),
            auto_mode=self._auto_mode(),
            always_listen=bool(self._config.get_config("FACE.ALWAYS_LISTEN", False)),
            face_available=face_available,
            text_supported=True,  # Sent like a typed message (UI_SEND_TEXT)
        )
        hooks = AppHooks(
            set_emotion=self._set_emotion if viewport is not None else None,
            set_auto_mode=self._set_auto_mode,
            send_text=self._send_text,
            face_wake=self._face_wake if face_available else None,
        )
        camera = None
        if self._vision is not None:
            camera = vision_camera_feed(self._vision, settings.camera_stream_fps)
        self.backend = RobotBackend(
            controller=self._servo,
            tracker=self._tracker,
            arbiter=self._arbiter,
            player=self._player,
            library=MotionLibrary(data_dir / "motions"),
            app_state=state,
            hooks=hooks,
            camera=camera,
            save_setting=self._save_setting,
            eye_colors=eye_colors if face_available else None,
            face_frames=self._frames,
            music=MusicControl(self._music_player, lambda: self._loop) if self._music_player is not None else None,
            levels=get_audio_levels(self._config),
            rest=self._rest,
        )
        if self._music_player is not None:
            self._music_player.add_listener(self.backend._changed)
        if self._face_model is not None:
            model = self._face_model
            for signal in (model.emotionChanged, model.visualStateChanged, model.activityChanged):
                signal.connect(self._sync_face)
            self._sync_face()
        self._server = WebPanelServer(
            self.backend,
            settings,
            static_dir=get_app_root() / "web_assets",
            cert_dir=data_dir / "web_cert",
        )
        bus = ctx.event_bus
        bus.on(Events.PROTOCOL_CONNECTED, self._on_connected)
        bus.on(Events.PROTOCOL_DISCONNECTED, self._on_disconnected)

    async def start(self) -> None:
        await super().start()
        started = await asyncio.to_thread(self._server.start)
        if started:
            # The session reads FACE.AUTO_MODE when the UI plugin starts, before this
            self.backend.app.update(auto_mode=self._auto_mode())
        else:
            logger.error("Web panel did not start: %s", self._server.start_error)

    def register_resources(self, pool) -> None:
        async def _shutdown():
            if self._server is not None:
                await asyncio.to_thread(self._server.stop)

        pool.register("web_panel", _shutdown)

    # ----- App -> page -----

    async def on_device_state_changed(self, state: Any) -> None:
        if self.backend is None:
            return
        value = getattr(state, "value", str(state))
        update = {"device_state": value, "auto_mode": self._auto_mode()}
        # Without the GUI face: same rule as UiPresenter (a reply emotion survives into
        # speaking, others reset). With it, the FaceModel's signals say what is shown.
        if value != "speaking" and self._face_model is None:
            update["emotion"] = "neutral"
        self.backend.app.update(**update)

    async def on_incoming_json(self, message: Any) -> None:
        if self.backend is None or not isinstance(message, dict):
            return
        kind = message.get("type")
        text = message.get("text")
        if kind == "stt" and text:
            self.backend.app.add_chat("user", text)
        elif kind == "tts" and text and message.get("state") == "sentence_start":
            self.backend.app.add_chat("robot", text)
        elif kind == "llm" and message.get("emotion") and self._face_model is None:
            emotion = str(message["emotion"]).lower()
            if emotion not in self.backend.app.emotions:
                emotion = "neutral"
            self.backend.app.update(emotion=emotion)

    async def _on_connected(self, _data=None) -> None:
        if self.backend is not None:
            self.backend.app.update(protocol_connected=True)

    async def _on_disconnected(self, _data=None) -> None:
        if self.backend is not None:
            self.backend.app.update(protocol_connected=False)

    def _sync_face(self) -> None:
        """Qt thread: the face changed (any source)."""
        model = self._face_model
        if self.backend is None or model is None:
            return
        self.backend.app.update(emotion=model.emotion, face_visual=model.visualState, face_activity=model.activity)

    def _face_snapshots_active(self, active: bool) -> None:
        """Web thread: first page started / last page stopped watching the face."""

        def apply():
            if self._snapshots is None and active:
                viewport = getattr(self.get_dep("ui"), "viewport", None)
                hub = self._frames
                self._snapshots = viewport.create_face_snapshot(hub.publish, fps=hub.fps)
                if self._snapshots is None:
                    logger.warning("Web panel: robot face not found, the page draws the face itself")
                    hub.available = False
                    self.backend._changed()
                    return
            if self._snapshots is not None:
                self._snapshots.set_active(active)
                logger.info("Web panel: face snapshots %s", "on" if active else "off")

        self._call_soon(apply)

    # ----- Page -> app (called on the web thread; hop onto the app loop) -----

    def _call_soon(self, fn, *args) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(fn, *args)

    def _set_emotion(self, name: str) -> None:
        def apply():
            viewport = getattr(self.get_dep("ui"), "viewport", None)
            if viewport is not None:
                viewport.set_emotion(name)

        self._call_soon(apply)

    def _set_auto_mode(self, enabled: bool) -> None:
        async def apply():
            from src.core.event_bus import Events

            if self._auto_mode() != enabled:
                await self.ctx.event_bus.emit(Events.UI_AUTO_TOGGLE)
            self.backend.app.update(auto_mode=self._auto_mode())

        self._call_soon(lambda: self.cmd.spawn(apply(), name="web_panel:auto_mode"))

    def _send_text(self, text: str) -> None:
        async def apply():
            from src.core.event_bus import Events

            await self.ctx.event_bus.emit(Events.UI_SEND_TEXT, text)

        self._call_soon(lambda: self.cmd.spawn(apply(), name="web_panel:send_text"))

    def _face_wake(self) -> None:
        def apply():
            viewport = getattr(self.get_dep("ui"), "viewport", None)
            face = getattr(getattr(viewport, "_main", None), "face_model", None)
            if face is not None:
                face.interact()

        self._call_soon(apply)

    def _save_setting(self, path: str, value) -> None:
        self._call_soon(lambda: self._config.update_config(path, value))

    def _auto_mode(self) -> bool | None:
        session = getattr(self.get_dep("ui"), "_session", None)
        return None if session is None else bool(session.auto_mode)
