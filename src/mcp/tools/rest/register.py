"""MCP tool that puts the robot to rest by voice ("đi ngủ đi").

Waking is only possible from the web panel (the robot does not listen while resting), so
there is deliberately no tool for it.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from src.core.rest_mode import RestController
from src.logging import get_logger
from src.mcp.tooling import McpTool, PropertyList

logger = get_logger()


def go_to_rest(rest: RestController) -> str:
    """Arrange for the robot to rest once its spoken answer is over; returns the tool's JSON."""
    result = rest.request_after_reply()
    if not result.get("ok"):
        return json.dumps({"success": False, "message": result.get("message", "Không đi nghỉ được")}, ensure_ascii=False)
    if not result.get("changed", True) and "đang nghỉ" in result.get("message", ""):
        return json.dumps({"success": True, "message": "Robot đang nghỉ rồi."}, ensure_ascii=False)
    logger.info("Rest requested by voice")
    return json.dumps(
        {
            "success": True,
            "message": "Đã sẵn sàng đi nghỉ. Hãy chúc ngủ ngon thật ngắn gọn (một câu); ngay sau đó "
            "robot sẽ ngừng nghe và ngừng nói cho tới khi được đánh thức từ trang web.",
        },
        ensure_ascii=False,
    )


def register_rest_tools(add_tool: Callable[[McpTool], None], rest: RestController) -> None:
    add_tool(
        McpTool(
            "self.robot.go_to_rest",
            "Cho robot đi nghỉ (ngủ): robot ngừng nghe, ngừng nói, mặt ngủ, đầu cúi xuống. Dùng khi "
            "người dùng nói: 'đi ngủ đi', 'nghỉ đi', 'ngủ đi', 'đi nghỉ đi', 'im lặng và đi ngủ'. "
            "Chỉ đánh thức lại được từ trang web điều khiển, nên chỉ dùng khi người dùng thật sự "
            "muốn robot đi nghỉ. Sau khi gọi, chúc ngủ ngon bằng một câu ngắn.",
            PropertyList([]),
            lambda args: go_to_rest(rest),
        )
    )
