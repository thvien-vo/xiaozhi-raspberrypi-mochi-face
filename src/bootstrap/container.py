"""Service container.

Integrates core services as the central coordinator of the application; session, health checks, wiring, and adapters are split into submodules.
"""

from src.bootstrap.adapters import PluginCommandsAdapter, PluginContextAdapter
from src.bootstrap.health import (
    DEGRADED_AUDIO_NOTICE,
    audio_is_fatal,
    check_critical_plugins,
)
from src.bootstrap.plugin_wiring import setup_plugins
from src.bootstrap.protocols import PluginCommands, PluginContext
from src.bootstrap.session import ConversationSession
from src.core.event_bus import EventBus, Events
from src.core.protocol_manager import ProtocolManager
from src.core.resource_pool import ResourcePool
from src.core.state_manager import StateManager
from src.core.task_manager import TaskManager
from src.logging import get_logger
from src.plugins.manager import PluginManager
from src.utils.config_manager import get_config

logger = get_logger()


class ServiceContainer:
    """Service container.

    Holds core services and ConversationSession, orchestrating the run / shutdown lifecycle.
    Session operations go through ``self.session``; health checks go through the ``health`` module.
    """

    def __init__(self):
        logger.debug("Initializing ServiceContainer")

        self.config = get_config()

        try:
            aec_enabled = bool(self.config.get_config("AEC_OPTIONS.ENABLED", True))
        except Exception:
            aec_enabled = True

        self.event_bus = EventBus()
        self.state = StateManager(self.event_bus, aec_enabled=aec_enabled)
        self.tasks = TaskManager()
        # Protocol inbound tasks go through TaskManager to avoid fire-and-forget
        self.protocol = ProtocolManager(self.event_bus, task_manager=self.tasks)
        self.plugins = PluginManager()
        self.resource_pool = ResourcePool()

        # Cross-plugin shared services held by the container (bound at startup, unbound at shutdown)
        self.mcp_server = None
        self.music_player = None
        self.notification_player = None
        self.vision = None

        # Session control (listen/speak/interrupt/TTS loopback)
        self.session = ConversationSession(
            state=self.state,
            protocol=self.protocol,
            plugins=self.plugins,
            event_bus=self.event_bus,
        )

        self._plugin_context: PluginContextAdapter | None = None
        self._plugin_commands: PluginCommandsAdapter | None = None

        self._mode: str = "cli"
        self._shutting_down = False
        # Running with degraded audio (XIAOZHI_DEGRADED_AUDIO=1 and audio failed)
        self._degraded_audio = False

    # -------------------------
    # Adapter creation
    # -------------------------
    def create_plugin_context(self) -> PluginContext:
        if not self._plugin_context:
            self._plugin_context = PluginContextAdapter(self)
        return self._plugin_context

    def create_plugin_commands(self) -> PluginCommands:
        if not self._plugin_commands:
            self._plugin_commands = PluginCommandsAdapter(self)
        return self._plugin_commands

    # -------------------------
    # Lifecycle
    # -------------------------
    async def run(self, *, protocol: str = "websocket", mode: str = "gui") -> int:
        logger.info(f"Starting ServiceContainer, protocol={protocol}, mode={mode}")
        self._mode = mode

        try:
            self.tasks.initialize()
            self.protocol.set_task_manager(self.tasks)
            self.protocol.set_protocol(protocol)
            self.protocol.set_idle_checker(self.state.is_idle)

            self.session.bind_events(self.event_bus)

            ctx = self.create_plugin_context()
            cmd = self.create_plugin_commands()

            await setup_plugins(self, mode, ctx, cmd)
            await self.plugins.start_all()

            # Critical plugin health check: exit on failure, prevent silent zombie
            health_error = check_critical_plugins(self.plugins)
            if health_error:
                logger.error(health_error)
                return 1

            # Audio failed but degradation allowed: log banner/notice and continue running UI
            if self.plugins.is_failed("audio") and not audio_is_fatal():
                logger.warning(DEGRADED_AUDIO_NOTICE)
                self._degraded_audio = True
                try:
                    await self.event_bus.emit(
                        Events.SYSTEM_NOTICE, DEGRADED_AUDIO_NOTICE
                    )
                except Exception as e:
                    logger.debug(f"Degraded mode notice event failed: {e}", exc_info=True)

            await self.plugins.notify_device_state_changed(self.state.device_state)

            # MQTT control connection stays persistent for MCP/notify; WSS default implementation is a no-op.
            await self.protocol.start()

            await self.tasks.wait_shutdown()
            return 0

        except Exception as e:
            logger.error(f"Application run failed: {e}", exc_info=True)
            return 1
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Shut down the application, releasing all resources in reverse order via the resource pool."""
        if self._shutting_down:
            logger.debug("ServiceContainer is already shutting down, skipping")
            return
        self._shutting_down = True
        logger.info("Shutting down ServiceContainer...")

        try:
            await self.resource_pool.shutdown()
            logger.info("ServiceContainer shutdown completed")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
        finally:
            if self._mode == "gui":
                try:
                    from PySide6.QtWidgets import QApplication

                    if QApplication.instance():
                        logger.debug("Quitting Qt application")
                        QApplication.quit()
                except Exception as e:
                    logger.debug(f"Error quitting Qt application: {e}")
