"""Web panel side of the music player: commands, library listing and uploads.

The player lives on the app's event loop; everything here may run on the web server's
thread. Commands that change playback are posted to that loop and answer with a
concurrent Future the server awaits without blocking other messages.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable

from src.logging import get_logger
from src.music.library import EXTENSIONS, LibraryError

logger = get_logger()


class MusicControl:
    def __init__(self, player, get_loop: Callable[[], asyncio.AbstractEventLoop | None]) -> None:
        self.player = player
        self._get_loop = get_loop

    # ----- Reading (web thread; blocking calls go through an executor) -----

    def snapshot(self) -> dict:
        return self.player.snapshot()

    def library_json(self) -> dict:
        library = self.player.library
        tracks = [t.to_json() for t in library.tracks()]
        return {
            "rev": library.revision,
            "tracks": tracks,
            "usage": library.usage(),
            "max_upload": library.max_upload,
            "max_total": library.max_total,
            "extensions": [e.lstrip(".") for e in EXTENSIONS],
        }

    def queue_json(self) -> dict:
        self.player.library.scan()
        return self.player.queue_snapshot()

    def cover(self, track_id: str) -> bytes | None:
        return self.player.library.cover(track_id)

    def begin_upload(self, name: str):
        return self.player.library.begin_upload(name)

    # ----- Commands -----

    def submit(self, make) -> concurrent.futures.Future:
        """Run make() (a coroutine function, or a plain one) on the app's loop."""
        loop = self._get_loop()
        if loop is None or loop.is_closed():
            raise LibraryError("app chưa sẵn sàng")

        async def run():
            result = make()
            if asyncio.iscoroutine(result):
                result = await result
            return {"ok": result.get("status") != "error", "message": result.get("message", "")}

        return asyncio.run_coroutine_threadsafe(run(), loop)

    def play(self, track_id: str | None):
        p = self.player
        return self.submit(lambda: p.play_track(track_id) if track_id else p.play())

    def pause(self):
        return self.submit(self.player.pause)

    def stop(self):
        return self.submit(self.player.stop)

    def next(self):
        return self.submit(self.player.next_track)

    def previous(self):
        return self.submit(self.player.previous_track)

    def seek(self, position: float):
        return self.submit(lambda: self.player.seek(position=position))

    def volume(self, volume: float, save: bool):
        return self.submit(lambda: self.player.set_volume(volume, save=save))

    def mode(self, repeat: str | None, shuffle: bool | None):
        return self.submit(lambda: self.player.set_modes(repeat, shuffle))

    def enqueue(self, track_id: str, play_next: bool):
        return self.submit(lambda: self.player.enqueue(track_id, play_next))

    def dequeue(self, uid: int):
        return self.submit(lambda: self.player.dequeue(uid))

    def queue_play(self, uid: int):
        return self.submit(lambda: self.player.queue_play(uid))

    def queue_clear(self):
        return self.submit(self.player.clear_queue)

    def delete(self, track_id: str):
        return self.submit(lambda: self.player.delete_track(track_id))

    def dance(self, enabled: bool):
        return self.submit(lambda: self.player.set_dance(enabled))
