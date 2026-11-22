"""MCP tool that changes the robot's eye color by voice ("đổi màu mắt sang màu tím")."""

from __future__ import annotations

import json
from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType
from src.ui.face_color import (
    MODE_EMOTION,
    MODE_RAINBOW,
    PALETTE,
    EyeColorState,
    color_label,
    parse_request,
)

logger = get_logger()

MODE_MESSAGES = {
    MODE_RAINBOW: "Đã bật mắt cầu vồng, màu mắt sẽ đổi dần liên tục",
    MODE_EMOTION: "Đã bật màu mắt theo cảm xúc: vui màu vàng, buồn xanh dương, giận màu đỏ",
}


def set_eye_color(eye_colors: EyeColorState, text: str) -> str:
    """Apply a spoken color or mode; returns the tool's JSON answer."""
    try:
        color, mode = parse_request(text)
    except ValueError:
        names = ", ".join(label.lower() for _key, label, _hex in PALETTE)
        return json.dumps(
            {
                "success": False,
                "message": f"Không biết màu '{text}'. Các màu có: {names}; "
                "hoặc 'cầu vồng', 'theo cảm xúc', 'mặc định'.",
            },
            ensure_ascii=False,
        )
    color, mode = eye_colors.set(color=color, mode=mode)
    message = MODE_MESSAGES.get(mode) or f"Đã đổi màu mắt sang {color_label(color).lower()}"
    logger.info("Eye color by voice: %r -> %s %s", text, color, mode)
    return json.dumps({"success": True, "color": color, "mode": mode, "message": message}, ensure_ascii=False)


def register_face_tools(add_tool: Callable[[McpTool], None], eye_colors: EyeColorState) -> None:
    names = ", ".join(f"'{label.lower()}'" for _key, label, _hex in PALETTE)
    add_tool(
        McpTool(
            "self.face.set_eye_color",
            "Đổi màu mắt trên màn hình mặt robot. Dùng khi người dùng nói: 'đổi màu mắt "
            "sang màu tím', 'cho mắt màu xanh lá', 'mắt màu đỏ đi', 'bật mắt cầu vồng', "
            "'mắt đổi màu theo cảm xúc', 'tắt cầu vồng', 'về màu mắt mặc định'. Tham số "
            f"color là tên màu tiếng Việt: {names}; hoặc mã hex như '#33aaff'; hoặc chế "
            "độ: 'cầu vồng' (màu đổi dần liên tục), 'theo cảm xúc' (mỗi biểu cảm một màu: "
            "vui vàng, buồn xanh dương, giận đỏ), 'cố định' (giữ màu hiện tại, dùng khi "
            "người dùng muốn tắt cầu vồng/cảm xúc), 'mặc định' (về màu trắng). Màu được lưu, "
            "khởi động lại vẫn giữ.",
            PropertyList([Property("color", PropertyType.STRING)]),
            lambda args: set_eye_color(eye_colors, str(args.get("color", ""))),
        )
    )
