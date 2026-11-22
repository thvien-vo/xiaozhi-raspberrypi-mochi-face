"""Music plugin: the robot moves along with the music it plays.

The face bounces (MUSIC.FACE_BOUNCE) and, when switched on, the head dances (MUSIC.DANCE).
Playback itself belongs to MusicPlayer, which the MCP tools and the web panel share.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.logging import get_logger
from src.plugins.base import Plugin

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext

logger = get_logger()


class MusicPlugin(Plugin):
    name = "music"
    priority = 75  # After mcp (event bus), ui (face) and servo
    requires = ["ui"]

    def __init__(self, config, player) -> None:
        super().__init__()
        self._config = config
        self._player = player

    async def setup(self, ctx: PluginContext, cmd: PluginCommands) -> None:
        await super().setup(ctx, cmd)
        self._player.add_listener(self._on_music_changed)

    async def start(self) -> None:
        await super().start()
        # The first scan reads every file's tags: do it before someone asks for a song
        self.cmd.spawn(asyncio.to_thread(self._scan), name="music:scan")
        if self._player.dancer is not None and bool(self._config.get_config("MUSIC.DANCE", False)):
            self._player.set_dance(True, save=False)

    def _scan(self) -> None:
        try:
            self._player.library.scan()
        except Exception:
            logger.error("Music library scan failed", exc_info=True)

    def _on_music_changed(self) -> None:
        """App loop (Qt thread in GUI mode)."""
        face = getattr(getattr(self.get_dep("ui"), "viewport", None), "face_model", None)
        if face is None or not hasattr(face, "set_music_bpm"):
            return
        bounce = self._player.sounding and bool(self._config.get_config("MUSIC.FACE_BOUNCE", True))
        face.set_music_bpm(self._player.bpm if bounce else 0.0)

    def register_resources(self, pool) -> None:
        async def _shutdown():
            if self._player.dancer is not None:
                await asyncio.to_thread(self._player.dancer.shutdown)

        pool.register("music.dance", _shutdown)
