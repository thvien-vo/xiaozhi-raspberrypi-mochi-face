"""Cross-platform process management (based on psutil).

Replaces platform-specific subprocess process listing and termination logic with psutil
to eliminate command injection risks.
"""

import sys

import psutil

from src.logging import get_logger

from .utils import AppMatcher

logger = get_logger()

# Electron/Chromium child process suffixes, representing internal helper processes of the main app
_HELPER_SUFFIXES = (
    " helper",
    " helper (gpu)",
    " helper (renderer)",
    " helper (plugin)",
    "_crashpad_handler",
)

# macOS system path prefixes; processes under these paths are system daemons
_MACOS_SYSTEM_PREFIXES = (
    "/usr/libexec/",
    "/usr/sbin/",
    "/System/Library/",
    "/Library/Apple/",
)

# Windows system process names (lowercase, without .exe)
_WINDOWS_SYSTEM_NAMES: set[str] = {
    "dwm",
    "winlogon",
    "csrss",
    "smss",
    "wininit",
    "services",
    "lsass",
    "svchost",
    "spoolsv",
    "taskhostw",
    "fontdrvhost",
    "dllhost",
    "ctfmon",
    "audiodg",
    "conhost",
    "sihost",
    "shellexperiencehost",
    "startmenuexperiencehost",
    "runtimebroker",
    "applicationframehost",
    "searchui",
    "lockapp",
    "explorer",
}


def _is_user_application(name: str, exe: str) -> bool:
    """Determine whether a process is a user-visible application."""
    name_lower = name.lower()
    exe_lower = exe.lower()

    # Exclude Electron/Chromium child processes (Helper, Renderer, GPU)
    if any(name_lower.endswith(suffix) for suffix in _HELPER_SUFFIXES):
        return False

    if sys.platform == "darwin":
        # macOS: only keep main .app processes under /Applications/
        if "/Applications/" in exe and ".app/" in exe:
            # Exclude nested sub-.app bundles inside .app (e.g. Framework/Helpers/)
            app_path = exe[: exe.index(".app/") + 5]
            remaining = exe[len(app_path) :]
            if ".app/" in remaining:
                return False
            return True
        # Exclude system path prefixes
        if any(exe_lower.startswith(p) for p in _MACOS_SYSTEM_PREFIXES):
            return False
        # Exclude background utilities under /Library/Application Support/ (e.g. security software, VPN)
        if "/Library/Application Support/" in exe and ".app" not in exe:
            return False
        # Exclude other known system process paths
        if exe_lower.startswith("/system/") or exe_lower.startswith("/library/"):
            return False
        return False

    elif sys.platform == "win32":
        if name_lower.replace(".exe", "") in _WINDOWS_SYSTEM_NAMES:
            return False
        # Exclude Windows system directory processes
        if "\\windows\\system32\\" in exe_lower:
            return False
        if "\\windows\\syswow64\\" in exe_lower:
            return False
        return True

    else:
        # Linux: exclude system daemons
        if exe_lower.startswith(("/usr/libexec/", "/usr/sbin/")):
            return False
        if exe_lower.startswith("/usr/bin/") and name_lower in {
            "dbus-daemon",
            "dbus-broker",
            "at-spi-bus-launcher",
            "pulseaudio",
            "pipewire",
            "systemd",
        }:
            return False
        return True


