"""Windows system application launcher.

Provides application launching functionality on Windows platforms.
All subprocess calls use list arguments without shell=True.
"""

import base64
import os
import subprocess

from src.logging import get_logger

logger = get_logger()

# Windows process creation flag (available on Windows only)
_DETACHED_PROCESS = 0x00000008


def launch_application(app_name: str) -> bool:
    """Launch an application on Windows.

    Args:
        app_name: Application name

    Returns:
        bool: Whether the launch succeeded
    """
    try:
        logger.info(f"[WindowsLauncher] Launching application: {app_name}")

        # Try different launch methods in order of priority
        launch_methods = [
            ("PowerShell Start-Process", _try_powershell_start),
            ("os.startfile", _try_os_startfile),
            ("Registry lookup", _try_registry_launch),
            ("Common paths", _try_common_paths),
            ("where command", _try_where_command),
            ("UWP app", _try_uwp_launch),
        ]

        for method_name, method_func in launch_methods:
            try:
                if method_func(app_name):
                    logger.info(f"[WindowsLauncher] {method_name} launched successfully: {app_name}")
                    return True
                else:
                    logger.debug(f"[WindowsLauncher] {method_name} launch failed: {app_name}")
            except Exception as e:
                logger.debug(f"[WindowsLauncher] {method_name} exception: {e}")

        logger.warning(f"[WindowsLauncher] All Windows launch methods failed: {app_name}")
        return False

    except Exception as e:
        logger.error(f"[WindowsLauncher] Windows launch exception: {e}", exc_info=True)
        return False


def launch_uwp_app_by_path(uwp_path: str) -> bool:
    """Launch an application via its UWP path.

    Args:
        uwp_path: UWP application path (in shell:AppsFolder\\... format)

    Returns:
        bool: Whether the launch succeeded
    """
    try:
        if uwp_path.startswith("shell:AppsFolder\\"):
            subprocess.Popen(
                ["explorer.exe", uwp_path],
                creationflags=_DETACHED_PROCESS,
            )
            logger.info(f"[WindowsLauncher] UWP app launched successfully: {uwp_path}")
            return True
        else:
            return False
    except Exception as e:
        logger.error(f"[WindowsLauncher] UWP app launch failed: {e}", exc_info=True)
        return False


def launch_shortcut(shortcut_path: str) -> bool:
    """Launch a shortcut file.

    Args:
        shortcut_path: Shortcut file path

    Returns:
        bool: Whether the launch succeeded
    """
    try:
        os.startfile(shortcut_path)
        logger.info(f"[WindowsLauncher] Shortcut launched successfully: {shortcut_path}")
        return True
    except Exception as e:
        logger.error(f"[WindowsLauncher] Shortcut launch failed: {e}", exc_info=True)
        return False


