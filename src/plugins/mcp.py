"""MCP plugin.

Manages MCP tools and message handling. McpServer and MusicPlayer must be injected by the container.
"""

from typing import TYPE_CHECKING, Any, Optional

from src.logging import get_logger
from src.mcp.mcp_server import McpServer
from src.plugins.base import Plugin

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext
    from src.mcp.tools.music.music_player import MusicPlayer
    from src.vision.runtime import VisionRuntime

logger = get_logger()


class McpPlugin(Plugin):
    name = "mcp"
    priority = 20  # Tool registration; needs early initialization

    def __init__(
        self,
        server: Optional[McpServer] = None,
        music_player: Optional["MusicPlayer"] = None,
        vision: Optional["VisionRuntime"] = None,
        servo=None,
        head_tracker=None,
        head_arbiter=None,
        head_player=None,
    ) -> None:
        super().__init__()
        if server is None:
            raise ValueError("McpPlugin requires a container-injected McpServer")
        if music_player is None:
            raise ValueError("McpPlugin requires a container-injected MusicPlayer")
        self._server: McpServer = server
        self._music_player = music_player
        self._vision = vision
        self._servo = servo
        self._head_tracker = head_tracker
        self._head_arbiter = head_arbiter
        self._head_player = head_player

    async def setup(self, ctx: "PluginContext", cmd: "PluginCommands") -> None:
        await super().setup(ctx, cmd)
        server = self._server

        async def _send(msg: str):
            try:
                delivered = await cmd.send_mcp_message(msg)
                if delivered is False:
                    raise RuntimeError("MCP response was not published to the active protocol")
            except Exception as e:
                logger.error(f"Failed to send MCP response: {e}", exc_info=True)

        try:
            server.set_send_callback(_send)
            # Camera: lazily created once and attached to server, shared between vision config and take_photo
            from src.mcp.tools.camera import create_camera, register_camera_tools
            from src.mcp.tools.screenshot import register_screenshot_tools

            camera = create_camera()
            server.set_camera(camera)
            register_camera_tools(server.add_tool, camera)
            register_screenshot_tools(server.add_tool, camera)

            if self._vision is not None and self._vision.settings.enabled:
                from src.mcp.tools.vision import register_vision_tools

                register_vision_tools(server.add_tool, self._vision)

            if self._servo is not None:
                from src.mcp.tools.servo import register_servo_tools

                register_servo_tools(
                    server.add_tool,
                    self._servo,
                    self._head_tracker,
                    arbiter=self._head_arbiter,
                    player=self._head_player,
                )

            server.add_common_tools(music_player=self._music_player)
        except Exception as e:
            logger.error(f"Failed to register MCP tools: {e}", exc_info=True)

        try:
            self._music_player.set_event_bus(ctx.event_bus, ctx)
            logger.info("MusicPlayer EventBus injected")
        except Exception as e:
            logger.warning(f"Failed to set MusicPlayer EventBus: {e}", exc_info=True)

    async def on_incoming_json(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        try:
            if message.get("type") == "mcp":
                payload = message.get("payload")
                if not payload:
                    return
                await self._server.parse_message(payload)
        except Exception as e:
            logger.error(f"Failed to handle MCP message: {e}", exc_info=True)

    def register_resources(self, pool) -> None:
        async def _mcp_cleanup():
            try:
                music_player = self._music_player
                if music_player.is_playing:
                    await music_player.stop()
                music_player.detach()
            except Exception as e:
                logger.debug(f"Failed to stop/detach music player: {e}", exc_info=True)

            try:
                self._server.detach()
            except Exception as e:
                logger.debug(f"MCP shutdown cleanup failed: {e}", exc_info=True)

        pool.register("mcp.server", _mcp_cleanup)
