"""Startup health checks and audio degradation policy.

Independent of session logic: determines whether to exit or continue in degraded mode based solely on plugin failures and environment variables.
"""

from __future__ import annotations

import os
from typing import Optional, Protocol

# Exit immediately on startup failure to avoid zombie wait_shutdown
CRITICAL_PLUGINS = ("ui",)
# Audio is critical by default; when XIAOZHI_DEGRADED_AUDIO=1, failure degrades gracefully instead of exiting
AUDIO_CRITICAL = True

DEGRADED_AUDIO_NOTICE = (
    "Degraded mode: audio unavailable (no microphone/speaker). Settings remain available; please restart after fixing audio devices."
)


class _PluginHealth(Protocol):
    def is_failed(self, name: str) -> bool: ...


def audio_is_fatal() -> bool:
    """Whether an audio plugin failure should cause the process to exit.

    - XIAOZHI_DISABLE_AUDIO=1: intentionally disabled, not treated as a failure
    - XIAOZHI_DEGRADED_AUDIO=1: degrade and continue on failure (UI/settings remain available)
    - Default: exit 1 on audio failure
    """
    if os.getenv("XIAOZHI_DISABLE_AUDIO") == "1":
        return False
    if os.getenv("XIAOZHI_DEGRADED_AUDIO") == "1":
        return False
    return AUDIO_CRITICAL


def check_critical_plugins(plugins: _PluginHealth) -> Optional[str]:
    """Check whether critical plugins are available.

    Returns:
        Error description; returns None when all critical plugins are healthy
    """
    failed: list[str] = []
    for name in CRITICAL_PLUGINS:
        if plugins.is_failed(name):
            failed.append(name)

    if audio_is_fatal() and plugins.is_failed("audio"):
        failed.append("audio")

    if not failed:
        return None

    hints = []
    if "audio" in failed:
        hints.append(
            "Set XIAOZHI_DISABLE_AUDIO=1 for no-audio debugging; "
            "or set XIAOZHI_DEGRADED_AUDIO=1 to continue without a microphone (UI/settings available)."
        )
    return (
        f"Critical plugin startup failed: {', '.join(failed)}. "
        "Application will exit to avoid running as a zombie."
        + (" " + " ".join(hints) if hints else "")
    )
