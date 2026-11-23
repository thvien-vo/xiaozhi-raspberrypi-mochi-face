"""Music playback session (public entry point).

Composition:
- PlaybackEngine - decode queue / start, stop, pause, resume, seek
- MusicEventBridge - EventBus binding and state/lyrics emission
- MusicLibrary + PlayQueue - the robot's own files, what plays next (src/music)

State fields live on the engine; this class exposes commonly used production properties (is_playing / paused / current_song).
Voice (MCP tools) and the web panel use the same methods, so both see the same queue. The
coroutines must run on the app's event loop; snapshot(), beat_state() and the properties
can be read from any thread.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path

from src.audio_codecs.music_decoder import MusicDecoder
from src.logging import get_logger
from src.music.beat import FALLBACK, analyze_file
from src.music.library import MusicLibrary, Track
from src.music.matching import wants_anything
from src.music.play_queue import REPEAT_MODES, PlayQueue

from .bus import MusicEventBridge
from .cache import MusicCache
from .config import load_music_config, plays_through_speech
from .download import MusicDownloader
from .local_library import LocalLibrary
from .lyrics import fetch_kuwo_lyrics, format_lyric_display, lyric_at
from .online_search import search_song
from .playback import PlaybackDeps, PlaybackEngine

logger = get_logger()

MAX_FAILURES = 3  # Undecodable files in a row before giving up
RESTART_AFTER_S = 4.0  # "Previous" restarts the song when further in than this
BEAT_DELAY_S = 2.0  # Let playback settle before analyzing the file
NO_AUDIO = "Robot không có thiết bị âm thanh (loa Jabra chưa cắm hoặc chưa mở được), chưa phát được nhạc"
NO_SOUND = "Thiết bị âm thanh của robot không chạy (loa vừa bị rút?)"


def _error(message: str) -> dict:
    return {"status": "error", "message": message}


def _ok(message: str, **extra) -> dict:
    return {"status": "success", "message": message, **extra}


class MusicPlayer:
    def __init__(self):
        self._config: dict | None = None
        self._cache = MusicCache()
        self._downloader = MusicDownloader(self._cache)
        self._library = LocalLibrary(self._cache)

        self.current_url = ""
        self.lyrics: list[tuple[float, str]] = []

        self._engine = PlaybackEngine(
            PlaybackDeps(
                cache=self._cache,
                downloader=self._downloader,
                library=self._library,
                hooks=self,
            )
        )
        self._bus = MusicEventBridge(self._engine, self)

        # The robot's own library and queue
        self.queue = PlayQueue()
        self.track_id: str | None = None  # Library track being played (None: online song)
        self.last_error = ""
        self._files: MusicLibrary | None = None
        self._files_lock = threading.Lock()
        self._settings_loaded = False
        self._op_lock = asyncio.Lock()  # One transport change at a time
        self._play_token = 0
        self._failures = 0
        self._beat: dict | None = None
        self._tasks: set[asyncio.Task] = set()
        self._listeners: list[Callable[[], None]] = []
        self._device_name = ""
        self.dancer = None  # MusicDancer, when the robot has servos

        logger.debug("MusicPlayer instance created")

    # ----- PlaybackEngine hooks -----

    def prepare_for_io(self) -> None:
        self.reload_config()
        self._cache.prepare()

    def is_speaking(self) -> bool:
        """Whether playback must wait for the assistant to finish speaking."""
        ctx = self._bus.plugin_ctx
        if not ctx or not ctx.is_speaking():
            return False
        try:
            if plays_through_speech(ctx.get_config(), self._engine.audio_codec):
                return False  # Plays at once, turned down
        except Exception:
            logger.debug("Cannot read the ducking settings", exc_info=True)
        return True

    def format_time(self, seconds: float) -> str:
        return self._format_time(seconds)

    async def tick_lyrics(self) -> None:
        await self._tick_lyrics()

    async def emit_state_change(
        self,
        state: str,
        song_name: str | None = None,
        position: float | None = None,
    ) -> None:
        await self._bus.emit_state_change(state, song_name, position)
        self._notify()

    def playback_finished(self, failed: bool) -> None:
        """The decoder reached the end (runs inside the playback task)."""
        if self.track_id is not None:
            self._spawn(self._advance(self._play_token, failed))

    # ----- Common production state properties (flat access) -----

    @property
    def cache_dir(self) -> Path:
        return self._cache.root

    @property
    def temp_cache_dir(self) -> Path:
        return self._cache.temp_dir

    @property
    def is_playing(self) -> bool:
        return self._engine.is_playing

    @property
    def paused(self) -> bool:
        return self._engine.paused

    @property
    def current_song(self) -> str:
        return self._engine.current_song

    @property
    def config(self) -> dict:
        if self._config is None:
            self.reload_config()
        return self._config

    def reload_config(self) -> dict:
        self._config = load_music_config()
        self._downloader.set_config(self._config)
        logger.debug(
            "MusicPlayer configuration loaded: "
            f"search={self._config['SEARCH_URL']}, "
            f"url_api={self._config['URL_API']}, "
            f"platform={self._config['DEFAULT_SOURCE']}"
        )
        return self._config

    # ----- Lifecycle / playback delegation -----

    def set_event_bus(self, event_bus, plugin_ctx=None) -> None:
        self._bus.set_event_bus(event_bus, plugin_ctx)
        if plugin_ctx is not None:
            self.load_settings()

    def detach(self) -> None:
        self._bus.detach()
        self._listeners.clear()
        for task in list(self._tasks):
            task.cancel()

    async def stop(self) -> dict:
        async with self._op_lock:
            self._play_token += 1
            result = await self._engine.stop()
        self._notify()
        return result

    async def pause(self, source: str = "manual") -> dict:
        async with self._op_lock:
            return await self._engine.pause(source=source)

    async def resume(self) -> dict:
        """Continue a paused song, or start the current one again after a stop."""
        async with self._op_lock:
            eng = self._engine
            if not eng.is_playing and self.queue.current is not None:
                return await self._start_current()
            if eng.is_playing and eng.paused and not self.audio_status()["ok"]:
                return _error(NO_AUDIO)
            return await eng.resume()

    async def seek(
        self,
        position: float | None = None,
        percent: float | None = None,
    ) -> dict:
        async with self._op_lock:
            eng = self._engine
            if eng.is_playing and eng.paused and eng.pause_source == "manual":
                # Stay paused: only move the point resume() starts from
                total = eng.total_duration
                if percent is not None and percent >= 0 and total > 0:
                    position = total * min(100.0, float(percent)) / 100.0
                if position is not None and position >= 0:
                    eng.current_position = min(float(position), max(0.0, total - 1)) if total > 0 else float(position)
                    self._notify()
                    return _ok(f"Đã tua tới {self._format_time(eng.current_position)} (đang tạm dừng)")
            return await eng.seek(position=position, percent=percent)

    async def get_position(self):
        return await self._engine.get_position()

    async def get_progress(self):
        return await self._engine.get_progress()

    # ----- Song search / local library -----

    async def get_local_playlist(self, force_refresh: bool = False) -> dict:
        if force_refresh:
            await asyncio.to_thread(self.library.scan, True)
        tracks = await asyncio.to_thread(self.library.tracks)
        if not tracks:
            return {
                "status": "info",
                "message": "Thư viện nhạc trên robot đang trống (tải nhạc lên ở trang web, tab Nhạc)",
                "playlist": [],
                "total_count": 0,
            }
        return {
            "status": "success",
            "message": f"{len(tracks)} bài",
            "playlist": [t.display for t in tracks[:60]],
            "total_count": len(tracks),
        }

    async def search_local_music(self, query: str) -> dict:
        hits = await asyncio.to_thread(self.library.search, query)
        return {
            "status": "success",
            "message": f"{len(hits)} bài khớp",
            "results": [
                {"song_info": t.display, "file_id": t.id, "duration": self._format_time(t.duration)} for _, t in hits
            ],
            "found_count": len(hits),
        }

    async def search_and_play(self, song_name: str) -> dict:
        """Play from the robot's library by (roughly heard) name; no name plays anything."""
        song_name = str(song_name or "").strip()[:200]
        try:
            async with self._op_lock:
                return await self._search_and_play(song_name)
        except Exception as e:
            logger.error(f"Search and play failed: {e}", exc_info=True)
            return _error(f"Lỗi khi phát nhạc: {e}. CHƯA có bài nào được phát")

    async def _search_and_play(self, query: str) -> dict:
        if not self.audio_status()["ok"]:
            return _error(NO_AUDIO)
        library = self.library
        tracks = await asyncio.to_thread(library.tracks)
        hits = await asyncio.to_thread(library.search, query) if query else []
        ids = [t.id for t in tracks]
        exact = bool(hits) and hits[0][0] >= 0.97
        if tracks and wants_anything(query) and not exact:
            self.queue.load(ids, random.choice(ids))
            return await self._start_current()
        if hits:
            best, track = hits[0]
            artist = await asyncio.to_thread(library.by_artist, query)
            if best < 0.96 and len(artist) > 1 and track in artist:
                # The query names the artist, not a song: queue everything they sing
                ids = [t.id for t in artist]
            logger.info(f"Library match for {query!r}: {track.display} ({best:.2f})")
            self.queue.load(ids, track.id)
            return await self._start_current()
        if self.online_enabled and query:
            return await self._play_online(query)
        if not tracks:
            return _error(
                "CHƯA phát được: thư viện nhạc trên robot đang trống. Hãy bảo người dùng tải file nhạc lên "
                "ở trang web điều khiển robot, tab Nhạc."
            )
        near = await asyncio.to_thread(library.search, query, 3, 0.45)
        hint = " Bài gần giống: " + "; ".join(t.display for _, t in near) + "." if near else ""
        return _error(
            f"CHƯA phát được: không có bài '{query}' trong thư viện nhạc trên robot ({len(tracks)} bài).{hint} "
            "Đừng nói là đang phát; có thể hỏi người dùng chọn bài khác, hoặc tải bài này lên ở trang web, tab Nhạc."
        )

    async def _play_online(self, song_name: str) -> dict:
        eng = self._engine
        try:
            self._play_token += 1
            self.track_id = None
            self._beat = None
            self.prepare_for_io()
            hit = await search_song(song_name, self.config)
            if hit is None:
                return {"status": "error", "message": f"Song not found: {song_name}"}

            if eng.is_playing:
                await eng.stop()
            eng.current_song = hit.display_name
            eng.song_id = hit.song_id
            eng.total_duration = hit.duration
            self.current_url = hit.api_url
            await self._fetch_lyrics(hit.song_id)

            success = await eng.play_url(hit.api_url)
            if success:
                return {
                    "status": "success",
                    "message": f"Now playing: {eng.current_song}",
                    "song": eng.current_song,
                    "duration": self._format_time(eng.total_duration),
                    "total_seconds": eng.total_duration,
                }

            detail = self._downloader.last_error or "Unknown reason"
            return {"status": "error", "message": f"Playback failed: {detail}"}
        except Exception as e:
            logger.error(f"Search and play failed: {e}", exc_info=True)
            return {"status": "error", "message": f"Operation failed: {str(e)}"}

    # ----- Library playback (voice and web panel) -----

    @property
    def library(self) -> MusicLibrary:
        with self._files_lock:
            if self._files is None:
                self._files = self._open_library()
            return self._files

    @library.setter
    def library(self, library: MusicLibrary) -> None:
        self._files = library

    @staticmethod
    def _open_library() -> MusicLibrary:
        from src.utils.config_manager import get_config
        from src.utils.resource_finder import get_music_library_dir

        config = get_config()
        folder = str(config.get_config("MUSIC.LIBRARY_DIR", None) or "").strip()

        def number(key: str, default: int) -> int:
            try:
                return max(1, int(config.get_config(f"MUSIC.{key}", default)))
            except (TypeError, ValueError):
                return default

        library = MusicLibrary(
            Path(folder).expanduser() if folder else get_music_library_dir(),
            max_upload_mb=number("MAX_UPLOAD_MB", 100),
            max_total_mb=number("MAX_LIBRARY_MB", 8192),
        )
        library.clean_work_dir()
        return library

    def load_settings(self) -> None:
        """Volume and play modes from the config (once)."""
        if self._settings_loaded:
            return
        try:
            from src.utils.config_manager import get_config

            config = get_config()
            mode = str(config.get_config("MUSIC.REPEAT", "off"))
            self.queue.set_repeat(mode if mode in REPEAT_MODES else "off")
            self.queue.set_shuffle(bool(config.get_config("MUSIC.SHUFFLE", False)))
            self._settings_loaded = True
        except Exception as e:
            logger.debug(f"Music settings not loaded: {e}")

    def _save(self, key: str, value) -> None:
        try:
            from src.utils.config_manager import get_config

            get_config().update_config(f"MUSIC.{key}", value)
        except Exception as e:
            logger.debug(f"Cannot save MUSIC.{key}: {e}")

    @property
    def online_enabled(self) -> bool:
        try:
            from src.utils.config_manager import get_config

            return bool(get_config().get_config("MUSIC.ONLINE_ENABLED", False))
        except Exception:
            return False

    def add_listener(self, callback: Callable[[], None]) -> None:
        """callback() after any change; called on the app loop."""
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                logger.debug("Music listener failed", exc_info=True)

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def audio_changed(self, codec) -> None:
        """A new AudioCodec (or None) is in use."""
        self._device_name = str(getattr(codec, "output_device_name", "") or "") if codec is not None else ""
        if codec is not None:
            self.load_settings()
            self.levels.attach(codec)
        self._notify()

    def audio_status(self) -> dict:
        codec = self._engine.audio_codec
        if codec is None:
            return {"ok": False, "device": "", "message": NO_AUDIO}
        if getattr(codec, "output_running", True) is False:
            return {"ok": False, "device": self._device_name, "message": NO_SOUND}
        return {"ok": True, "device": self._device_name, "message": ""}

    @property
    def levels(self):
        """Shared master / voice / music / effects levels; the music slider is its music channel."""
        from src.audio_codecs.levels import get_audio_levels

        return get_audio_levels()

    @property
    def volume(self) -> int:
        return self.levels.get("music")

    async def play_track(self, track_id: str, queue_ids: list[str] | None = None) -> dict:
        """Play one track; the queue becomes queue_ids (default: the whole library)."""
        async with self._op_lock:
            track = await asyncio.to_thread(self.library.get, track_id)
            if track is None:
                return _error("Bài này không còn trong thư viện")
            if queue_ids is None:
                queue_ids = [t.id for t in await asyncio.to_thread(self.library.tracks)]
            self.queue.load(queue_ids, track_id)
            return await self._start_current()

    async def play(self) -> dict:
        """The play button: resume, restart the current song, or start the library."""
        if not self._engine.is_playing and self.queue.current is None:
            return await self.search_and_play("")
        return await self.resume()

    async def toggle(self) -> dict:
        if self._engine.is_playing and not self._engine.paused:
            return await self.pause()
        return await self.play()

    async def next_track(self) -> dict:
        async with self._op_lock:
            if not len(self.queue):
                return _error("Hàng đợi đang trống, không có bài tiếp theo")
            if self.queue.next() is None:
                return _error("Đã hết hàng đợi, không có bài tiếp theo")
            return await self._start_current()

    async def previous_track(self) -> dict:
        async with self._op_lock:
            if not len(self.queue):
                return _error("Hàng đợi đang trống")
            if self._engine.is_playing and self._engine.position() > RESTART_AFTER_S:
                return await self._start_current()
            self.queue.previous()
            return await self._start_current()

    async def _start_current(self) -> dict:
        """Start the queue's current track from the beginning (op lock held)."""
        eng = self._engine
        self._play_token += 1
        token = self._play_token
        status = self.audio_status()
        if not status["ok"]:
            self._notify()
            return _error(status["message"])
        for _ in range(len(self.queue)):
            item = self.queue.current
            track = await asyncio.to_thread(self.library.get, item.track_id) if item else None
            if track is not None:
                break
            if item is None or not self.queue.remove(item.uid):  # File gone
                break
        else:
            track = None
        if track is None:
            return _error("Bài này không còn trong thư viện")

        if eng.is_playing:
            if eng.decoder:
                await eng.decoder.stop()
                eng.decoder = None
            if eng.audio_codec is not None:
                await eng.audio_codec.clear_music_queue()
        self.track_id = track.id
        self.current_url = str(track.path)
        self.lyrics = []
        self._beat = None
        eng.api_url = None
        eng.song_id = ""
        eng.current_song = track.display
        eng.total_duration = track.duration
        if eng.total_duration <= 0:
            eng.total_duration = await MusicDecoder.get_duration(track.path)
        if not await eng.start_playback(track.path):
            eng.is_playing = False
            self.last_error = f"Không phát được '{track.display}': máy chưa có ffmpeg hoặc file hỏng"
            self._notify()
            return _error(self.last_error + ". CHƯA có bài nào được phát")
        self.last_error = ""
        self._spawn(self._analyze(track, token))
        message = f"Đang phát: {track.display}"
        if eng.paused:
            message = f"Sẽ phát ngay khi robot nói xong: {track.display}"
        return _ok(message, song=track.display, track_id=track.id, total_seconds=eng.total_duration)

    async def _advance(self, token: int, failed: bool) -> None:
        """Next song after one ended by itself."""
        async with self._op_lock:
            if token != self._play_token or self._engine.is_playing:
                return  # Someone chose something meanwhile
            self._failures = self._failures + 1 if failed else 0
            if failed:
                self.last_error = f"Không giải mã được '{self._engine.current_song}', bỏ qua"
                if self._failures >= min(MAX_FAILURES, max(1, len(self.queue))):
                    self._failures = 0
                    self._notify()
                    return
            error = self.last_error if failed else ""
            if self.queue.next(auto=not failed) is not None:
                await self._start_current()
            self.last_error = error or self.last_error
            self._notify()

    # ----- Volume, modes, queue -----

    def set_volume(self, volume: float, save: bool = True) -> dict:
        self.load_settings()
        level = self.levels.set("music", volume, persist=save)
        self._notify()
        return _ok(f"Âm lượng nhạc: {level}%", volume=level)

    def change_volume(self, delta: float) -> dict:
        self.load_settings()
        return self.set_volume(self.volume + delta)

    def set_modes(self, repeat: str | None = None, shuffle: bool | None = None) -> dict:
        self.load_settings()
        if repeat is not None:
            self.queue.set_repeat(repeat)
            self._save("REPEAT", repeat)
        if shuffle is not None:
            self.queue.set_shuffle(shuffle)
            self._save("SHUFFLE", shuffle)
        self._notify()
        label = {"off": "không lặp", "all": "lặp cả danh sách", "one": "lặp một bài"}[self.queue.repeat]
        return _ok(f"Chế độ phát: {label}, trộn bài {'bật' if self.queue.shuffle else 'tắt'}")

    async def enqueue(self, track_id: str, play_next: bool = False) -> dict:
        async with self._op_lock:
            track = await asyncio.to_thread(self.library.get, track_id)
            if track is None:
                return _error("Bài này không còn trong thư viện")
            try:
                self.queue.add(track_id, play_next)
            except ValueError as e:
                return _error(str(e))
            self._notify()
            return _ok(f"{'Phát tiếp theo' if play_next else 'Đã thêm vào hàng đợi'}: {track.title}")

    async def dequeue(self, uid: int) -> dict:
        async with self._op_lock:
            current = self.queue.current
            if current is not None and current.uid == uid and self._engine.is_playing:
                return _error("Bài này đang phát; hãy chuyển bài trước khi bỏ khỏi hàng đợi")
            if not self.queue.remove(uid):
                return _error("Bài này không còn trong hàng đợi")
            self._notify()
            return _ok("")

    async def queue_play(self, uid: int) -> dict:
        async with self._op_lock:
            if self.queue.select(uid) is None:
                return _error("Bài này không còn trong hàng đợi")
            return await self._start_current()

    async def clear_queue(self) -> dict:
        async with self._op_lock:
            self.queue.clear(keep_current=self._engine.is_playing)
            self._notify()
            return _ok("Đã xóa hàng đợi")

    async def delete_track(self, track_id: str) -> dict:
        async with self._op_lock:
            if self.track_id == track_id and self._engine.is_playing:
                self._play_token += 1
                await self._engine.stop()
            for item in [it for it in self.queue.items if it.track_id == track_id]:
                self.queue.remove(item.uid)
            try:
                track = await asyncio.to_thread(self.library.delete, track_id)
            except OSError as e:
                return _error(f"Không xóa được: {e.strerror or e}")
            if track is None:
                return _error("Bài này không còn trong thư viện")
            if self.track_id == track_id:
                self.track_id = None
                self._engine.current_song = ""
                self._engine.current_position = self._engine.total_duration = 0.0
            self._notify()
            return _ok(f"Đã xóa '{track.display}'")

    # ----- Beat and dance -----

    def set_dance(self, on: bool, save: bool = True) -> dict:
        if self.dancer is None:
            return _error("Robot không có servo (SERVO.ENABLED=false)")
        self.dancer.set_enabled(bool(on))
        if save:
            self._save("DANCE", bool(on))
        self._notify()
        return _ok("Đã bật nhảy theo nhạc" if on else "Đã tắt nhảy theo nhạc")


    async def _analyze(self, track: Track, token: int) -> None:
        beat = await asyncio.to_thread(self.library.load_beat, track)
        if beat is None:
            await asyncio.sleep(BEAT_DELAY_S)
            if token != self._play_token:
                return
            beat = await asyncio.to_thread(analyze_file, track.path)
            await asyncio.to_thread(self.library.save_beat, track, beat)
            logger.info(f"Beat of {track.display}: {beat['bpm']:.0f} BPM (confidence {beat['confidence']:.2f})")
        if token == self._play_token:
            self._beat = beat
            self._notify()

    @property
    def bpm(self) -> float:
        return float((self._beat or FALLBACK).get("bpm") or FALLBACK["bpm"])

    @property
    def sounding(self) -> bool:
        return self._engine.is_playing and not self._engine.paused

    def beat_state(self) -> tuple[float, float, float] | None:
        """(position, bpm, offset) while music sounds (any thread)."""
        if not self.sounding:
            return None
        beat = self._beat or FALLBACK
        return self._engine.position(), self.bpm, float(beat.get("offset") or 0.0)

    # ----- Snapshots for the web panel (any thread, no I/O) -----

    def snapshot(self) -> dict:
        eng = self._engine
        files = self._files
        track = files.peek(self.track_id) if files is not None and self.track_id else None
        current = self.queue.current
        if not eng.is_playing:
            state = "stopped"
        elif eng.paused:
            state = "paused"
        else:
            state = "playing"
        info = track.to_json() if track is not None else None
        if info is None and eng.current_song and (eng.is_playing or self.track_id is None):
            info = {"id": None, "title": eng.current_song, "artist": "", "album": "", "cover": False}
        return {
            "state": state,
            "pause_source": eng.pause_source if state == "paused" else None,
            "track": info,
            # Whole seconds: the page moves the bar itself and only needs corrections
            "position": int(eng.position()) if eng.is_playing else 0,
            "duration": round(eng.total_duration, 1),
            "volume": self.volume,
            "repeat": self.queue.repeat,
            "shuffle": self.queue.shuffle,
            "queue_rev": self.queue.revision,
            "queue_uid": current.uid if current else None,
            "library_rev": files.revision if files is not None else 0,
            "audio": self.audio_status(),
            "bpm": round(self.bpm) if self._beat and self._beat.get("confidence") else None,
            "dance": {
                "available": self.dancer is not None,
                "enabled": bool(self.dancer and self.dancer.enabled),
                "active": bool(self.dancer and self.dancer.dancing),
            },
            "error": self.last_error,
        }

    def queue_snapshot(self) -> dict:
        items = []
        for it in self.queue.items:
            track = self.library.peek(it.track_id)
            if track is not None:
                items.append({"uid": it.uid, "id": it.track_id, "title": track.title, "artist": track.artist,
                              "duration": round(track.duration, 1)})
        current = self.queue.current
        return {"rev": self.queue.revision, "uid": current.uid if current else None, "items": items}

    async def get_lyrics(self) -> dict:
        if not self.lyrics:
            return {"status": "info", "message": "No lyrics available for the current song", "lyrics": []}
        lines = [f"[{self._format_time(t)}] {text}" for t, text in self.lyrics]
        return {
            "status": "success",
            "message": f"Retrieved {len(self.lyrics)} lyric lines",
            "lyrics": lines,
        }

    async def get_status(self) -> dict:
        eng = self._engine
        position = await eng.get_position()
        progress = await eng.get_progress()
        if not eng.is_playing:
            playing_state = "Not playing"
        elif eng.paused and eng.pause_source == "manual":
            playing_state = "Paused"
        elif eng.is_playing:
            playing_state = "Playing"
        else:
            playing_state = "Unknown"

        return {
            "status": "success",
            "message": (
                f"Current song: {eng.current_song}\n"
                f"Playback state: {playing_state}\n"
                f"Pause source: {eng.pause_source or 'None'} (tts=temporarily paused while speaking)\n"
                f"Total duration (seconds): {int(eng.total_duration)}\n"
                f"Current position (seconds): {int(position)}\n"
                f"Total duration: {self._format_time(eng.total_duration)}\n"
                f"Current position: {self._format_time(position)}\n"
                f"Playback progress: {progress}%\n"
                f"Lyrics available: {'Yes' if len(self.lyrics) > 0 else 'No'}\n"
                f"Tip: To seek to N%, call seek(percent=N) rather than estimating from lyrics"
            ),
        }

    # ----- Lyrics -----

    async def _tick_lyrics(self) -> None:
        if not self.lyrics:
            return
        eng = self._engine
        now = time.time()
        if now - eng.last_lyric_tick < 0.2:
            return
        eng.last_lyric_tick = now

        position = now - eng.start_play_time if eng.start_play_time > 0 else 0.0
        hit = lyric_at(self.lyrics, position)
        if hit is None:
            return
        idx, text = hit
        if idx == eng.current_lyric_index:
            return
        eng.current_lyric_index = idx
        display = format_lyric_display(text, position, eng.total_duration)
        await self._bus.emit_lyrics_update(display, self.lyrics[idx][0])
        logger.debug(f"Displaying lyric: {text}")

    async def _fetch_lyrics(self, song_id: str):
        eng = self._engine
        self.lyrics = await fetch_kuwo_lyrics(
            song_id,
            lyrics_url=self.config["LYRICS_URL"],
            headers=self.config["HEADERS"],
        )
        if eng.total_duration == 0 and self.lyrics:
            last_time, _ = self.lyrics[-1]
            eng.total_duration = last_time + 5.0
            logger.info(f"Estimated song duration from lyrics: {eng.total_duration}s")

    def _format_time(self, seconds: float) -> str:
        minutes = int(seconds) // 60
        secs = int(seconds) % 60
        return f"{minutes:02d}:{secs:02d}"

    def __del__(self):
        try:
            cache = getattr(self, "_cache", None)
            if cache is not None and getattr(cache, "_ready", False):
                cache.clean_temp()
        except Exception as e:
            logger.debug(f"__del__ failed to clean temporary cache: {e}")
