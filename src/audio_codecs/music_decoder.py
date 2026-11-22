import asyncio
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from src.constants.constants import AudioConfig
from src.logging import get_logger
from src.utils.resource_finder import get_ffmpeg_path, get_ffprobe_path

logger = get_logger()

_SUBPROCESS_KW = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)

# Local path or http(s) stream
AudioSource = str | Path


def is_http_url(source: AudioSource) -> bool:
    text = str(source).strip()
    return text.startswith("http://") or text.startswith("https://")


def _source_label(source: AudioSource) -> str:
    if is_http_url(source):
        host = urlparse(str(source)).hostname or "http"
        return f"stream:{host}"
    path = Path(source)
    return path.name


def _ffmpeg_header_args(headers: Mapping[str, str] | None) -> list[str]:
    """Build -headers / -user_agent arguments for ffmpeg (commonly used for HTTP sources)."""
    if not headers:
        return []
    args: list[str] = []
    ua = None
    lines: list[str] = []
    for key, value in headers.items():
        if not value:
            continue
        if key.lower() == "user-agent":
            ua = str(value)
            continue
        lines.append(f"{key}: {value}")
    if lines:
        blob = "".join(f"{line}\r\n" for line in lines)
        args.extend(["-headers", blob])
    if ua:
        args.extend(["-user_agent", ua])
    return args


