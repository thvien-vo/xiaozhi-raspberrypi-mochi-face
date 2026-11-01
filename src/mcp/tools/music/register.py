"""Music MCP tools: registered with a container-injected MusicPlayer without global get_instance."""

from typing import Any, Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .music_player import MusicPlayer

logger = get_logger()


def register_music_tools(
    add_tool: Callable[[McpTool], None], player: MusicPlayer
) -> None:
    """Register music tools with McpServer (closure holds container-injected player)."""

    async def search_and_play(args: dict[str, Any]) -> str:
        song_name = (args or {}).get("song_name", "")
        result = await player.search_and_play(song_name)
        return result.get("message", "Search and play completed")

    async def pause(args: dict[str, Any]) -> str:
        # Default to manual on MCP tool side; TTS ducking goes through EventBus rather than this tool
        result = await player.pause(source="manual")
        return result.get("message", "Paused")

    async def resume(args: dict[str, Any]) -> str:
        result = await player.resume()
        return result.get("message", "Playback resumed")

    async def stop(args: dict[str, Any]) -> str:
        result = await player.stop()
        return result.get("message", "Playback stopped")

    async def seek(args: dict[str, Any]) -> str:
        percent = int(args.get("percent", -1))
        position = int(args.get("position", -1))
        kwargs: dict[str, Any] = {}
        if percent >= 0:
            kwargs["percent"] = percent
        elif position >= 0:
            kwargs["position"] = position
        else:
            return "Please specify percent (0-100) or position (seconds)"
        result = await player.seek(**kwargs)
        return result.get("message", "Seek completed")

    async def get_status(args: dict[str, Any]) -> str:
        result = await player.get_status()
        return result.get("message", "Unable to get status")

    async def get_lyrics(args: dict[str, Any]) -> str:
        result = await player.get_lyrics()
        if result.get("status") == "success":
            lyrics = result.get("lyrics", [])
            return "Lyrics:\n" + "\n".join(lyrics)
        return result.get("message", "Failed to get lyrics")

    async def get_local_playlist(args: dict[str, Any]) -> str:
        force_refresh = args.get("force_refresh", False)
        result = await player.get_local_playlist(force_refresh)
        if result.get("status") == "success":
            playlist = result.get("playlist", [])
            total_count = result.get("total_count", 0)
            if playlist:
                text = f"Local Music Playlist ({total_count} songs in total):\n"
                text += "\n".join(playlist)
                return text
            return "No music files in local cache"
        return result.get("message", "Failed to get local playlist")

    tools: list[McpTool] = [
        McpTool(
            "music_player.search_and_play",
            (
                "Search for and play a specified song. Searches online by song title and automatically starts playback. "
                "If music is already playing, it automatically stops the current song and plays the new one. "
                "Use this tool when the user requests to play a specific song, e.g., 'play Shape of You' or 'play Jay Chou Rice Field'."
            ),
            PropertyList([Property("song_name", PropertyType.STRING)]),
            search_and_play,
        ),
        McpTool(
            "music_player.pause",
            (
                "Pause the currently playing music while preserving the playback position, which can later be resumed with resume. "
                "Must be called when the user says 'pause music', 'pause the song', etc. "
                "Important: Call this tool before replying to the user; otherwise music will automatically resume after TTS finishes."
            ),
            PropertyList(),
            pause,
        ),
        McpTool(
            "music_player.resume",
            (
                "Resume previously paused music from where it was paused. "
                "Call when the user says 'continue playing', 'resume music', 'unpause music', etc. "
                "Note: Music automatically pauses during TTS speech and resumes afterward without calling this tool. "
                "Only call this tool when the user explicitly asks to resume after a manual pause."
            ),
            PropertyList(),
            resume,
        ),
        McpTool(
            "music_player.stop",
            (
                "Completely stop and close music playback, resetting position to the beginning. "
                "Must be called when the user says 'stop music', 'turn off music', 'stop playing', etc. "
                "Difference from pause: stop completely terminates playback, whereas pause temporarily pauses and can be resumed."
            ),
            PropertyList(),
            stop,
        ),
        McpTool(
            "music_player.seek",
            (
                "[Dedicated for Progress / Seeking] Seek to a specified position in the current song. "
                "When the user says 'jump to 30%', 'seek to 20%', or 'jump to halfway': must use percent (0-100); "
                "the player automatically calculates the position in seconds from the total duration—do not query lyrics or guess the timestamp. "
                "When the user says 'jump to 2 minutes', 'seek to 90 seconds', or 'back to the beginning': use position (in seconds, starting from 0). "
                "For 'fast forward 30 seconds': call get_status first to get the current position, then call seek with position=current_seconds+30. "
                "Unrelated to get_lyrics; percentage seeking does not require lyrics."
            ),
            PropertyList(
                [
                    Property("percent", PropertyType.INTEGER, default_value=-1),
                    Property("position", PropertyType.INTEGER, default_value=-1),
                ]
            ),
            seek,
        ),
        McpTool(
            "music_player.get_status",
            (
                "Query current playback status: song title, playing/paused state, total duration (seconds), current position (seconds), and progress percentage. "
                "Use for questions like 'where is the song at now?', 'how long is this song?', or checking position before fast-forwarding. "
                "Do not use this tool in place of seek; call seek to jump to a position."
            ),
            PropertyList(),
            get_status,
        ),
        McpTool(
            "music_player.get_lyrics",
            (
                "Used exclusively to retrieve the lyric text of the current song. "
                "Call when the user asks 'what are the lyrics?' or 'what is it singing?'. "
                "Do NOT use for: progress seeking, jumping to a percentage, fast-forwarding/rewinding, or calculating playback position—"
                "use music_player.seek (with percent for percentage jumps) for those."
            ),
            PropertyList(),
            get_lyrics,
        ),
        McpTool(
            "music_player.get_local_playlist",
            (
                "Get the local music playlist showing all downloaded and cached songs. "
                "Return format: 'Title - Artist'. "
                "Use when the user asks 'what songs do I have?', 'local song list', 'what music is cached?', etc. "
                "Note: To play a song from the playlist, simply call search_and_play with the song title."
            ),
            PropertyList(
                [Property("force_refresh", PropertyType.BOOLEAN, default_value=False)]
            ),
            get_local_playlist,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d music MCP tools (container-injected MusicPlayer)", len(tools))
