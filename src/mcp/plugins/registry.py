"""External MCP plugin runtime registry: ownership index and unloading."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.logging import get_logger

if TYPE_CHECKING:
    from src.mcp.mcp_server import McpServer

logger = get_logger()


@dataclass
class PluginRegistry:
    """Tracks external tool_name -> plugin_id mapping and supports unloading tools from McpServer by id."""

    tool_owner: dict[str, str] = field(default_factory=dict)

    def register_ownership(self, plugin_id: str, tool_names: list[str]) -> None:
        for name in tool_names:
            self.tool_owner[name] = plugin_id

    def tools_for_plugin(self, plugin_id: str) -> list[str]:
        return [n for n, p in self.tool_owner.items() if p == plugin_id]

    def unload_plugin(self, server: "McpServer", plugin_id: str) -> int:
        """Remove tools registered by this plugin from server.tools; returns the count of removed tools."""
        try:
            from src.mcp.plugins.subprocess_runtime import drop_session

            drop_session(plugin_id)
        except Exception:
            pass
        names = set(self.tools_for_plugin(plugin_id))
        if not names:
            logger.info("[MCP Plugin] Unload %s: no registered tools found", plugin_id)
            return 0
        before = len(server.tools)
        server.tools = [t for t in server.tools if t.name not in names]
        removed = before - len(server.tools)
        for n in names:
            self.tool_owner.pop(n, None)
        logger.info(
            "[MCP Plugin] Unloaded %s: removed %d tools %s",
            plugin_id,
            removed,
            sorted(names),
        )
        return removed

    def clear(self) -> None:
        self.tool_owner.clear()


# Optional in-process shared instance (prefer holding via McpServer; provided here for simple scenarios)
_default_registry: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = PluginRegistry()
    return _default_registry
