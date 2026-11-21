"""MCP tools that move the robot's pan/tilt head by voice."""

from __future__ import annotations

import json
from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

logger = get_logger()

DEFAULT_STEP_DEG = 20


def register_servo_tools(add_tool: Callable[[McpTool], None], controller, tracker=None) -> None:
    def status(message: str) -> str:
        pan, tilt = controller.angles
        connected = controller.connected
        result = {
            "success": connected,
            "pan_deg": round(pan),
            "tilt_deg": round(tilt),
            "tracking": bool(tracker and tracker.enabled),
            "message": message
            if connected
            else message + " (chưa kết nối bo điều khiển servo, đầu sẽ quay khi có kết nối)",
        }
        return json.dumps(result, ensure_ascii=False)

    def manual(move: Callable[[], object], message: str) -> str:
        if tracker is not None:
            tracker.hold()
        move()
        return status(message)

    def degrees_prop() -> PropertyList:
        return PropertyList(
            [
                Property(
                    "degrees",
                    PropertyType.INTEGER,
                    default_value=DEFAULT_STEP_DEG,
                    min_value=1,
                    max_value=90,
                )
            ]
        )

    def turn(dpan_sign: int, dtilt_sign: int, label: str):
        def callback(args: dict) -> str:
            deg = int(args.get("degrees", DEFAULT_STEP_DEG))
            return manual(
                lambda: controller.nudge(dpan_sign * deg, dtilt_sign * deg),
                f"Đã {label} {deg} độ",
            )

        return callback

    common = (
        " Góc tính theo đầu robot; đầu chỉ xoay ngang tối đa 90 độ mỗi bên và gật "
        "tối đa 45 độ lên/xuống, vượt quá sẽ dừng ở giới hạn. Sau lệnh này đầu giữ yên "
        "vài giây rồi mới tiếp tục tự bám theo người."
    )

    tools = [
        McpTool(
            "self.head.turn_left",
            "Quay đầu robot sang TRÁI (bên trái của chính robot) thêm một góc, mặc định "
            "20 độ. Dùng khi người dùng nói: 'quay trái', 'nhìn sang trái', 'quay sang "
            "trái 30 độ'. Tham số degrees: số độ cần quay (1-90)." + common,
            degrees_prop(),
            turn(+1, 0, "quay sang trái"),
        ),
        McpTool(
            "self.head.turn_right",
            "Quay đầu robot sang PHẢI (bên phải của chính robot) thêm một góc, mặc định "
            "20 độ. Dùng khi người dùng nói: 'quay phải', 'nhìn sang phải', 'quay sang "
            "phải 45 độ'. Tham số degrees: số độ cần quay (1-90)." + common,
            degrees_prop(),
            turn(-1, 0, "quay sang phải"),
        ),
        McpTool(
            "self.head.look_up",
            "Ngẩng đầu robot LÊN thêm một góc, mặc định 20 độ. Dùng khi người dùng nói: "
            "'ngẩng lên', 'nhìn lên', 'ngước lên 10 độ'. Tham số degrees: số độ (1-45)."
            + common,
            degrees_prop(),
            turn(0, +1, "ngẩng lên"),
        ),
        McpTool(
            "self.head.look_down",
            "Cúi đầu robot XUỐNG thêm một góc, mặc định 20 độ. Dùng khi người dùng nói: "
            "'cúi xuống', 'nhìn xuống', 'gật xuống 10 độ'. Tham số degrees: số độ (1-45)."
            + common,
            degrees_prop(),
            turn(0, -1, "cúi xuống"),
        ),
        McpTool(
            "self.head.look_straight",
            "Đưa đầu robot về vị trí nhìn thẳng phía trước (giữa). Dùng khi người dùng "
            "nói: 'nhìn thẳng', 'quay về giữa', 'nhìn về phía trước'. Không có tham số.",
            PropertyList(),
            lambda _args: manual(controller.center, "Đã nhìn thẳng"),
        ),
        McpTool(
            "self.head.set_tracking",
            "Bật hoặc tắt chế độ tự quay đầu bám theo người (dùng camera nhận diện người "
            "lớn nhất trong khung hình). Dùng khi người dùng nói: 'nhìn theo tôi', 'bám "
            "theo tôi', 'đi theo mặt tôi' (enabled=true) hoặc 'đừng nhìn theo nữa', 'tắt "
            "bám người', 'đứng yên' (enabled=false). Tham số enabled: true để bật, false "
            "để tắt.",
            PropertyList([Property("enabled", PropertyType.BOOLEAN)]),
            lambda args: set_tracking(bool(args["enabled"])),
        ),
    ]

    def set_tracking(enabled: bool) -> str:
        if tracker is None:
            return json.dumps(
                {"success": False, "message": "Chế độ bám người không khả dụng (camera/YOLO đang tắt)"},
                ensure_ascii=False,
            )
        tracker.set_enabled(enabled)
        return status("Đã bật bám theo người" if enabled else "Đã tắt bám theo người")

    for tool in tools:
        add_tool(tool)
    logger.info("Registered %d head (servo) tools", len(tools))
