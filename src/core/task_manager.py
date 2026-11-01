"""Task manager.

Uniformly manages creation, tracking, and cleanup of asynchronous tasks.
"""

import asyncio
from typing import Any, Awaitable, Callable, Optional

from src.logging import get_logger

logger = get_logger()


class TaskManager:
    """Asynchronous task manager.

    Responsibilities:
    - Create and track asynchronous tasks
    - Cancel all tasks uniformly on shutdown
    - Provide thread-safe task scheduling

    Usage:
        tm = TaskManager()
        tm.set_loop(asyncio.get_running_loop())

        # Create task
        task = tm.spawn(some_coroutine(), "task_name")

        # Thread-safe scheduling
        tm.schedule_nowait(some_function, arg1, arg2)

        # Clean up on shutdown
        await tm.cancel_all()
    """

    def __init__(self):
        self._tasks: set[asyncio.Task] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._running: bool = False

    def initialize(self, loop: asyncio.AbstractEventLoop = None) -> None:
        """Initialize task manager.

        Args:
            loop: Event loop; uses the currently running loop when None
        """
        self._loop = loop or asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()
        self._running = True
        logger.debug("TaskManager initialized")

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """
        Get event loop.
        """
        return self._loop

    @property
    def running(self) -> bool:
        """
        Whether the task manager is running.
        """
        return self._running

    @property
    def shutdown_event(self) -> Optional[asyncio.Event]:
        """
        Get shutdown event.
        """
        return self._shutdown_event

    def spawn(self, coro: Awaitable[Any], name: str) -> Optional[asyncio.Task]:
        """Create and track an asynchronous task.

        Args:
            coro: Coroutine object
            name: Task name

        Returns:
            Created task object, or None if the application is shutting down
        """
        # Check if shutting down
        if not self._running or (
            self._shutdown_event and self._shutdown_event.is_set()
        ):
            logger.debug(f"Skipping task creation (application shutting down): {name}")
            return None

        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)

        def _on_done(t: asyncio.Task):
            self._tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc:
                    # Done callback has no active exception context; must pass exc instance directly
                    logger.error(f"Task {name} exited with exception: {exc}", exc_info=exc)

        task.add_done_callback(_on_done)
        return task

    def schedule_nowait(self, fn: Callable, *args, **kwargs) -> None:
        """Schedule a callable in a thread-safe manner.

        Automatically creates a task if the callable returns a coroutine.

        Args:
            fn: Callable object
            *args: Positional arguments
            **kwargs: Keyword arguments
        """
        # Check if shutting down - silently reject
        if not self._running or (
            self._shutdown_event and self._shutdown_event.is_set()
        ):
            return

        if not self._loop or self._loop.is_closed():
            # Silently skip when closed without logging a warning
            return

        def _runner():
            try:
                result = fn(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    task = self.spawn(
                        result, name=f"scheduled:{getattr(fn, '__name__', 'anon')}"
                    )
                    if task is None:
                        result.close()
            except Exception as e:
                logger.error(f"Scheduled callable execution failed: {e}", exc_info=True)

        self._loop.call_soon_threadsafe(_runner)

    async def wait_shutdown(self) -> None:
        """
        Wait for shutdown signal.
        """
        if self._shutdown_event:
            await self._shutdown_event.wait()

    def request_shutdown(self) -> None:
        """
        Request shutdown.
        """
        if self._shutdown_event and not self._shutdown_event.is_set():
            self._shutdown_event.set()
            logger.info("Shutdown request received")

    async def cancel_all(self) -> None:
        """Cancel all tracked tasks.

        Waits for all tasks to complete or be cancelled.
        """
        self._running = False

        if self._shutdown_event:
            self._shutdown_event.set()

        if not self._tasks:
            return

        logger.info(f"Cancelling {len(self._tasks)} tasks...")

        # Cancel all tasks
        for task in list(self._tasks):
            if not task.done():
                task.cancel()

        # Wait for all tasks to finish
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        logger.info("All tasks cancelled")

    def task_count(self) -> int:
        """
        Get current number of active tasks.
        """
        return len(self._tasks)

    def get_task_names(self) -> list[str]:
        """
        Get names of all active tasks.
        """
        return [t.get_name() for t in self._tasks if not t.done()]
