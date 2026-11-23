"""Blacklist of sentences the server's speech recognition invents from noise.

From silence, room noise or music the server STT sometimes returns YouTube outro lines
("Hãy subscribe cho kênh Ghiền Mì Gõ...", "La La School", "cảm ơn các bạn đã theo dõi").
The robot must not answer those. This module decides whether a transcript is such a
hallucination; src/core/stt_gate.py drops the turn when it is.

Rule (fuzzy, accent- and case-insensitive):

1. The text is folded: lower case, Vietnamese accents removed (đ -> d), punctuation dropped,
   split into words.
2. Each pattern is looked for as a run of words whose letters (spaces ignored, so "lala" and
   "la la" are the same) are within a small edit distance of the pattern's letters
   (patterns of 5+ letters allow about one typo per 4 letters, at most 3: "subcribe",
   "la la scool", "Diền Mì Gõ").
   Patterns of 4 letters or less must match exactly, and a fuzzy match of a pattern of
   several words needs at least half of its words spelled exactly ("chiên mì gói" is not
   "Ghiền Mì Gõ", "Diền Mì Gõ" is).
3. The text is ignored when at least one pattern matched and every word left over is a
   filler word (hãy, cho, kênh, các, bạn, nhé, ...), or when the whole text has fewer than
   MIN_CHARS letters. Texts of more than 30 words are never ignored. Anything else is a real
   request: "mở nhạc La La School" keeps
   "mở nhạc" and is answered.

Patterns live in STT_FILTER.PATTERNS (config), editable from the web panel. One shared
instance (get_stt_filter) is used by the app loop and the web server's thread.
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from src.logging import get_logger

logger = get_logger()

CONFIG_SECTION = "STT_FILTER"
DEFAULT_MIN_CHARS = 2
MIN_CHARS_RANGE = (0, 20)
MAX_PATTERNS = 200
MAX_PATTERN_CHARS = 120
RECENT_IGNORED = 20
MAX_TYPOS = 3
MAX_CHECKED_WORDS = 30

DEFAULT_PATTERNS: tuple[str, ...] = (
    # Channel names and plugs the STT is known to invent
    "La La School",
    "Ghiền Mì Gõ",
    "Hãy subscribe cho kênh Ghiền Mì Gõ",
    "Hãy subscribe cho kênh",
    "subscribe",
    "subcribe",
    "subscribe kênh YouTube",
    "kênh YouTube",
    "đăng ký kênh",
    "đăng kí kênh",
    "đừng quên đăng ký kênh",
    "nhấn nút đăng ký",
    "bấm chuông thông báo",
    "nhấn chuông thông báo",
    "theo dõi kênh",
    "ủng hộ kênh",
    "ủng hộ mình",
    "like và share",
    "like share và subscribe",
    "chia sẻ video",
    "để không bỏ lỡ những video hấp dẫn",
    "để không bỏ lỡ những video mới nhất",
    # Outros
    "cảm ơn các bạn đã theo dõi",
    "cảm ơn các bạn đã theo dõi và hẹn gặp lại",
    "cảm ơn các bạn đã xem video",
    "cảm ơn các bạn đã lắng nghe",
    "cảm ơn các bạn đã xem",
    "hẹn gặp lại các bạn trong những video tiếp theo",
    "hẹn gặp lại các bạn trong video tiếp theo",
    "hẹn gặp lại các bạn",
    "chúc các bạn xem video vui vẻ",
    # Subtitle credits
    "Vietsub",
    "phụ đề bởi",
    "Thanks for watching",
    "Thank you for watching",
    "Subtitles by the Amara.org community",
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
    "字幕由Amara.org社区提供",
)

# Words that carry no request on their own once the hallucinated phrase is gone (folded)
FILLER_WORDS = frozenset(
    """
    a ah ha oi o u uh um nhe nha ne the thi roi nao day nay do kia
    hay cho kenh cua minh toi chung cac ban moi nguoi quy vi va voi de nhung video
    tiep theo nua cung nhieu rat lai youtube clip
    """.split()
)

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def fold_words(text: str) -> list[str]:
    """Lower case, accents removed (đ -> d), punctuation dropped, split into words."""
    s = unicodedata.normalize("NFD", (text or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")
    words: list[str] = []
    for word in _WORD.findall(s):
        # Split CJK runs into single characters so spacing does not matter
        if any("㐀" <= c <= "鿿" for c in word):
            words.extend(word)
        else:
            words.append(word)
    return words


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, giving up (returning limit + 1) once it must exceed limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def allowed_typos(letters: int) -> int:
    if letters <= 4:
        return 0
    return min(MAX_TYPOS, max(1, letters // 4))


@dataclass(frozen=True)
class _Pattern:
    text: str  # As written by the user
    words: tuple[str, ...]
    letters: str
    typos: int


def compile_pattern(text: str) -> _Pattern | None:
    words = tuple(fold_words(text))
    letters = "".join(words)
    if not letters:
        return None
    return _Pattern(text, words, letters, allowed_typos(len(letters)))


@dataclass(frozen=True)
class FilterResult:
    ignored: bool
    reason: str = ""  # "pattern", "short" or ""
    patterns: tuple[str, ...] = ()
    leftover: str = ""  # Words that were neither a pattern nor filler

    @property
    def label(self) -> str:
        if self.reason == "short":
            return "quá ngắn"
        if self.patterns:
            return ", ".join(self.patterns)
        return ""


def _may_contain(pattern: _Pattern, words: list[str], letters: str) -> bool:
    """Cheap pre-check: one of the pattern's longer words appears (or nearly) in the text."""
    for anchor in pattern.words:
        if anchor in letters:
            return True
        if len(anchor) >= 3:
            for word in words:
                if _edit_distance(word, anchor, 1) <= 1:
                    return True
    return False


