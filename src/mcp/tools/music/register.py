"""Music MCP tools: registered with a container-injected MusicPlayer without global get_instance."""

from typing import Any, Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .music_player import MusicPlayer

logger = get_logger()


def register_music_tools(
    add_tool: Callable[[McpTool], None], player: MusicPlayer, dancer=None
) -> None:
    """Register music tools with McpServer (closure holds container-injected player).

    dancer: MusicDancer (head dances to the music), when the robot has servos.
    """

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
                text = f"Thư viện nhạc trên robot ({total_count} bài):\n"
                text += "\n".join(playlist)
                return text
            return result.get("message", "Thư viện nhạc trên robot đang trống")
        return result.get("message", "Failed to get local playlist")

    async def next_track(args: dict[str, Any]) -> str:
        return (await player.next_track())["message"]

    async def previous_track(args: dict[str, Any]) -> str:
        return (await player.previous_track())["message"]

    async def set_volume(args: dict[str, Any]) -> str:
        volume = int(args.get("volume", -1))
        if volume >= 0:
            return player.set_volume(volume)["message"]
        change = int(args.get("change", 0))
        if change == 0:
            return f"Âm lượng nhạc: {player.volume}%"
        return player.change_volume(change)["message"]

    async def set_mode(args: dict[str, Any]) -> str:
        repeat = str(args.get("repeat", "") or "").strip().lower()
        shuffle = str(args.get("shuffle", "") or "").strip().lower()
        if repeat and repeat not in ("off", "all", "one"):
            return "repeat phải là off, all hoặc one"
        if shuffle and shuffle not in ("on", "off"):
            return "shuffle phải là on hoặc off"
        return player.set_modes(repeat or None, None if not shuffle else shuffle == "on")["message"]

    async def dance(args: dict[str, Any]) -> str:
        on = bool(args.get("enabled", True))
        if dancer is None:
            return "Robot không có servo nên không nhảy được"
        player.set_dance(on)
        if not on:
            return "Đã tắt nhảy theo nhạc"
        return "Đã bật nhảy theo nhạc" + ("" if player.sounding else "; robot sẽ nhảy khi có nhạc phát")

    tools: list[McpTool] = [
        McpTool(
            "music_player.search_and_play",
            (
                "Phát nhạc từ thư viện trên robot. Dùng cho MỌI yêu cầu bật/mở/phát nhạc: 'bật nhạc', 'mở bài ...', "
                "'phát nhạc của ...', 'play <song>'. song_name: tên bài và/hoặc ca sĩ đúng như người dùng nói (tìm gần "
                "đúng, không cần dấu); để trống nếu họ không nêu bài cụ thể. Đang có bài khác thì tự chuyển. "
                "Đọc kỹ kết quả: nếu có chữ 'CHƯA' thì nhạc KHÔNG phát, phải nói thật lý do cho người dùng."
            ),
            PropertyList([Property("song_name", PropertyType.STRING, default_value="")]),
            search_and_play,
        ),
        McpTool(
            "music_player.next",
            "Chuyển sang bài tiếp theo. Dùng khi nghe 'bài tiếp', 'bài khác', 'qua bài', 'next'.",
            PropertyList(),
            next_track,
        ),
        McpTool(
            "music_player.previous",
            "Quay lại bài trước. Dùng khi nghe 'bài trước', 'quay lại bài vừa rồi', 'previous'.",
            PropertyList(),
            previous_track,
        ),
        McpTool(
            "music_player.set_volume",
            (
                "Chỉnh âm lượng của NHẠC (0-100). Khi đang phát nhạc mà nghe 'to lên', 'nhỏ xuống', 'mở to', 'vặn nhỏ': "
                "gọi với change=15 hoặc change=-15. 'nhạc to 50%': volume=50. Giọng nói của robot thì dùng "
                "self.audio_speaker.set_volume."
            ),
            PropertyList(
                [
                    Property("volume", PropertyType.INTEGER, default_value=-1, min_value=-1, max_value=100),
                    Property("change", PropertyType.INTEGER, default_value=0, min_value=-100, max_value=100),
                ]
            ),
            set_volume,
        ),
        McpTool(
            "music_player.set_mode",
            (
                "Lặp và trộn bài. repeat: off (không lặp), all (lặp cả danh sách), one (lặp một bài); "
                "shuffle: on/off (trộn bài, phát ngẫu nhiên). Để trống phần không đổi."
            ),
            PropertyList(
                [
                    Property("repeat", PropertyType.STRING, default_value=""),
                    Property("shuffle", PropertyType.STRING, default_value=""),
                ]
            ),
            set_mode,
        ),
        McpTool(
            "music_player.dance",
            "Bật/tắt chế độ đầu robot nhảy theo nhạc: 'nhảy theo nhạc đi' (enabled=true), 'thôi đừng nhảy nữa' (false).",
            PropertyList([Property("enabled", PropertyType.BOOLEAN, default_value=True)]),
            dance,
        ),
        McpTool(
            "music_player.pause",
            (
                "Pause the currently playing music while preserving the playback position, which can later be resumed with resume. "
                "Must be called when the user says 'tạm dừng nhạc', 'dừng một chút', 'pause music', etc."
            ),
            PropertyList(),
            pause,
        ),
        McpTool(
            "music_player.resume",
            (
                "Resume previously paused music from where it was paused. "
                "Call when the user says 'phát tiếp', 'mở nhạc lại', 'resume music', etc. "
                "Music turns down by itself while you speak: do not call this for that."
            ),
            PropertyList(),
            resume,
        ),
        McpTool(
            "music_player.stop",
            (
                "Completely stop and close music playback, resetting position to the beginning. "
                "Must be called when the user says 'dừng nhạc', 'tắt nhạc', 'thôi không nghe nữa', 'stop music', etc. "
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
                "List the songs in the robot's music library ('Title - Artist'). "
                "Use for 'có những bài nào?', 'danh sách nhạc', 'what songs do you have?'. Read out only a few. "
                "To play one, call search_and_play with its title."
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