def _try_powershell_start(app_name: str) -> bool:
    """Try launching the application using PowerShell Start-Process."""
    try:
        result = subprocess.run(
            ["powershell", "-Command", "Start-Process", "-FilePath", app_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _try_os_startfile(app_name: str) -> bool:
    """Try launching the application using os.startfile."""
    try:
        os.startfile(app_name)
        return True
    except OSError:
        return False


def _try_registry_launch(app_name: str) -> bool:
    """Try finding and launching the application via the Windows Registry."""
    try:
        executable_path = _find_executable_in_registry(app_name)
        if executable_path:
            subprocess.Popen(
                [executable_path],
                creationflags=_DETACHED_PROCESS,
            )
            return True
    except Exception as e:
        logger.debug(f"[WindowsLauncher] Registry application lookup failed: {e}")
    return False


def _try_common_paths(app_name: str) -> bool:
    """Try common application installation paths."""
    username = os.getenv("USERNAME", "")
    common_paths = [
        f"C:\\Program Files\\{app_name}\\{app_name}.exe",
        f"C:\\Program Files (x86)\\{app_name}\\{app_name}.exe",
        f"C:\\Users\\{username}\\AppData\\Local\\Programs\\{app_name}\\{app_name}.exe",
        f"C:\\Users\\{username}\\AppData\\Local\\{app_name}\\{app_name}.exe",
        f"C:\\Users\\{username}\\AppData\\Roaming\\{app_name}\\{app_name}.exe",
    ]

    for path in common_paths:
        if os.path.exists(path):
            try:
                subprocess.Popen(
                    [path],
                    creationflags=_DETACHED_PROCESS,
                )
                return True
            except Exception:
                continue
    return False


def _try_where_command(app_name: str) -> bool:
    """Try finding and launching the application using the where command."""
    try:
        result = subprocess.run(
            ["where", app_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            exe_path = result.stdout.strip().split("\n")[0]
            if exe_path and os.path.exists(exe_path):
                subprocess.Popen(
                    [exe_path],
                    creationflags=_DETACHED_PROCESS,
                )
                return True
    except Exception as e:
        logger.debug(f"[WindowsLauncher] where.exe application lookup failed: {e}")
    return False


def _try_uwp_launch(app_name: str) -> bool:
    """Try launching a UWP application."""
    try:
        return _launch_uwp_app(app_name)
    except Exception:
        return False


def _find_executable_in_registry(app_name: str) -> str | None:
    """Find the executable file path of an application via the Windows Registry.

    Args:
        app_name: Application name

    Returns:
        Application path, or None if not found
    """
    try:
        import winreg

        registry_paths = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]

        for registry_path in registry_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, registry_path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                try:
                                    display_name = winreg.QueryValueEx(
                                        subkey, "DisplayName"
                                    )[0]
                                    if app_name.lower() in display_name.lower():
                                        try:
                                            install_location = winreg.QueryValueEx(
                                                subkey, "InstallLocation"
                                            )[0]
                                            if install_location and os.path.exists(
                                                install_location
                                            ):
                                                for root, _dirs, files in os.walk(
                                                    install_location
                                                ):
                                                    for file in files:
                                                        if (
                                                            file.lower().endswith(
                                                                ".exe"
                                                            )
                                                            and app_name.lower()
                                                            in file.lower()
                                                        ):
                                                            return os.path.join(
                                                                root, file
                                                            )
                                        except FileNotFoundError:
                                            pass

                                        try:
                                            display_icon = winreg.QueryValueEx(
                                                subkey, "DisplayIcon"
                                            )[0]
                                            if (
                                                display_icon
                                                and display_icon.endswith(".exe")
                                                and os.path.exists(display_icon)
                                            ):
                                                return display_icon
                                        except FileNotFoundError:
                                            pass

                                except FileNotFoundError:
                                    continue
                        except Exception:
                            continue
            except Exception:
                continue

        return None

    except ImportError:
        logger.debug("[WindowsLauncher] winreg module unavailable, skipping registry lookup")
        return None
    except Exception as e:
        logger.debug(f"[WindowsLauncher] Registry lookup failed: {e}")
        return None


def _launch_uwp_app(app_name: str) -> bool:
    """Try launching a UWP (Windows Store) application.

    Passes the application name via the $env:_APP_QUERY environment variable
    to avoid concatenating user input directly into the PowerShell script.

    Args:
        app_name: Application name

    Returns:
        Whether the launch succeeded
    """
    try:
        # Script reads the query string from environment variable without user input interpolation
        powershell_script = (
            "$q = $env:_APP_QUERY\n"
            "$app = Get-AppxPackage "
            '| Where-Object {$_.Name -like "*$q*" '
            '-or $_.PackageFullName -like "*$q*"} '
            "| Select-Object -First 1\n"
            "if ($app) {\n"
            "    $manifest = Get-AppxPackageManifest $app.PackageFullName\n"
            "    $appId = $manifest.Package.Applications.Application.Id\n"
            "    if ($appId) {\n"
            '        Start-Process "shell:AppsFolder\\$($app.PackageFullName)!$appId"\n'
            '        Write-Output "Success"\n'
            "    }\n"
            "}"
        )

        encoded = base64.b64encode(powershell_script.encode("utf-16-le")).decode(
            "ascii"
        )

        env = os.environ.copy()
        env["_APP_QUERY"] = app_name

        result = subprocess.run(
            ["powershell", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )

        if result.returncode == 0 and "Success" in result.stdout:
            return True

    except Exception as e:
        logger.debug(f"[WindowsLauncher] UWP launch exception: {e}")

    return False
