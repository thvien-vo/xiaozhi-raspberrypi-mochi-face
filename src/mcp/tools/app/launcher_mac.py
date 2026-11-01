"""macOS system application launcher.

Provides application launching functionality on macOS platforms.
All subprocess calls use list arguments without shell=True.
"""

import os
import subprocess

from src.logging import get_logger

logger = get_logger()


def launch_application(app_name: str) -> bool:
    """Launch an application on macOS.

    Args:
        app_name: Application name

    Returns:
        bool: Whether the launch succeeded
    """
    try:
        logger.info(f"[MacLauncher] Launching application: {app_name}")

        # Method 1: Use open -a command (safe list form)
        try:
            subprocess.Popen(
                ["open", "-a", app_name],
                start_new_session=True,
            )
            logger.info(f"[MacLauncher] Launched via open -a: {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[MacLauncher] open -a launch failed: {app_name}")

        # Method 2: Launch directly using the application name
        try:
            subprocess.Popen(
                [app_name],
                start_new_session=True,
            )
            logger.info(f"[MacLauncher] Direct launch succeeded: {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[MacLauncher] Direct launch failed: {app_name}")

        # Method 3: Try Applications directory
        app_path = f"/Applications/{app_name}.app"
        if os.path.exists(app_path):
            subprocess.Popen(
                ["open", app_path],
                start_new_session=True,
            )
            logger.info(f"[MacLauncher] Launched via Applications directory: {app_name}")
            return True

        # Method 4: Try open -a in background mode as final fallback (without osascript)
        # Previous osascript + f-string concatenation had AppleScript injection risks and was removed
        try:
            subprocess.Popen(
                ["open", "-a", app_name, "--background"],
                start_new_session=True,
            )
            logger.info(f"[MacLauncher] Launched via open -a (background mode): {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[MacLauncher] open -a (background mode) launch failed: {app_name}")

        logger.warning(f"[MacLauncher] All macOS launch methods failed: {app_name}")
        return False

    except Exception as e:
        logger.error(f"[MacLauncher] macOS launch failed: {e}", exc_info=True)
        return False
