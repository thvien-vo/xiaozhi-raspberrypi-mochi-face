"""Vision plugin: connects the shared vision runtime to the UI and the app lifecycle."""

import asyncio
from typing import TYPE_CHECKING

from src.logging import get_logger
from src.plugins.base import Plugin

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext
    from src.vision.runtime import VisionRuntime

logger = get_logger()


class VisionPlugin(Plugin):
    name = "vision"
    priority = 65
    requires = ["ui"]  # The preview attaches to the viewport during setup

    def __init__(self, runtime: "VisionRuntime") -> None:
        super().__init__()
        self._runtime = runtime

    async def setup(self, ctx: "PluginContext", cmd: "PluginCommands") -> None:
        await super().setup(ctx, cmd)
        from src.core.event_bus import Events

        ctx.event_bus.on(Events.CONFIG_CHANGED, self._on_config_changed)

        viewport = self.get_dep("ui").viewport
        attach = getattr(viewport, "attach_vision", None)
        if callable(attach) and attach(self._runtime):
            self._runtime.set_keep_alive(True)

    async def start(self) -> None:
        await super().start()
        if self._runtime.keep_alive:
            self._runtime.start()

    async def _on_config_changed(self, _data=None) -> None:
        self._runtime.camera.request_reopen()

    def register_resources(self, pool) -> None:
        async def _shutdown():
            await asyncio.to_thread(self._runtime.shutdown)

        pool.register("vision", _shutdown)
