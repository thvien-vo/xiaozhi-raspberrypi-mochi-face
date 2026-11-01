"""Log handler module.

Provides:
- Dual rotating file handler (time + size)
- Asynchronous log handler
"""

import atexit
import gzip
import logging
import queue
import shutil
import time
from logging.handlers import BaseRotatingHandler, QueueHandler, QueueListener
from pathlib import Path
from typing import Union


class TimeSizeRotatingFileHandler(BaseRotatingHandler):
    """Dual rotating file handler.

    Supports rotation by both time and file size, rotating as soon as either condition is met.
    """

    def __init__(
        self,
        filename: Union[str, Path],
        when: str = "midnight",
        interval: int = 1,
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 30,
        encoding: str = "utf-8",
        delay: bool = False,
        compress: bool = False,
    ) -> None:
        self.baseFilename = str(filename)
        self.when = when.upper()
        self.interval = interval
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.compress = compress

        # Compute time-based rollover
        self._compute_rollover_time()

        # Initialize parent class
        super().__init__(self.baseFilename, "a", encoding=encoding, delay=delay)

    def _compute_rollover_time(self) -> None:
        """
        Compute the next rollover timestamp.
        """
        current_time = int(time.time())

        if self.when == "MIDNIGHT":
            # Compute seconds until midnight
            t = time.localtime(current_time)
            current_hour = t.tm_hour
            current_minute = t.tm_min
            current_second = t.tm_sec
            seconds_to_midnight = (
                (24 - current_hour - 1) * 3600
                + (60 - current_minute - 1) * 60
                + (60 - current_second)
            )
            self.rollover_at = current_time + seconds_to_midnight
            self.suffix = "%Y-%m-%d"
        elif self.when == "H":
            self.rollover_at = current_time + 3600 * self.interval
            self.suffix = "%Y-%m-%d_%H"
        elif self.when == "D":
            self.rollover_at = current_time + 86400 * self.interval
            self.suffix = "%Y-%m-%d"
        else:
            self.rollover_at = current_time + 86400
            self.suffix = "%Y-%m-%d"

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        """
        Check whether log rollover is needed.
        """
        # Check time condition
        if time.time() >= self.rollover_at:
            return True

        # Check size condition
        if self.max_bytes > 0:
            if self.stream is None:
                self.stream = self._open()
            try:
                self.stream.seek(0, 2)  # Seek to end of file
                if self.stream.tell() + len(self.format(record)) >= self.max_bytes:
                    return True
            except (OSError, ValueError):
                pass

        return False

    def doRollover(self) -> None:
        """
        Perform log rollover.
        """
        if self.stream:
            self.stream.close()
            self.stream = None

        # Generate rotated filename
        current_time = time.time()
        time_suffix = time.strftime(self.suffix, time.localtime(current_time))

        # Handle size-triggered rollover (which may happen multiple times on the same day)
        base_path = Path(self.baseFilename)
        base_name = base_path.stem
        base_ext = base_path.suffix
        parent = base_path.parent

        # Build rotated filename
        rotated_name = f"{base_name}.{time_suffix}"

        # Append sequence number if a file with the same name already exists
        counter = 0
        while True:
            if counter == 0:
                dfn = parent / f"{rotated_name}{base_ext}"
            else:
                dfn = parent / f"{rotated_name}.{counter}{base_ext}"

            if self.compress:
                dfn = Path(str(dfn) + ".gz")

            if not dfn.exists():
                break
            counter += 1

        # Execute rollover
        source_path = Path(self.baseFilename)
        if source_path.exists():
            if self.compress:
                self._compress_file(source_path, dfn)
                source_path.unlink()
            else:
                shutil.move(str(source_path), str(dfn))

        # Clean up old files
        self._cleanup_old_files(parent, base_name)

        # Recompute next rollover time
        self._compute_rollover_time()

        # Reopen log file
        if not self.delay:
            self.stream = self._open()

    def _compress_file(self, source: Path, dest: Path) -> None:
        """
        Compress log file.
        """
        with open(source, "rb") as f_in:
            with gzip.open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

    def _cleanup_old_files(self, directory: Path, base_name: str) -> None:
        """
        Clean up old rotated files exceeding backup_count.
        """
        if self.backup_count <= 0:
            return

        # Find all rotated files
        pattern = f"{base_name}.*"
        log_files = []

        for f in directory.glob(pattern):
            if f.name != Path(self.baseFilename).name:
                log_files.append(f)

        # Sort by modification time (newest first)
        log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # Delete excess files
        for old_file in log_files[self.backup_count :]:
            try:
                old_file.unlink()
            except OSError:
                pass


class AsyncHandler(QueueHandler):
    """Asynchronous log handler.

    Uses a queue to offload log writing to a background thread, avoiding blocking the main thread.
    """

    def __init__(
        self,
        handlers: list[logging.Handler],
        queue_size: int = 10000,
        respect_handler_level: bool = True,
    ) -> None:
        # Create queue
        self._log_queue: queue.Queue = queue.Queue(maxsize=queue_size)
        super().__init__(self._log_queue)

        # Create listener
        self._listener = QueueListener(
            self._log_queue,
            *handlers,
            respect_handler_level=respect_handler_level,
        )
        self._listener.start()

        # Register cleanup on exit
        atexit.register(self.close)

    def close(self) -> None:
        """
        Close handler.
        """
        try:
            self._listener.stop()
        except Exception as e:
            logging.getLogger(__name__).debug(f"Failed to stop log listener: {e}")
        super().close()

    def emit(self, record: logging.LogRecord) -> None:
        """
        Enqueue log record.
        """
        try:
            self.enqueue(record)
        except queue.Full:
            logging.getLogger(__name__).debug("Log queue is full, dropping one log record")
