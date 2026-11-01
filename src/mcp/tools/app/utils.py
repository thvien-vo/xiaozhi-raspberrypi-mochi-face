"""Common utilities for application management.

Provides unified application matching, lookup, caching, and name cleaning functionality.
"""

import platform
import re
import time
from typing import Any

from src.logging import get_logger

logger = get_logger()

# Global application cache
_cached_applications: list[dict[str, Any]] | None = None
_cache_timestamp: float = 0
_cache_duration = 300  # Cache for 5 minutes


def clean_app_name(name: str) -> str:
    """Clean an application name by removing version numbers and special characters.

    Args:
        name: Original application name

    Returns:
        str: Cleaned application name
    """
    if not name:
        return ""

    # Remove common version number patterns
    name = re.sub(r"\s+v?\d+[\.\d]*", "", name)
    name = re.sub(r"\s*\(\d+\)", "", name)
    name = re.sub(r"\s*\[.*?\]", "", name)

    # Collapse extra spaces
    name = " ".join(name.split())

    return name.strip()


class AppMatcher:
    """
    Unified application matcher.
    """

    # Special application name mappings - ordered by length to avoid short names matching prematurely
    SPECIAL_MAPPINGS = {
        "qqmusic": ["qqmusic", "qq music"],
        "qq music": ["qqmusic", "qq music"],
        "tencent meeting": ["tencent meeting", "voovmeeting"],
        "voovmeeting": ["tencent meeting", "voovmeeting"],
        "google chrome": ["chrome", "googlechrome", "google chrome"],
        "microsoft edge": ["msedge", "edge", "microsoft edge"],
        "microsoft office": [
            "microsoft office",
            "office",
            "word",
            "excel",
            "powerpoint",
        ],
        "microsoft word": ["microsoft word", "word"],
        "microsoft excel": ["microsoft excel", "excel"],
        "microsoft powerpoint": ["microsoft powerpoint", "powerpoint"],
        "visual studio code": ["code", "vscode", "visual studio code"],
        "wps office": ["wps", "wps office"],
        "qq": ["qq", "qqnt", "tencentqq"],
        "wechat": ["wechat", "weixin"],
        "dingtalk": ["dingtalk", "ding"],
        "chrome": ["chrome", "googlechrome", "google chrome"],
        "firefox": ["firefox", "mozilla"],
        "edge": ["msedge", "edge", "microsoft edge"],
        "safari": ["safari"],
        "notepad": ["notepad", "notepad++"],
        "calculator": ["calc", "calculator", "calculatorapp"],
        "calc": ["calc", "calculator", "calculatorapp"],
        "feishu": ["feishu", "lark"],
        "vscode": ["code", "vscode", "visual studio code"],
        "pycharm": ["pycharm", "pycharm64"],
        "cursor": ["cursor"],
        "typora": ["typora"],
        "wps": ["wps", "wps office"],
        "office": ["microsoft office", "office", "word", "excel", "powerpoint"],
        "word": ["microsoft word", "word"],
        "excel": ["microsoft excel", "excel"],
        "powerpoint": ["microsoft powerpoint", "powerpoint"],
        "finder": ["finder"],
        "terminal": ["terminal", "iterm"],
        "iterm": ["iterm", "iterm2"],
    }

    # Process group mappings (used for grouping during termination)
    PROCESS_GROUPS = {
        "chrome": "chrome",
        "googlechrome": "chrome",
        "firefox": "firefox",
        "edge": "edge",
        "msedge": "edge",
        "safari": "safari",
        "qq": "qq",
        "qqnt": "qq",
        "tencentqq": "qq",
        "qqmusic": "qqmusic",
        "QQMUSIC": "QQMUSIC",
        "qq music": "qqmusic",
        "wechat": "wechat",
        "weixin": "wechat",
        "dingtalk": "dingtalk",
        "feishu": "feishu",
        "lark": "feishu",
        "vscode": "vscode",
        "code": "vscode",
        "cursor": "cursor",
        "pycharm": "pycharm",
        "pycharm64": "pycharm",
        "typora": "typora",
        "calculatorapp": "calculator",
        "calc": "calculator",
        "calculator": "calculator",
        "tencent meeting": "tencent_meeting",
        "voovmeeting": "tencent_meeting",
        "wps": "wps",
        "word": "word",
        "excel": "excel",
        "powerpoint": "powerpoint",
        "finder": "finder",
        "terminal": "terminal",
        "iterm": "iterm",
        "iterm2": "iterm",
    }

    @classmethod
    def normalize_name(cls, name: str) -> str:
        """
        Normalize an application name.
        """
        if not name:
            return ""

        # Strip .exe suffix
        name = name.lower().replace(".exe", "")

        # Remove version numbers and special characters
        name = re.sub(r"\s+v?\d+[\.\d]*", "", name)
        name = re.sub(r"\s*\(\d+\)", "", name)
        name = re.sub(r"\s*\[.*?\]", "", name)
        name = " ".join(name.split())

        return name.strip()

    @classmethod
    def get_process_group(cls, process_name: str) -> str:
        """
        Get the group to which a process belongs.
        """
        normalized = cls.normalize_name(process_name)

        # Check direct mapping
        if normalized in cls.PROCESS_GROUPS:
            return cls.PROCESS_GROUPS[normalized]

        # Check substring containment
        for key, group in cls.PROCESS_GROUPS.items():
            if key in normalized or normalized in key:
                return group

        return normalized

    @classmethod
    def match_application(cls, target_name: str, app_info: dict[str, Any]) -> int:
        """Match an application and return a similarity score.

        Args:
            target_name: Target application name
            app_info: Application information dictionary

        Returns:
            int: Match score (0-100), where 0 means no match
        """
        if not target_name or not app_info:
            return 0

        target_lower = target_name.lower()
        app_name = app_info.get("name", "").lower()
        display_name = app_info.get("display_name", "").lower()
        window_title = app_info.get("window_title", "").lower()
        exe_path = app_info.get("command", "").lower()

        # 1. Exact match (100 points)
        if target_lower == app_name or target_lower == display_name:
            return 100

        # 2. Special mapping match (95-98 points) - prioritize more specific keywords
        best_special_score = 0

        for key in cls.SPECIAL_MAPPINGS:
            if key in target_lower or target_lower == key:
                # Check if any alias matches
                for alias in cls.SPECIAL_MAPPINGS[key]:
                    if alias.lower() in app_name or alias.lower() in display_name:
                        # Calculate match score: more specific matches score higher
                        if target_lower == key:
                            score = 98  # Exact match on special mapping key
                        elif len(key) > len(target_lower) * 0.8:
                            score = 97  # Similar-length match
                        else:
                            score = 95  # General special mapping match

                        if score > best_special_score:
                            best_special_score = score

        if best_special_score > 0:
            return best_special_score

        # 3. Normalized name match (90 points)
        normalized_target = cls.normalize_name(target_name)
        normalized_app = cls.normalize_name(app_info.get("name", ""))
        normalized_display = cls.normalize_name(app_info.get("display_name", ""))

        if (
            normalized_target == normalized_app
            or normalized_target == normalized_display
        ):
            return 90

        # 4. Substring containment match (70-80 points)
        if target_lower in app_name:
            return 80
        if target_lower in display_name:
            return 75
        if app_name and app_name in target_lower:
            # Avoid short names falsely matching long names with high scores
            if len(app_name) < len(target_lower) * 0.5:
                return 50  # Lower score
            return 70

        # 5. Window title match (60 points)
        if window_title and target_lower in window_title:
            return 60

        # 6. Path match (50 points)
        if exe_path and target_lower in exe_path:
            return 50

        # 7. Fuzzy match (30 points)
        if cls._fuzzy_match(target_lower, app_name) or cls._fuzzy_match(
            target_lower, display_name
        ):
            return 30

        return 0

    @classmethod
    def _fuzzy_match(cls, target: str, candidate: str) -> bool:
        """
        Fuzzy matching.
        """
        if not target or not candidate:
            return False

        # Strip all non-alphanumeric characters for comparison
        target_clean = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]", "", target)
        candidate_clean = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]", "", candidate)

        return target_clean in candidate_clean or candidate_clean in target_clean