class MusicDecoder:
    """Responsible only for decoding audio sources into PCM; cache prefetching is handled elsewhere (to avoid coupling with playback progress)."""

    @staticmethod
    async def get_duration(
        source: AudioSource,
        headers: Mapping[str, str] | None = None,
    ) -> float:
        """Get duration via ffprobe; source can be a local file or http(s) URL."""
        try:
            ffprobe = get_ffprobe_path()
            try:
                check = await asyncio.create_subprocess_exec(
                    ffprobe,
                    "-version",
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_SUBPROCESS_KW,
                )
                await check.wait()
            except FileNotFoundError:
                if not is_http_url(source):
                    duration = MusicDecoder._tagged_duration(Path(source))
                    if duration > 0:
                        return duration
                logger.warning(
                    "ffprobe is unavailable (bundled libs/ffmpeg not found and ffprobe not in system PATH), "
                    f"cannot get audio duration. Attempted path: {ffprobe}"
                )
                return 0
            except OSError as e:
                logger.warning(f"Failed to start ffprobe: {ffprobe}: {e}", exc_info=True)
                return 0

            cmd = [ffprobe, "-v", "error"]
            if is_http_url(source):
                cmd.extend(_ffmpeg_header_args(headers))
            cmd.extend(
                [
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(source),
                ]
            )

            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_SUBPROCESS_KW
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                duration_str = stdout.decode("utf-8").strip()
                duration = float(duration_str)
                logger.debug(
                    f"Parsed audio duration: {duration:.2f}s ({_source_label(source)})"
                )
                return duration
            else:
                error_msg = stderr.decode("utf-8", errors="ignore")
                logger.warning(f"ffprobe failed to get duration: {error_msg}")
                return 0

        except Exception as e:
            logger.warning(f"Failed to parse audio duration: {e}", exc_info=True)
            return 0

    @staticmethod
    def _tagged_duration(path: Path) -> float:
        """Duration from the file's own header (the imageio-ffmpeg wheel has no ffprobe)."""
        try:
            from mutagen import File

            audio = File(path)
            return float(getattr(getattr(audio, "info", None), "length", 0) or 0)
        except Exception as e:
            logger.debug(f"Cannot read duration of {path.name}: {e}")
            return 0.0

    def __init__(self, sample_rate: int = 24000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._process: asyncio.subprocess.Process | None = None
        self._probe_process: asyncio.subprocess.Process | None = None
        self._decode_task: asyncio.Task | None = None
        self._stopped = False
        self.completed_successfully = False

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process | None) -> None:
        """Terminate and reap a potentially running subprocess."""
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
            # wait() only returns once the pipes are drained: an unread stdout buffer
            # would make every stop run into the timeout
            await asyncio.wait_for(
                asyncio.gather(
                    process.wait(), MusicDecoder._discard(getattr(process, "stdout", None))
                ),
                timeout=2.0,
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except Exception as e:
                logger.debug(f"Failed to force kill FFmpeg: {e}")
        except ProcessLookupError:
            pass
        except Exception as e:
            if str(e).strip():
                logger.debug(f"Terminating FFmpeg process: {e}")

    @staticmethod
    async def _discard(stream) -> None:
        try:
            while stream is not None and await stream.read(65536):
                pass
        except Exception:
            pass  # Being read elsewhere, or already closed

    @staticmethod
    async def _spawn_process(*args, **kwargs) -> asyncio.subprocess.Process:
        """Acquire and reap the created subprocess even if the spawning coroutine is cancelled."""
        spawn_task = asyncio.create_task(
            asyncio.create_subprocess_exec(*args, **kwargs)
        )
        try:
            return await asyncio.shield(spawn_task)
        except asyncio.CancelledError:
            try:
                process = await asyncio.shield(spawn_task)
            except asyncio.CancelledError:
                process = await spawn_task
            await MusicDecoder._terminate_process(process)
            raise

    async def start_decode(
        self,
        source: AudioSource,
        output_queue: asyncio.Queue,
        start_position: float = 0.0,
        headers: Mapping[str, str] | None = None,
        cache_path: Path | None = None,
    ) -> bool:
        """Start decoding. source is a local path or http(s) URL.

        cache_path is deprecated and ignored: caching is performed in the background at network speed, decoupled from playback progress.
        """
        _ = cache_path
        http = is_http_url(source)
        if not http:
            path = Path(source)
            if not path.exists():
                logger.error(f"Audio file does not exist: {path}")
                return False
            source = path

        self._stopped = False
        self.completed_successfully = False

        try:
            ffmpeg = get_ffmpeg_path()
            try:
                result = await self._spawn_process(
                    ffmpeg,
                    "-version",
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_SUBPROCESS_KW,
                )
                self._probe_process = result
                if self._stopped:
                    await self._terminate_process(result)
                    self._probe_process = None
                    return False
                await result.wait()
                self._probe_process = None
                if self._stopped:
                    return False
                if result.returncode not in (0, None):
                    logger.error(
                        f"FFmpeg failed to start (exit code {result.returncode}): {ffmpeg}. "
                        "Installer packages should include a portable binary; if it still fails, please report your version and platform. "
                        "When running from source, execute ./scripts/bundle_ffmpeg.sh or install system ffmpeg"
                    )
                    return False
            except FileNotFoundError:
                logger.error(
                    "FFmpeg is unavailable: bundled libs/ffmpeg/<plat>/<arch>/ffmpeg not found, "
                    f"and not in system PATH. Attempted path: {ffmpeg}. "
                    "Installer users do not need to install system FFmpeg separately; if using an installer and it still fails, "
                    "please re-download the full installer. When running from source, install ffmpeg or execute "
                    "./scripts/bundle_ffmpeg.sh"
                )
                return False
            except OSError as e:
                logger.error(
                    f"Failed to start FFmpeg (possibly missing dynamic libraries): {ffmpeg}: {e}",
                    exc_info=True,
                )
                return False

            cmd = [ffmpeg]

            if http:
                cmd.extend(
                    [
                        "-reconnect",
                        "1",
                        "-reconnect_streamed",
                        "1",
                        "-reconnect_delay_max",
                        "5",
                    ]
                )
                cmd.extend(_ffmpeg_header_args(headers))

            else:
                # A local file must not make ffmpeg open URLs (playlists, references)
                cmd.extend(["-protocol_whitelist", "file,pipe"])

            if start_position > 0.1:
                cmd.extend(["-ss", f"{start_position:.3f}"])

            cmd.extend(
                [
                    "-i",
                    str(source),
                    "-vn",  # Embedded cover art
                    "-f",
                    "s16le",
                    "-ar",
                    str(self.sample_rate),
                    "-ac",
                    str(self.channels),
                    "-loglevel",
                    "error",
                    "-",
                ]
            )

            self._process = await self._spawn_process(
                *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_SUBPROCESS_KW
            )
            if self._stopped:
                await self.stop()
                return False

            self._decode_task = asyncio.create_task(self._read_pcm_stream(output_queue))

            position_info = f" from {start_position:.1f}s" if start_position > 0 else ""
            mode = "stream" if http else "file"
            logger.info(
                f"Started audio decoding ({mode}): {_source_label(source)}{position_info} "
                f"[{self.sample_rate}Hz, {self.channels}ch]"
            )
            return True

        except asyncio.CancelledError:
            await self.stop()
            raise
        except Exception as e:
            logger.error(f"Failed to start audio decoding: {e}", exc_info=True)
            await self.stop()
            return False

    async def _read_pcm_stream(self, output_queue: asyncio.Queue):
        frame_size_samples = int(self.sample_rate * AudioConfig.FRAME_DURATION / 1000)
        sample_bytes = 2 * self.channels
        frame_size_bytes = frame_size_samples * sample_bytes
        process = self._process
        pending = b""
        cancelled = False
        stderr_task = asyncio.create_task(self._read_stderr(process))
        try:
            while not self._stopped:
                chunk = await process.stdout.read(frame_size_bytes)
                if not chunk:
                    returncode = await asyncio.wait_for(process.wait(), timeout=2.0)
                    self.completed_successfully = returncode == 0 and not pending
                    if not self.completed_successfully:
                        logger.warning(
                            "FFmpeg decoding failed or PCM truncated, exit code: %s", returncode
                        )
                    break

                pending += chunk
                usable = len(pending) - len(pending) % sample_bytes
                if not usable:
                    continue
                audio = np.frombuffer(pending[:usable], dtype="<i2")
                audio = audio.astype(np.float32) / 32768.0
                pending = pending[usable:]
                if self.channels > 1:
                    audio = audio.reshape(-1, self.channels)
                # Bounded queue provides backpressure without dropping half a PCM sample on short network reads.
                await output_queue.put(audio)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as e:
            self.completed_successfully = False
            logger.error(f"Failed to read PCM stream: {e}", exc_info=True)
        finally:
            if not self.completed_successfully:
                await self._terminate_process(process)
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            # All non-cancelled terminal states wake downstream; caller can inspect completed_successfully to distinguish failures.
            if not cancelled and not self._stopped:
                await output_queue.put(None)

    @staticmethod
    async def _read_stderr(process) -> None:
        # Drain stderr concurrently to prevent heavy error logs from filling the pipe and blocking stdout.
        tail = b""
        while chunk := await process.stderr.read(4096):
            tail = (tail + chunk)[-300:]
        if tail.strip():
            logger.warning("FFmpeg: %s", tail.decode("utf-8", errors="replace").strip())

    async def stop(self):
        self._stopped = True
        logger.debug("Stopping audio decoder")
        task = self._decode_task
        self._decode_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        process, probe = self._process, self._probe_process
        self._process = None
        self._probe_process = None
        await self._terminate_process(process)
        await self._terminate_process(probe)

    def is_running(self) -> bool:
        return (
            not self._stopped
            and self._decode_task is not None
            and not self._decode_task.done()
        )

    async def wait_completion(self):
        if self._decode_task and not self._decode_task.done():
            try:
                await self._decode_task
            except Exception as e:
                logger.error(f"Failed while waiting for decoding completion: {e}", exc_info=True)
