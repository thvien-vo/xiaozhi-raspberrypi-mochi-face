"""Linux system application launcher.

Provides application launching functionality on Linux platforms.
All subprocess calls use list arguments without shell=True.
"""

import os
import subprocess

from src.logging import get_logger

logger = get_logger()


def launch_application(app_name: str) -> bool:
    """Launch an application on Linux.

    Args:
        app_name: Application name

    Returns:
        bool: Whether the launch succeeded
    """
    try:
        logger.info(f"[LinuxLauncher] Launching application: {app_name}")

        # Method 1: Launch directly using the application name
        try:
            subprocess.Popen(
                [app_name],
                start_new_session=True,
            )
            logger.info(f"[LinuxLauncher] Direct launch succeeded: {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[LinuxLauncher] Direct launch failed: {app_name}")

        # Method 2: Locate application path using which
        try:
            result = subprocess.run(
                ["which", app_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                app_path = result.stdout.strip()
                subprocess.Popen(
                    [app_path],
                    start_new_session=True,
                )
                logger.info(f"[LinuxLauncher] Launched via which: {app_name}")
                return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[LinuxLauncher] Launch via which failed: {app_name}")

        # Method 3: Use xdg-open (for desktop environments)
        try:
            subprocess.Popen(
                ["xdg-open", app_name],
                start_new_session=True,
            )
            logger.info(f"[LinuxLauncher] Launched via xdg-open: {app_name}")
            return True
        except (OSError, subprocess.SubprocessError):
            logger.debug(f"[LinuxLauncher] Launch via xdg-open failed: {app_name}")

        # Method 4: Try common application paths
        common_paths = [
            f"/usr/bin/{app_name}",
            f"/usr/local/bin/{app_name}",
            f"/opt/{app_name}/{app_name}",
            f"/snap/bin/{app_name}",
        ]

        for path in common_paths:
            if os.path.exists(path):
                subprocess.Popen(
                    [path],
                    start_new_session=True,
                )
                logger.info(
                    f"[LinuxLauncher] Launched via common path: {app_name} ({path})"
                )
                return True

        # Method 5: Try launching via .desktop file
        desktop_dirs = [
            "/usr/share/applications",
            "/usr/local/share/applications",
            os.path.expanduser("~/.local/share/applications"),
        ]

        for desktop_dir in desktop_dirs:
            desktop_file = os.path.join(desktop_dir, f"{app_name}.desktop")
            if os.path.exists(desktop_file):
                subprocess.Popen(
                    ["gtk-launch", f"{app_name}.desktop"],
                    start_new_session=True,
                )
                logger.info(f"[LinuxLauncher] Launched via desktop file: {app_name}")
                return True

        logger.warning(f"[LinuxLauncher] All Linux launch methods failed: {app_name}")
        return False

    except Exception as e:
        logger.error(f"[LinuxLauncher] Linux launch failed: {e}", exc_info=True)
        return False
