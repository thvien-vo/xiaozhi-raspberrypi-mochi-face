"""Resource pool.

Unified resource registration and cleanup mechanism. All resources requiring cleanup
(C extensions, audio streams, network connections, etc.) are registered into the pool
and released in reverse registration order during shutdown to avoid duplicate or missed cleanup.
"""

import asyncio
from typing import Awaitable, Callable, Union

from src.logging import get_logger

logger = get_logger()

CleanupFunc = Callable[[], Union[None, Awaitable[None]]]


class ResourcePool:
    """Resource pool - registers cleanup functions and releases them in reverse order.

    Usage:
        pool = ResourcePool()
        pool.register("opus_codec", opus_codec.close)
        pool.register("audio_stream", stream_manager.stop)
        await pool.shutdown()  # Executes all cleanup functions in reverse order
    """

    def __init__(self):
        self._resources: list[tuple[str, CleanupFunc]] = []
        self._shutting_down = False

    def register(self, name: str, cleanup: CleanupFunc) -> None:
        """Register a cleanup function.

        Args:
            name: Resource name (used for logging and troubleshooting)
            cleanup: Cleanup function, can be a regular function or async function
        """
        if self._shutting_down:
            logger.warning(f"Resource pool is shutting down, skipping registration: {name}")
            return
        self._resources.append((name, cleanup))

    async def shutdown(self) -> None:
        """Release all registered resources in reverse registration order."""
        if self._shutting_down:
            return
        self._shutting_down = True

        for name, cleanup in reversed(self._resources):
            try:
                result = cleanup()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Failed to release resource [{name}]: {e}", exc_info=True)

        self._resources.clear()
        logger.debug("Resource pool cleared")
