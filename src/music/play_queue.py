"""Play queue: order, current item, repeat and shuffle. Pure logic, no audio."""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass

REPEAT_MODES = ("off", "all", "one")
MAX_QUEUE = 2000


@dataclass(frozen=True)
class QueueItem:
    uid: int  # Unique in the queue (a track can be queued twice)
    track_id: str
    seq: int  # Place in the unshuffled order


class PlayQueue:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._uids = itertools.count(1)
        self._items: list[QueueItem] = []
        self._index = -1
        self.repeat = "off"
        self.shuffle = False
        self.revision = 0

    # ----- Reading -----

    @property
    def items(self) -> tuple[QueueItem, ...]:
        return tuple(self._items)

    @property
    def index(self) -> int:
        return self._index

    @property
    def current(self) -> QueueItem | None:
        items, index = self._items, self._index
        return items[index] if 0 <= index < len(items) else None

    def __len__(self) -> int:
        return len(self._items)

    # ----- Changing -----

    def _make(self, track_id: str, seq: int) -> QueueItem:
        return QueueItem(next(self._uids), track_id, seq)

    def load(self, track_ids: list[str], start: str | None = None) -> QueueItem | None:
        """Replace the queue. With shuffle, start plays first and the rest is mixed."""
        ids = list(track_ids)[:MAX_QUEUE]
        if start is not None and start not in ids:
            ids.insert(0, start)
        items = [self._make(t, i) for i, t in enumerate(ids)]
        index = -1
        if items:
            index = next((i for i, it in enumerate(items) if it.track_id == start), 0)
            if self.shuffle:
                if start is None:
                    index = self._rng.randrange(len(items))
                first = items.pop(index)
                self._rng.shuffle(items)
                items.insert(0, first)
                index = 0
        self._items, self._index = items, index
        self.revision += 1
        return self.current

    def add(self, track_id: str, play_next: bool = False) -> QueueItem:
        if len(self._items) >= MAX_QUEUE:
            raise ValueError("hàng đợi đã đầy")
        item = self._make(track_id, max((it.seq for it in self._items), default=-1) + 1)
        if play_next:
            self._items.insert(self._index + 1, item)
        else:
            self._items.append(item)
        if self._index < 0:
            self._index = 0
        self.revision += 1
        return item

    def remove(self, uid: int) -> bool:
        """Remove one item (the caller decides what to do when it is the current one:
        the index then points at the item that followed it, if any)."""
        for i, it in enumerate(self._items):
            if it.uid == uid:
                items = self._items[:i] + self._items[i + 1:]
                index = self._index - 1 if i < self._index else self._index
                self._items, self._index = items, min(index, len(items) - 1)
                self.revision += 1
                return True
        return False

    def clear(self, keep_current: bool = True) -> None:
        current = self.current if keep_current else None
        self._items = [current] if current else []
        self._index = 0 if current else -1
        self.revision += 1

    def select(self, uid: int) -> QueueItem | None:
        for i, it in enumerate(self._items):
            if it.uid == uid:
                self._index = i
                self.revision += 1
                return it
        return None

    def set_repeat(self, mode: str) -> None:
        if mode not in REPEAT_MODES:
            raise ValueError(f"chế độ lặp không có: {mode}")
        if mode != self.repeat:
            self.repeat = mode
            self.revision += 1

    def set_shuffle(self, on: bool) -> None:
        """Mix what comes after the current item, or put the queue back in order."""
        if on == self.shuffle:
            return
        self.shuffle = on
        current = self.current
        if on:
            tail = self._items[self._index + 1:]
            self._rng.shuffle(tail)
            self._items = self._items[: self._index + 1] + tail
        else:
            self._items = sorted(self._items, key=lambda it: it.seq)
            if current is not None:
                self._index = self._items.index(current)
        self.revision += 1

    # ----- Moving -----

    def next(self, auto: bool = False) -> QueueItem | None:
        """Advance. auto: the song ended by itself (repeat "one" then plays it again)."""
        if not self._items:
            return None
        if auto and self.repeat == "one":
            return self.current
        if self._index + 1 < len(self._items):
            self._index += 1
        elif self.repeat == "all" or (not auto and self.repeat == "one"):
            if self.shuffle:
                items = list(self._items)
                self._rng.shuffle(items)
                self._items = items
            self._index = 0
        else:
            return None
        self.revision += 1
        return self.current

    def previous(self) -> QueueItem | None:
        if not self._items:
            return None
        if self._index > 0:
            self._index -= 1
        elif self.repeat == "all":
            self._index = len(self._items) - 1
        self.revision += 1
        return self.current