def _enough_words(pattern: _Pattern, window_words: list[str]) -> bool:
    """A fuzzy match of a multi-word pattern needs half its words spelled exactly."""
    k = len(pattern.words)
    if k < 2:
        return True
    pool = list(pattern.words)
    common = 0
    for word in window_words:
        if word in pool:
            pool.remove(word)
            common += 1
            continue
        # Words run together ("lala" for "la la") count as all of them
        for i in range(k):
            for j in range(i + 2, k + 1):
                if "".join(pattern.words[i:j]) == word:
                    common += j - i
                    break
            else:
                continue
            break
    return common * 2 >= k


def check_text(text: str, patterns, min_chars: int = DEFAULT_MIN_CHARS) -> FilterResult:
    """Decide whether text is only an STT hallucination (see the module rule)."""
    words = fold_words(text)
    letters = "".join(words)
    if len(letters) < max(0, min_chars):
        return FilterResult(True, "short")
    if len(words) > MAX_CHECKED_WORDS:
        # Outros are short; a long sentence is speech (and checking it would cost time)
        return FilterResult(False, "", (), "")
    compiled = [p if isinstance(p, _Pattern) else compile_pattern(p) for p in patterns]
    compiled = sorted((p for p in compiled if p is not None), key=lambda p: -len(p.letters))
    covered = [False] * len(words)
    matched: list[str] = []
    for pattern in compiled:
        if not _may_contain(pattern, words, letters):
            continue
        k = len(pattern.words)
        sizes = range(max(1, k - 2), k + 3)
        hit = False
        while True:
            # Best window first (fewest edits, then closest word count), so a fuzzy match
            # never swallows a neighbouring word another pattern needs
            best = None
            for i in range(len(words)):
                for size in sizes:
                    window = range(i, i + size)
                    if window.stop > len(words) or any(covered[j] for j in window):
                        continue
                    joined = "".join(words[j] for j in window)
                    dist = _edit_distance(joined, pattern.letters, pattern.typos)
                    if dist <= pattern.typos and (
                        dist == 0 or _enough_words(pattern, [words[j] for j in window])
                    ):
                        key = (dist, abs(size - k), i)
                        if best is None or key < best[0]:
                            best = (key, window)
            if best is None:
                break
            for j in best[1]:
                covered[j] = True
            hit = True
        if hit:
            matched.append(pattern.text)
    leftover = [w for w, c in zip(words, covered) if not c and w not in FILLER_WORDS]
    if matched and not leftover:
        return FilterResult(True, "pattern", tuple(matched))
    return FilterResult(False, "", tuple(matched), " ".join(leftover))


def clean_pattern(value) -> str:
    """A pattern as entered on the web page, or ValueError."""
    if not isinstance(value, str):
        raise ValueError("mẫu phải là chuỗi")
    value = " ".join(value.split())
    if not value:
        raise ValueError("mẫu trống")
    if len(value) > MAX_PATTERN_CHARS:
        raise ValueError(f"mẫu dài quá {MAX_PATTERN_CHARS} ký tự")
    if compile_pattern(value) is None:
        raise ValueError("mẫu phải có chữ hoặc số")
    return value


