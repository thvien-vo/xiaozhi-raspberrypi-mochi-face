"""detect_objects MCP tool: answers from the on-device YOLO detector."""

import asyncio
import json
from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, PropertyList

logger = get_logger()


def register_vision_tools(add_tool: Callable[[McpTool], None], vision) -> None:
    async def detect_objects(arguments: dict) -> str:
        result = await asyncio.to_thread(vision.query)
        return json.dumps(result, ensure_ascii=False)

    add_tool(
        McpTool(
            "detect_objects",
            (
                "[Live Object Detection] Fast, on-device check of what is in front of the "
                "robot's camera right now. Call this tool when the user asks what you can "
                "see, what is in front of you, whether someone or something is there, or "
                "how many of something are visible.\n"
                "Examples: 'What's in front of you?', 'What do you see?', 'Is anyone "
                "there?', 'How many people are here?', 'Is there a cup on the left?'.\n"
                "Takes no parameters and answers immediately, without uploading an image.\n"
                "Returns JSON: `summary` is a ready sentence; `objects` lists each `label` "
                "with its `count`, best `confidence` (0-1) and `positions` (left, center, "
                "right, from the camera's viewpoint). Describe the result naturally and "
                "do not read out numbers such as confidence.\n"
                "It recognizes 80 common object types (people, animals, vehicles, furniture, "
                "food, household items) and cannot read text or identify individuals. Use "
                "take_photo instead for reading text, describing a scene in detail, or "
                "identifying something it does not cover."
            ),
            PropertyList(),
            detect_objects,
        )
    )
    logger.info("Registered detect_objects")
