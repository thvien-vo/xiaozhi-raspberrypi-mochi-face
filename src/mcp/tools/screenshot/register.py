"""Screenshot MCP tool registration and factory."""

import asyncio
import json
from typing import Callable

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .screenshot_camera import ScreenshotCamera

logger = get_logger()


def create_screenshot_camera() -> ScreenshotCamera:
    """Create a desktop screenshot camera instance."""
    return ScreenshotCamera()


def register_screenshot_tools(
    add_tool: Callable[[McpTool], None],
    photo_camera=None,
) -> None:
    """Register take_screenshot.

    photo_camera: Optional, used to reuse explain URL/token during analysis (same camera as take_photo).
    """
    camera = create_screenshot_camera()
    if photo_camera is not None and hasattr(photo_camera, "get_explain_url"):
        # Share vision configuration with photo camera (if configured)
        try:
            url = getattr(photo_camera, "explain_url", None) or getattr(
                photo_camera, "_explain_url", None
            )
            token = getattr(photo_camera, "explain_token", None) or getattr(
                photo_camera, "_explain_token", None
            )
            if url and hasattr(camera, "set_explain_url"):
                camera.set_explain_url(url)
            if token and hasattr(camera, "set_explain_token"):
                camera.set_explain_token(token)
        except Exception as e:
            logger.debug(f"Failed to sync vision config to screenshot camera: {e}", exc_info=True)

    # Attach reference if analyze needs to fall back to photo camera's explain settings
    camera._photo_camera_ref = photo_camera  # type: ignore[attr-defined]

    async def take_screenshot(arguments: dict) -> str:
        logger.info(
            f"Using screenshot camera implementation: {camera.__class__.__name__}"
        )

        question = arguments.get("question", "")
        display_id = arguments.get("display", None)

        if display_id:
            if isinstance(display_id, str):
                if display_id.lower() in ["main", "primary", "main display", "laptop", "internal"]:
                    display_id = "main"
                elif display_id.lower() in [
                    "secondary",
                    "external",
                    "secondary display",
                    "external display",
                    "second",
                ]:
                    display_id = "secondary"
                else:
                    try:
                        display_id = int(display_id)
                    except ValueError:
                        logger.warning(
                            f"Invalid display parameter: {display_id}, using default"
                        )
                        display_id = None

        logger.info(
            f"Taking screenshot with question: {question}, display: {display_id}"
        )

        success = await asyncio.to_thread(camera.capture, display_id)
        if not success:
            logger.error("Failed to capture screenshot")
            return json.dumps(
                {"success": False, "message": "Failed to capture screenshot"}
            )

        logger.info("Screenshot captured, starting analysis...")
        return await asyncio.to_thread(camera.analyze, question)

    add_tool(
        McpTool(
            "take_screenshot",
            (
                "[Desktop Screenshot / Screen Analysis] Call this tool when the user asks to take a screenshot, "
                "look at the desktop, analyze the screen, check what is on the desktop, view the current interface, "
                "analyze the current page, read screen content, or perform screen OCR. "
                "Features: (1) Capture the full desktop screen; (2) Screen content recognition and analysis; "
                "(3) Screen OCR text extraction; (4) UI element analysis; (5) Application identification; "
                "(6) Error message screenshot analysis; (7) Desktop status inspection; (8) Multi-display screenshot capture. "
                "Parameters: { question: 'Question you want answered about the desktop/screen', display: 'Display selection (optional)' }; "
                "Valid values for display: 'main'/'primary'/'laptop' (primary display), 'secondary'/'external'/'second' (secondary display), or leave empty (all displays); "
                "Applicable scenarios: desktop screenshot capture, screen analysis, UI troubleshooting, application status inspection, error screenshot analysis, etc. "
                "Note: This tool captures the desktop screen; ensure the user has consented to taking a screenshot."
            ),
            PropertyList(
                [
                    Property("question", PropertyType.STRING),
                    Property("display", PropertyType.STRING, default_value=""),
                ]
            ),
            take_screenshot,
        )
    )
    logger.info("Registered take_screenshot (no global singleton)")
