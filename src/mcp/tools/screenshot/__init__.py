"""Screenshot tools for MCP.

Screenshot instances are created/injected by the caller.
"""

from .register import create_screenshot_camera, register_screenshot_tools

__all__ = [
    "create_screenshot_camera",
    "register_screenshot_tools",
]
