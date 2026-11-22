"""Plugin and shared service wiring.

Centralizes: creating McpServer/MusicPlayer, registering the plugin manifest, resource pool cleanup, and direct audio routing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.logging import get_logger

if TYPE_CHECKING:
    from src.bootstrap.container import ServiceContainer
    from src.bootstrap.protocols import PluginCommands, PluginContext

logger = get_logger()


def bind_shared_services(container: "ServiceContainer") -> None:
    """Create cross-plugin shared services (McpServer / MusicPlayer / Notify), held only by the container.

    No longer writes to module-level singletons; plugins and MCP tools receive the same instance via constructor injection or closures.
    """
    from src.mcp.mcp_server import McpServer
    from src.mcp.tools.music.music_player import MusicPlayer
    from src.notifications.notification_player import NotificationPlayer
    from src.vision.camera_service import set_active_camera_service
    from src.vision.runtime import VisionRuntime
    from src.vision.settings import load_vision_settings

    if container.mcp_server is None:
        container.mcp_server = McpServer()

    if container.music_player is None:
        container.music_player = MusicPlayer()

    if container.notification_player is None:
        container.notification_player = NotificationPlayer()
        container.notification_player.set_event_bus(container.event_bus)
        container.session.set_notification_player(container.notification_player)

    if container.vision is None:
        container.vision = VisionRuntime(load_vision_settings())
        # Owns the camera even with detection disabled: take_photo goes through it
        set_active_camera_service(container.vision.camera)

    if container.servo is None and container.config.get_config("SERVO.ENABLED", False):
        bind_servo(container)

    logger.debug("Shared services created: McpServer, MusicPlayer, NotificationPlayer")


def bind_servo(container: "ServiceContainer") -> None:
    """Pan/tilt head controller, plus person tracking when YOLO is available, and the
    control arbiter and motion player shared by voice commands and the web panel."""
    from src.servo.motion import ControlArbiter, MotionPlayer
    from src.servo.servo_controller import ServoController
    from src.servo.settings import load_servo_settings
    from src.servo.tracker import HeadTracker

    settings = load_servo_settings(container.config)
    container.servo = ServoController(settings)
    vision = container.vision
    if vision is not None and vision.settings.enabled:
        config = container.config
        container.head_tracker = HeadTracker(
            container.servo,
            vision.detection.latest,
            settings,
            flip=lambda: str(config.get_config("CAMERA.flip", "none") or "none"),
        )
    container.head_arbiter = ControlArbiter(container.head_tracker, settings.manual_hold_sec)
    container.head_player = MotionPlayer(container.servo, container.head_arbiter)

    from src.music.dance import MusicDancer

    if container.music_player is not None:
        container.music_player.dancer = MusicDancer(
            container.servo,
            container.head_arbiter,
            container.music_player.beat_state,
            player=container.head_player,
        )


def unbind_shared_services(container: "ServiceContainer") -> None:
    """Release shared service references (called in the final stage of the resource pool)."""
    from src.vision.camera_service import set_active_camera_service

    set_active_camera_service(None)
    if container.notification_player is not None:
        try:
            container.notification_player.detach()
        except Exception as e:
            logger.debug(f"Failed to detach NotificationPlayer: {e}", exc_info=True)
    if container.music_player is not None:
        try:
            container.music_player.detach()
        except Exception as e:
            logger.debug(f"Failed to detach MusicPlayer: {e}", exc_info=True)
    if container.mcp_server is not None:
        try:
            container.mcp_server.detach()
        except Exception as e:
            logger.debug(f"Failed to detach McpServer: {e}", exc_info=True)
    container.music_player = None
    container.mcp_server = None
    container.notification_player = None
    container.vision = None
    container.servo = None
    container.head_tracker = None
    container.head_arbiter = None
    container.head_player = None


async def setup_plugins(
    container: "ServiceContainer",
    mode: str,
    ctx: "PluginContext",
    cmd: "PluginCommands",
) -> None:
    """Bind shared services, register and initialize plugins, attach resource cleanup and direct audio routing."""
    from src.plugins.audio import AudioPlugin
    from src.plugins.mcp import McpPlugin
    from src.plugins.music import MusicPlugin
    from src.plugins.shortcuts import ShortcutsPlugin
    from src.plugins.ui import UIPlugin
    from src.plugins.vision import VisionPlugin
    from src.plugins.wake_word import WakeWordPlugin

    bind_shared_services(container)

    # Create plugin instances (Audio publishes codec via events, without injecting MusicPlayer)
    audio_plugin = AudioPlugin()
    wake_word_plugin = WakeWordPlugin()
    ui_plugin = UIPlugin(mode=mode, task_manager=container.tasks)
    shortcuts_plugin = ShortcutsPlugin()
    mcp_plugin = McpPlugin(
        server=container.mcp_server,
        music_player=container.music_player,
        vision=container.vision,
        servo=container.servo,
        head_tracker=container.head_tracker,
        head_arbiter=container.head_arbiter,
        head_player=container.head_player,
    )
    vision_plugin = VisionPlugin(container.vision)

    plugins = [
        mcp_plugin,
        audio_plugin,
        wake_word_plugin,
        ui_plugin,
        vision_plugin,
        shortcuts_plugin,
        MusicPlugin(container.config, container.music_player),
    ]
    if container.servo is not None:
        from src.plugins.servo import ServoPlugin

        plugins.append(
            ServoPlugin(
                container.servo,
                container.head_tracker,
                container.vision,
                player=container.head_player,
            )
        )
    # Web control panel (WEB.ENABLED): 3D head control, camera, face and chat
    if container.config.get_config("WEB.ENABLED", False):
        from src.plugins.web_panel import WebPanelPlugin

        plugins.append(WebPanelPlugin.from_container(container))
    # Robot kiosk: keep listening whatever stops it (needs the UI session actions for taps)
    if container.config.get_config("FACE.ALWAYS_LISTEN", False):
        from src.plugins.always_listen import AlwaysListenPlugin

        plugins.append(AlwaysListenPlugin.from_config(container.config, audio_plugin))

    container.plugins.register(*plugins)

    await container.plugins.setup_all(ctx, cmd)

    register_cleanup_resources(container)

    # Set up direct audio channel (TTS audio bypasses EventBus to reduce latency)
    if not audio_plugin.failed:
        container.protocol.set_audio_handler(audio_plugin.on_incoming_audio)


def register_cleanup_resources(container: "ServiceContainer") -> None:
    """Register cleanup functions of all modules into the resource pool (first registered, last released)."""
    pool = container.resource_pool

    # Registered first = released last: shared services unbind after plugin cleanup
    pool.register(
        "shared_services", lambda: unbind_shared_services(container)
    )

    # Event bus released second-to-last (registered second)
    pool.register("event_bus", container.event_bus.clear)

    # Each plugin registers its own resources
    for plugin in container.plugins._plugins:
        plugin.register_resources(pool)

    # Notifications must be cleaned up before AudioCodec to stop decoding tasks and release playback FIFOs.
    if container.notification_player is not None:
        pool.register("notification_player", container.notification_player.stop)

    # Network connection: close control channel and background threads on exit; in-session reconnects still use disconnect.
    pool.register("protocol", container.protocol.shutdown)

    # Asynchronous tasks
    pool.register("tasks", container.tasks.cancel_all)
