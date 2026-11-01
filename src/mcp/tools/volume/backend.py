"""Volume backend protocol and factory."""

from __future__ import annotations

from typing import Protocol


class VolumeBackend(Protocol):
    """Platform-specific volume implementation."""

    def get_volume(self) -> int:
        """Current volume level (0-100)."""
        ...

    def set_volume(self, volume: int) -> None:
        """Set volume level 0-100 (clamped by caller)."""
        ...