def list_running_applications(filter_name: str = "") -> list[dict]:
    """List running user applications.

    Returns only user-visible desktop applications, excluding system daemons, Electron helper processes, etc.

    Args:
        filter_name: Optional name filter keyword; relaxes filtering when provided (used for kill matching)
    """
    apps: list[dict] = []
    filter_lower = filter_name.lower() if filter_name else ""

    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline", "status"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            exe = info.get("exe") or ""
            pid = info.get("pid", 0)
            if not name or pid <= 4:
                continue

            # Relax filtering when a filter keyword is provided (for kill matching scenarios that need child processes)
            if filter_lower:
                name_lower = name.lower()
                exe_lower = exe.lower()
                cmd = " ".join(info.get("cmdline") or [])
                cmd_lower = cmd.lower()
                if (
                    filter_lower not in name_lower
                    and filter_lower not in exe_lower
                    and filter_lower not in cmd_lower
                ):
                    continue
                apps.append(
                    {
                        "pid": pid,
                        "name": name,
                        "display_name": name,
                        "exe": exe,
                        "command": cmd,
                        "type": "application",
                    }
                )
            else:
                # Without filter keyword: strictly return user applications only
                if not _is_user_application(name, exe):
                    continue
                apps.append(
                    {
                        "pid": pid,
                        "name": name,
                        "display_name": name,
                        "exe": exe,
                        "command": " ".join(info.get("cmdline") or []),
                        "type": "application",
                    }
                )

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # Sort by name and deduplicate by PID
    seen_pids: set[int] = set()
    unique_apps: list[dict] = []
    for app in sorted(apps, key=lambda x: x["name"].lower()):
        if app["pid"] not in seen_pids:
            seen_pids.add(app["pid"])
            unique_apps.append(app)

    return unique_apps


def kill_process(pid: int, force: bool = False) -> bool:
    """Terminate the process with the specified PID.

    Args:
        pid: Process ID
        force: True to use SIGKILL/TerminateProcess, False to use SIGTERM

    Returns:
        Whether termination succeeded
    """
    try:
        proc = psutil.Process(pid)
        if force:
            proc.kill()
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                logger.warning(f"[ProcessManager] Process {pid} did not exit within timeout, force killing")
                proc.kill()
        return True
    except psutil.NoSuchProcess:
        logger.warning(f"[ProcessManager] Process does not exist: {pid}")
        return False
    except psutil.AccessDenied as e:
        logger.warning(f"[ProcessManager] Access denied when terminating process {pid}: {e}", exc_info=True)
        return False


def find_matching_processes(app_name: str) -> list[dict]:
    """Find running processes matching the application name.

    Uses AppMatcher for intelligent matching (including special mappings, fuzzy matching, etc.).

    Args:
        app_name: Target application name

    Returns:
        List of matching process info dicts, sorted by match score descending
    """
    all_apps = list_running_applications()
    matched: list[tuple[int, dict]] = []

    for app in all_apps:
        score = AppMatcher.match_application(app_name, app)
        if score >= 50:
            matched.append((score, app))

    matched.sort(key=lambda x: x[0], reverse=True)
    return [app for _, app in matched]


def kill_application_by_name(app_name: str, force: bool = False) -> bool:
    """Terminate an application by name (matching all related processes).

    Args:
        app_name: Application name
        force: Whether to force kill

    Returns:
        Whether at least one process was terminated
    """
    matched = find_matching_processes(app_name)
    if not matched:
        logger.info(f"[ProcessManager] No matching running process found: {app_name}")
        return False

    logger.info(f"[ProcessManager] Found {len(matched)} matching processes: {app_name}")

    # Terminate child processes first before terminating parent process
    success_count = 0
    for app in matched:
        pid = app["pid"]
        try:
            proc = psutil.Process(pid)
            # Terminate child processes first
            children = proc.children(recursive=True)
            for child in children:
                try:
                    if force:
                        child.kill()
                    else:
                        child.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Terminate main process
            if kill_process(pid, force):
                success_count += 1
                logger.info(f"[ProcessManager] Terminated process: {app['name']} (PID={pid})")
        except psutil.NoSuchProcess:
            logger.debug(f"[ProcessManager] Process already exited: PID={pid}")
        except psutil.AccessDenied as e:
            logger.warning(f"[ProcessManager] Access denied for process PID={pid}: {e}", exc_info=True)

    logger.info(
        f"[ProcessManager] Termination complete, succeeded on {success_count}/{len(matched)} processes"
    )
    return success_count > 0