async def get_cached_applications(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Get the cached list of applications.

    Args:
        force_refresh: Whether to force refreshing the cache

    Returns:
        List of applications
    """
    global _cached_applications, _cache_timestamp

    current_time = time.time()

    # Check if cache is valid
    if (
        not force_refresh
        and _cached_applications is not None
        and (current_time - _cache_timestamp) < _cache_duration
    ):
        logger.debug(
            f"[AppUtils] Using cached application list, cached {int(current_time - _cache_timestamp)}s ago"
        )
        return _cached_applications

    # Rescan applications
    try:
        import json

        from .scanner import scan_installed_applications

        logger.info("[AppUtils] Refreshing application cache")
        result_json = await scan_installed_applications(
            {"force_refresh": force_refresh}
        )
        result = json.loads(result_json)

        if result.get("success", False):
            _cached_applications = result.get("applications", [])
            _cache_timestamp = current_time
            logger.info(
                f"[AppUtils] Application cache refreshed, found {len(_cached_applications)} applications"
            )
            return _cached_applications
        else:
            logger.warning(
                f"[AppUtils] Application scan failed: {result.get('message', 'Unknown error')}"
            )
            return _cached_applications or []

    except Exception as e:
        logger.error(f"[AppUtils] Failed to refresh application cache: {e}", exc_info=True)
        return _cached_applications or []


async def find_best_matching_app(
    app_name: str, app_type: str = "any"
) -> dict[str, Any] | None:
    """Find the best matching application.

    Args:
        app_name: Application name
        app_type: Application type filter ("installed", "running", "any")

    Returns:
        Best matching application info dict
    """
    try:
        if app_type == "running":
            # Get currently running applications
            import asyncio

            from .process_manager import list_running_applications as list_running_sync

            applications = await asyncio.to_thread(list_running_sync)
        else:
            # Get installed applications
            applications = await get_cached_applications()

        if not applications:
            return None

        # Calculate match scores across all applications
        matches = []
        for app in applications:
            score = AppMatcher.match_application(app_name, app)
            if score > 0:
                matches.append((score, app))

        if not matches:
            return None

        # Sort by score and return best match
        matches.sort(key=lambda x: x[0], reverse=True)
        best_score, best_app = matches[0]

        logger.info(
            f"[AppUtils] Found best match: {best_app.get('display_name', best_app.get('name', ''))} (score: {best_score})"
        )
        return best_app

    except Exception as e:
        logger.error(f"[AppUtils] Failed to find matching application: {e}", exc_info=True)
        return None


def clear_app_cache():
    """Clear the application cache."""
    global _cached_applications, _cache_timestamp

    _cached_applications = None
    _cache_timestamp = 0
    logger.info("[AppUtils] Application cache cleared")


def get_system_scanner():
    """Get the scanner module corresponding to the current operating system."""
    system = platform.system()

    if system == "Darwin":
        from . import scanner_mac

        return scanner_mac
    elif system == "Windows":
        from . import scanner_windows

        return scanner_windows
    elif system == "Linux":
        from . import scanner_linux

        return scanner_linux
    else:
        logger.warning(f"[AppUtils] Unsupported system: {system}")
        return None


def get_cache_info() -> dict[str, Any]:
    """
    Get cache metadata information.
    """

    current_time = time.time()
    cache_age = current_time - _cache_timestamp if _cache_timestamp > 0 else -1

    return {
        "cached": _cached_applications is not None,
        "count": len(_cached_applications) if _cached_applications else 0,
        "age_seconds": int(cache_age) if cache_age >= 0 else None,
        "valid": cache_age >= 0 and cache_age < _cache_duration,
        "cache_duration": _cache_duration,
    }
