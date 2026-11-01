"""Unified UI interface: implemented by gui / cli / gpio."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ViewPort(Protocol):
    """Set of methods for updating the UI."""

    async def start(self, mode: str = "cli") -> None:
        """Start."""
        ...

    async def close(self) -> None:
        """Close."""
        ...

    def set_status(self, status: str, connected: bool = True) -> None:
        """Status bar."""
        ...

    def set_emotion(self, emotion: str) -> None:
        """Emotion name; specific assets are resolved by each frontend."""
        ...

    def set_chat_text(self, text: str) -> None:
        """Chat content (TTS / STT)."""
        ...

    def set_music_line(self, text: str) -> None:
        """Music status or lyrics."""
        ...

    def set_button_text(self, text: str) -> None:
        """Primary button label; can be a no-op if there is no button."""
        ...

    def set_auto_mode(self, auto_mode: bool) -> None:
        """Refresh auto/manual display (source of truth is in Session)."""
        ...

    def is_auto_mode(self) -> bool:
        """Current mode recorded on the UI (used e.g. by GPIO button branches)."""
        ...
