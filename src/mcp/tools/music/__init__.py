"""Music-related tools.

- music_player: playback session (created by container and injected into MCP; sole external entry point)
- playback / bus: PlaybackEngine / MusicEventBridge (internal composition, not public API)
- cache / download / lyrics: caching, direct URL resolution, lyrics
- online_search / local_library / metadata: song search and local library
- register_music_tools: register tools with McpServer (holds player in closure)
"""

from .register import register_music_tools
from .music_player import MusicPlayer

__all__ = [
    "MusicPlayer",
    "register_music_tools",
]
