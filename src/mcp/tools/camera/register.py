"""Camera MCP tool registration and factory."""

import asyncio
import json
from collections.abc import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType
from src.utils.config_manager import get_config

from .normal_camera import NormalCamera
from .vl_camera import VLCamera

logger = get_logger()


def create_camera():
    """
    Create and return a camera implementation instance based on configuration.
    """
    config = get_config()

    vl_key = config.get_config("CAMERA.VLapi_key")
    vl_url = config.get_config("CAMERA.Local_VL_url")

    if vl_key and vl_url:
        logger.info(f"Initializing VL Camera with URL: {vl_url}")
        return VLCamera()

    logger.info("VL configuration not found, using normal Camera implementation")
    return NormalCamera()


def register_camera_tools(add_tool: Callable[[McpTool], None], camera) -> None:
    async def take_photo(arguments: dict) -> str:
        logger.info(f"Using camera implementation: {camera.__class__.__name__}")
        question = arguments.get("question", "")
        logger.info(f"Taking photo with question: {question}")

        success = await asyncio.to_thread(camera.capture)
        if not success:
            logger.error("Failed to capture photo")
            return json.dumps({"success": False, "message": "Failed to capture photo"})

        logger.info("Photo captured, starting analysis...")
        return await asyncio.to_thread(camera.analyze, question)

    add_tool(
        McpTool(
            "take_photo",
            (
                "[Photo Capture & Visual Recognition] Call this tool when the user asks to take a photo, "
                "look at something, identify an object, recognize text, or inspect what is in front of the camera.\n"
                "Function: Captures a photo from the camera, analyzes its visual content, and answers questions about it.\n"
                "Use cases:\n"
                "1. User asks to take a photo or see what is in front of the camera (e.g., 'Help me see what this is', 'Take a photo', 'Look at what is in front')\n"
                "2. Object or scene recognition ('What is this object?', 'Identify this for me')\n"
                "3. Optical Character Recognition / OCR ('Read the text on this', 'Extract the text', 'What does this say?')\n"
                "4. Visual question answering ('How many people are in the picture?', 'What color is this?', 'What is shown here?')\n\n"
                "Parameters:\n"
                "- question: String, the question the user wants answered about the photo\n\n"
                "Usage tip: When the user says 'look', 'take a look', 'what is this', or similar expressions, prefer using this tool to capture and analyze a photo.\n"
                "Args: `question` - The question that you want to ask about the photo.\n"
                "Return: A JSON object that provides the photo information.\n"
                "Examples: 'take a photo', 'what is this', 'help me look at this', 'read the text in front of the camera'."
            ),
            PropertyList([Property("question", PropertyType.STRING)]),
            take_photo,
        )
    )
    logger.info("Registered take_photo (camera injected by container)")
