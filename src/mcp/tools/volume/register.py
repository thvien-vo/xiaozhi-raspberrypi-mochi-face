"""Volume MCP tools: injects VolumeController via register_volume_tools without module-level singletons."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from src.audio_codecs.levels import CHANNEL_LABELS, CHANNELS, AudioLevels, get_audio_levels

from .volume_controller import VolumeController

logger = get_logger()


def create_volume_controller() -> VolumeController | None:
    """Create a volume controller; returns None if dependencies are missing or initialization fails."""
    if not VolumeController.check_dependencies():
        return None
    try:
        return VolumeController()
    except Exception as e:
        logger.error(f"Volume controller initialization failed: {e}", exc_info=True)
        return None


def register_volume_tools(
    add_tool: Callable[[McpTool], None],
    controller: VolumeController | None = None,
    levels: AudioLevels | None = None,
) -> None:
    """Register the volume MCP tools.

    They act on the shared AudioLevels (software gain in the mixer: master, voice, music,
    effects, mute), the same numbers the web panel shows, so a voice command moves the web
    sliders and the other way round. `controller` is the old operating-system backend, used
    only to report the system volume when it is injected; the tools no longer change it.
    """
    if levels is None:
        levels = get_audio_levels()

    def status_line(channel: str) -> str:
        label = CHANNEL_LABELS[channel].lower()
        text = f"Âm lượng {label}: {levels.get(channel)}%"
        if levels.muted:
            text += " (đang tắt tiếng)"
        if not levels.available:
            text += ". Lưu ý: hiện chưa có loa phát được"
        return text

    async def set_volume(args: dict[str, Any]) -> str:
        channel = str(args.get("channel", "") or "master").strip().lower()
        if channel not in CHANNELS:
            return f"Kênh '{channel}' không có; chọn một trong: {', '.join(CHANNELS)}"
        mute = str(args.get("mute", "") or "").strip().lower()
        volume = int(args.get("volume", -1))
        change = int(args.get("change", 0))
        logger.info(f"[VolumeTools] channel={channel} volume={volume} change={change} mute={mute!r}")
        unmuted = False
        if mute in ("on", "true", "yes", "1"):
            levels.set_muted(True)
            return "Đã tắt tiếng"
        if mute in ("off", "false", "no", "0"):
            levels.set_muted(False)
            unmuted = True
        elif mute:
            return "mute phải là on hoặc off"
        # "bật tiếng" may come together with a level ("bật tiếng và nhỏ lại"): do both
        if volume >= 0:
            levels.set(channel, volume)
        elif change != 0:
            levels.change(channel, change)
        text = status_line(channel)
        return f"Đã bật tiếng. {text}" if unmuted else text

    async def get_volume(args: dict[str, Any]) -> int:
        return levels.get("master")

    async def get_volume_status(args: dict[str, Any]) -> str:
        status = levels.snapshot()
        status["volume"] = status["master"]
        status["available"] = bool(status["available"])
        if controller is not None:
            try:
                status["system_volume"] = await asyncio.to_thread(controller.get_volume)
            except Exception:
                pass
        return json.dumps(status, ensure_ascii=False)

    tools: list[McpTool] = [
        McpTool(
            "self.audio_speaker.set_volume",
            (
                "Chỉnh âm lượng loa của robot (0-100). MẶC ĐỊNH chỉnh âm lượng TỔNG (mọi âm thanh ra loa).\n"
                "Dùng khi người dùng nói: to lên, nhỏ xuống, lớn hơn, nhỏ hơn, âm lượng 50%, tắt tiếng, bật tiếng, "
                "im đi, nói to lên, nói nhỏ lại.\n"
                "- 'to lên', 'lớn hơn': change=10 (nói 'to hơn nhiều': 20; 'hơi to': 5). 'nhỏ xuống': change=-10.\n"
                "- 'âm lượng 50%', 'đặt âm lượng 30': volume=50 / volume=30.\n"
                "- 'tắt tiếng', 'im lặng': mute='on'. 'bật tiếng', 'mở tiếng lại': mute='off'.\n"
                "- channel: 'master' (mặc định), 'voice' (chỉ giọng nói của robot: 'robot nói nhỏ lại'), "
                "'music' (nhạc), 'effects'. Chỉ khi người dùng nói rõ về NHẠC / bài hát mới dùng "
                "music_player.set_volume hoặc channel='music'.\n"
                "Đọc kết quả trả về và nói lại mức mới cho người dùng."
            ),
            PropertyList(
                [
                    Property("volume", PropertyType.INTEGER, default_value=-1, min_value=-1, max_value=100),
                    Property("change", PropertyType.INTEGER, default_value=0, min_value=-100, max_value=100),
                    Property("mute", PropertyType.STRING, default_value=""),
                    Property("channel", PropertyType.STRING, default_value="master"),
                ]
            ),
            set_volume,
        ),
        McpTool(
            "self.audio_speaker.get_volume",
            (
                "Get the current master speaker volume (0-100).\n"
                "Use when user asks about: current volume, volume level, how loud, what's the volume.\n"
                "Examples: 'what is the current volume?', 'âm lượng bao nhiêu?'."
            ),
            PropertyList(),
            get_volume,
        ),
        McpTool(
            "self.audio_speaker.get_volume_status",
            (
                "Get the speaker volume status as JSON: master, voice, music, effects (0-100 each), "
                "muted (bool), available (a speaker can play), device, message."
            ),
            PropertyList(),
            get_volume_status,
        ),
    ]

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d volume MCP tools (software levels)", len(tools))
