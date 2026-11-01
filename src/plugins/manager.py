"""Plugin manager.

Manages plugin lifecycle, supporting dependency declarations and topological sorting.
"""

from typing import TYPE_CHECKING, Any, List, Optional

from src.logging import get_logger

from .base import Plugin

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext

logger = get_logger()


class PluginManager:
    """Plugin manager.

    Responsibilities:
    - Topological sorting by dependencies
    - Automatic injection of plugin dependencies
    - Unified setup/start/stop broadcasting
    - Error isolation so a single plugin failure does not affect others
    - Failure marking + skipping downstream dependents when a dependency fails
    """

    def __init__(self) -> None:
        self._plugins: List[Plugin] = []
        self._by_name: dict[str, Plugin] = {}
        self._sorted: bool = False

    def register(self, *plugins: Plugin) -> None:
        """Register plugins.

        Initially sorts by priority; setup_all performs topological sorting by dependencies.
        """
        sorted_plugins = sorted(plugins, key=lambda p: getattr(p, "priority", 50))
        for p in sorted_plugins:
            if p not in self._plugins:
                self._plugins.append(p)
                try:
                    name = getattr(p, "name", None)
                    if isinstance(name, str) and name:
                        self._by_name[name] = p
                except Exception as e:
                    logger.error(f"Failed to register plugin: {e}", exc_info=True)
        self._sorted = False

    def get_plugin(self, name: str) -> Optional[Plugin]:
        """Get a plugin instance by name."""
        return self._by_name.get(name)

    def is_failed(self, name: str) -> bool:
        """Check whether a plugin has failed (unregistered plugins are treated as failed)."""
        plugin = self._by_name.get(name)
        return plugin is None or plugin.failed

    def failed_plugins(self) -> List[str]:
        """Return names of all failed plugins."""
        return [
            p.name
            for p in self._plugins
            if getattr(p, "name", None) and p.failed
        ]

    def _dependencies_ok(self, plugin: Plugin) -> bool:
        """Check whether all declared dependencies of a plugin are available (registered and not failed)."""
        requires = getattr(plugin, "requires", []) or []
        for dep_name in requires:
            dep = self._by_name.get(dep_name)
            if dep is None:
                logger.warning(
                    f"Plugin {getattr(plugin, 'name', 'unknown')} dependency {dep_name} is not registered"
                )
                return False
            if dep.failed:
                logger.warning(
                    f"Plugin {getattr(plugin, 'name', 'unknown')} dependency {dep_name} failed, skipping"
                )
                return False
        return True

    def _active_plugins(self) -> List[Plugin]:
        """List of non-failed plugins."""
        return [p for p in self._plugins if not p.failed]

    def _topological_sort(self) -> List[Plugin]:
        """Topologically sort the plugin list to ensure dependencies initialize before dependents.

        Returns:
            Sorted list of plugins

        Raises:
            ValueError: If a circular dependency exists
        """
        # Build dependency graph
        in_degree: dict[str, int] = {}
        dependents: dict[str, List[str]] = {}  # Who depends on this plugin

        for p in self._plugins:
            name = getattr(p, "name", "")
            if name:
                in_degree[name] = 0
                dependents[name] = []

        for p in self._plugins:
            name = getattr(p, "name", "")
            requires = getattr(p, "requires", []) or []
            for dep in requires:
                if dep in self._by_name:
                    in_degree[name] = in_degree.get(name, 0) + 1
                    dependents[dep].append(name)
                else:
                    logger.warning(
                        f"Plugin {name} declared unregistered dependency {dep}, ignoring"
                    )

        # Kahn's algorithm
        queue = [name for name, degree in in_degree.items() if degree == 0]
        result: List[Plugin] = []

        while queue:
            # Pick the node with the lowest priority value among zero in-degree nodes
            queue.sort(
                key=lambda n: getattr(self._by_name.get(n), "priority", 50)
            )
            current = queue.pop(0)
            plugin = self._by_name.get(current)
            if plugin:
                result.append(plugin)

            for dependent in dependents.get(current, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len([p for p in self._plugins if getattr(p, "name", "")]):
            raise ValueError("Circular dependency detected among plugins")

        # Append unnamed plugins at the end
        unnamed = [p for p in self._plugins if not getattr(p, "name", "")]
        result.extend(unnamed)

        return result

    def _inject_dependencies(self) -> None:
        """Inject declared dependencies into each plugin."""
        for p in self._plugins:
            requires = getattr(p, "requires", []) or []
            for dep_name in requires:
                dep_plugin = self._by_name.get(dep_name)
                if dep_plugin:
                    p._inject_dependency(dep_name, dep_plugin)
                    logger.debug(f"Injected dependency: {p.name} <- {dep_name}")

    async def setup_all(self, ctx: "PluginContext", cmd: "PluginCommands") -> None:
        """Initialize all plugins.

        Initializes in topologically sorted order and automatically injects dependencies.
        Plugins whose setup or dependencies fail are marked failed and skip subsequent lifecycle stages.

        Args:
            ctx: Plugin context
            cmd: Plugin commands interface
        """
        # Topological sort
        if not self._sorted:
            try:
                self._plugins = self._topological_sort()
                self._sorted = True
                logger.info(
                    f"Plugin topological sort completed: {[p.name for p in self._plugins if hasattr(p, 'name')]}"
                )
            except ValueError as e:
                logger.error(f"Plugin sorting failed: {e}", exc_info=True)
                # Fall back to priority sorting
                self._plugins.sort(key=lambda p: getattr(p, "priority", 50))

        # Inject dependencies
        self._inject_dependencies()

        # Initialize
        for p in list(self._plugins):
            name = getattr(p, "name", "unknown")
            if p.failed:
                continue
            if not self._dependencies_ok(p):
                p.mark_failed()
                logger.error(f"Plugin {name} skipped setup due to dependency failure")
                continue
            try:
                await p.setup(ctx, cmd)
            except Exception as e:
                p.mark_failed()
                logger.error(f"Plugin {name} setup failed: {e}", exc_info=True)

    async def start_all(self) -> None:
        """Start all non-failed plugins."""
        for p in list(self._plugins):
            if p.failed:
                continue
            if not self._dependencies_ok(p):
                p.mark_failed()
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} skipped start due to dependency failure"
                )
                continue
            try:
                await p.start()
            except Exception as e:
                p.mark_failed()
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} start failed: {e}",
                    exc_info=True,
                )

    async def notify_protocol_connected(self, protocol: Any) -> None:
        """Notify plugins that the protocol is connected."""
        for p in self._active_plugins():
            try:
                if p.on_protocol_connected:
                    await p.on_protocol_connected(protocol)
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} on_protocol_connected failed: {e}",
                    exc_info=True,
                )

    async def notify_incoming_json(self, message: Any) -> None:
        """Notify plugins of an incoming JSON message."""
        for p in self._active_plugins():
            try:
                await p.on_incoming_json(message)
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} on_incoming_json failed: {e}",
                    exc_info=True,
                )

    async def notify_incoming_audio(self, data: bytes) -> None:
        """Notify plugins of incoming audio data."""
        for p in self._active_plugins():
            try:
                await p.on_incoming_audio(data)
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} on_incoming_audio failed: {e}",
                    exc_info=True,
                )

    async def notify_device_state_changed(self, state: Any) -> None:
        """Notify plugins of a device state change."""
        for p in self._active_plugins():
            try:
                await p.on_device_state_changed(state)
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} on_device_state_changed failed: {e}",
                    exc_info=True,
                )

    async def stop_all(self) -> None:
        """Stop all plugins (in reverse order; failed plugins still attempt stop for cleanup)."""
        for p in reversed(self._plugins):
            try:
                await p.stop()
            except Exception as e:
                logger.error(
                    f"Plugin {getattr(p, 'name', 'unknown')} stop failed: {e}",
                    exc_info=True,
                )
