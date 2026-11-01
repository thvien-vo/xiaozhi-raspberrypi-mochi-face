"""Application termination and running process management.

Provides cross-platform process termination and listing via process_manager (psutil).
"""

import asyncio
import json
from typing import Any

from src.logging import get_logger

from .process_manager import kill_application_by_name
from .process_manager import list_running_applications as _list_apps

logger = get_logger()


async def kill_application(args: dict[str, Any]) -> bool:
    """Close an application.

    Args:
        args: Parameter dictionary containing application name
            - app_name: Application name
            - force: Whether to force close (optional, defaults to False)

    Returns:
        Whether the application was closed successfully
    """
    try:
        app_name = args["app_name"]
        force = args.get("force", False)
        logger.info(f"[AppKiller] Attempting to close application: {app_name}, force: {force}")

        success = await asyncio.to_thread(kill_application_by_name, app_name, force)

        if success:
            logger.info(f"[AppKiller] Successfully closed application: {app_name}")
        else:
            logger.warning(f"[AppKiller] Failed to close application: {app_name}")

        return success

    except Exception as e:
        logger.error(f"[AppKiller] Error closing application: {e}", exc_info=True)
        return False


async def list_running_applications(args: dict[str, Any]) -> str:
    """List all currently running applications.

    Args:
        args: Dictionary containing listing parameters
            - filter_name: Filter by application name (optional)

    Returns:
        JSON-formatted list of running applications
    """
    try:
        filter_name = args.get("filter_name", "")
        logger.info(f"[AppKiller] Listing running applications, filter: {filter_name}")

        apps = await asyncio.to_thread(_list_apps, filter_name)

        result = {
            "success": True,
            "total_count": len(apps),
            "applications": apps[:50],
            "message": f"Found {len(apps)} running applications",
        }

        logger.info(f"[AppKiller] Listing complete, found {len(apps)} running applications")
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        error_msg = f"Failed to list running applications: {e}"
        logger.error(f"[AppKiller] {error_msg}", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "total_count": 0,
                "applications": [],
                "message": error_msg,
            },
            ensure_ascii=False,
        )