class SttFilter:
    def __init__(
        self,
        enabled: bool = True,
        patterns=None,
        min_chars: int = DEFAULT_MIN_CHARS,
        save: Callable[[dict], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._lock = threading.Lock()
        self._enabled = bool(enabled)
        self._min_chars = self._clean_min_chars(min_chars, DEFAULT_MIN_CHARS)
        self._patterns: list[str] = []
        self._compiled: list[_Pattern] = []
        self._set_patterns_locked(DEFAULT_PATTERNS if patterns is None else patterns)
        self._save = save
        self._clock = clock
        self._recent: deque = deque(maxlen=RECENT_IGNORED)
        self._ignored_total = 0
        self._listeners: list[Callable[[], None]] = []

    @staticmethod
    def _clean_min_chars(value, fallback: int) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return fallback
        return int(max(MIN_CHARS_RANGE[0], min(MIN_CHARS_RANGE[1], value)))

    def _set_patterns_locked(self, patterns) -> None:
        seen: set[str] = set()
        self._patterns, self._compiled = [], []
        for raw in patterns or ():
            try:
                text = clean_pattern(raw)
            except ValueError:
                continue
            compiled = compile_pattern(text)
            if compiled.letters in seen or len(self._patterns) >= MAX_PATTERNS:
                continue
            seen.add(compiled.letters)
            self._patterns.append(text)
            self._compiled.append(compiled)

    @classmethod
    def from_config(cls, config) -> SttFilter:
        def save(updates: dict) -> None:
            paths = {f"{CONFIG_SECTION}.{k}": v for k, v in updates.items()}
            if hasattr(config, "update_configs"):
                config.update_configs(paths)
            else:
                for path, value in paths.items():
                    config.update_config(path, value)

        get = config.get_config
        patterns = get(f"{CONFIG_SECTION}.PATTERNS", None)
        if not isinstance(patterns, list):
            patterns = None
        return cls(
            enabled=bool(get(f"{CONFIG_SECTION}.ENABLED", True)),
            patterns=patterns,
            min_chars=get(f"{CONFIG_SECTION}.MIN_CHARS", DEFAULT_MIN_CHARS),
            save=save,
        )

    # ----- Checking -----

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def patterns(self) -> list[str]:
        with self._lock:
            return list(self._patterns)

    @property
    def min_chars(self) -> int:
        with self._lock:
            return self._min_chars

    def check(self, text: str) -> FilterResult:
        """The verdict for one transcript (never ignored while the filter is off)."""
        with self._lock:
            if not self._enabled:
                return FilterResult(False)
            compiled, min_chars = list(self._compiled), self._min_chars
        return check_text(text, compiled, min_chars)

    def record_ignored(self, text: str, result: FilterResult) -> None:
        with self._lock:
            self._ignored_total += 1
            self._recent.append(
                {"time": self._clock(), "text": (text or "")[:300], "pattern": result.label}
            )
        self._notify()

    # ----- Editing (web panel) -----

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
        self._persist()

    def set_min_chars(self, value: int) -> int:
        with self._lock:
            self._min_chars = self._clean_min_chars(value, self._min_chars)
            value = self._min_chars
        self._persist()
        return value

    def add_pattern(self, text) -> str:
        text = clean_pattern(text)
        compiled = compile_pattern(text)
        with self._lock:
            if any(p.letters == compiled.letters for p in self._compiled):
                raise ValueError(f"đã có mẫu này: {text}")
            if len(self._patterns) >= MAX_PATTERNS:
                raise ValueError(f"tối đa {MAX_PATTERNS} mẫu")
            self._patterns.append(text)
            self._compiled.append(compiled)
        self._persist()
        return text

    def remove_pattern(self, text) -> str:
        if not isinstance(text, str):
            raise ValueError("mẫu phải là chuỗi")
        with self._lock:
            try:
                index = self._patterns.index(text)
            except ValueError:
                raise ValueError(f"không có mẫu: {text}") from None
            del self._patterns[index]
            del self._compiled[index]
        self._persist()
        return text

    def reset_patterns(self) -> None:
        with self._lock:
            self._set_patterns_locked(DEFAULT_PATTERNS)
        self._persist()

    def clear_recent(self) -> None:
        with self._lock:
            self._recent.clear()
        self._notify()

    # ----- Persistence and listeners -----

    def _persist(self) -> None:
        if self._save is not None:
            with self._lock:
                updates = {
                    "ENABLED": self._enabled,
                    "PATTERNS": list(self._patterns),
                    "MIN_CHARS": self._min_chars,
                }
            try:
                self._save(updates)
            except Exception:
                logger.warning("Cannot save STT_FILTER", exc_info=True)
        self._notify()

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                logger.debug("STT filter listener failed", exc_info=True)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "enabled": self._enabled,
                "patterns": list(self._patterns),
                "min_chars": self._min_chars,
                "ignored_total": self._ignored_total,
                "recent": list(reversed(self._recent)),
                "defaults": len(DEFAULT_PATTERNS),
            }


_shared: SttFilter | None = None
_shared_lock = threading.Lock()


def get_stt_filter(config=None) -> SttFilter:
    """The process-wide filter, created from the config on first use."""
    global _shared
    with _shared_lock:
        if _shared is None:
            if config is None:
                from src.utils.config_manager import get_config

                try:
                    config = get_config()
                except RuntimeError:
                    config = None
            _shared = SttFilter.from_config(config) if config is not None else SttFilter()
        return _shared


def reset_stt_filter() -> None:
    """Forget the shared filter (tests only)."""
    global _shared
    with _shared_lock:
        _shared = None
