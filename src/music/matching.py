"""Fuzzy song lookup that ignores case, Vietnamese marks and filler words.

Song names arrive from speech recognition, so they may be misspelled, lack marks, carry
words such as "bài hát" or "cho tôi nghe", or name only the artist.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

MATCH_THRESHOLD = 0.66
MAX_QUERY_CHARS = 120

_NON_WORD = re.compile(r"[^a-z0-9㐀-鿿぀-ヿ가-힯]+")
# Requests wrapped around a name ("mở bài ... cho tôi nghe đi"), marks already removed.
# Only stripped from the ends, so a title containing these words survives.
_LEAD = (
    "hay", "lam on", "xiaozhi", "mo", "bat", "phat", "choi", "play", "cho toi nghe", "cho minh nghe",
    "cho em nghe", "cho anh nghe", "cho chi nghe", "cho con nghe", "nghe", "bai hat", "bai nhac", "ca khuc", "bai",
    "nhac cua", "nhac", "cua ca si", "cua", "ca si", "ten la", "song", "music",
)
_TAIL = (
    "cho toi nghe", "cho minh nghe", "cho em nghe", "cho anh nghe", "cho chi nghe", "cho con nghe", "cho toi",
    "cho minh", "di", "nhe", "nha", "voi", "giup toi", "len",
)
# What is left when no particular song was asked for
_ANYTHING = {
    "", "gi do", "nao do", "bat ky", "bat ki", "ngau nhien", "gi cung duoc", "nao cung duoc", "mot bai", "mot bai hat",
    "mot bai bat ky", "mot bai bat ki", "mot bai nao do", "tat ca", "thu vien", "trong thu vien", "any", "anything",
    "random", "some music", "vui", "nhac vui", "hat",
}


def normalize(text: str) -> str:
    """Lowercase, no marks, words separated by single spaces."""
    text = unicodedata.normalize("NFD", str(text or "").lower().replace("đ", "d"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Recompose so Hangul syllables match again after NFD
    return _NON_WORD.sub(" ", unicodedata.normalize("NFC", text)).strip()


def strip_request(query: str) -> str:
    """The song name inside a spoken request (normalized); "" when none is named."""
    text = normalize(str(query or "")[:MAX_QUERY_CHARS])
    changed = True
    while changed and text:
        changed = False
        for word in _LEAD:
            if text == word or text.startswith(word + " "):
                text, changed = text[len(word):].strip(), True
                break
        for word in _TAIL:
            if text == word or text.endswith(" " + word):
                text, changed = text[: -len(word)].strip(), True
                break
    return text


def wants_anything(query: str) -> bool:
    """True for "bật nhạc đi", "mở bài nào cũng được": no particular song."""
    return normalize(query) in _ANYTHING or strip_request(query) in _ANYTHING


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _score_text(query: str, text: str) -> float:
    if not text:
        return 0.0
    if query == text:
        return 1.0
    q, t = query.split(), text.split()
    # Every asked word is there, in full
    if f" {query} " in f" {text} ":
        return 0.86 + 0.13 * len(query) / len(text)
    if len(q) > 1 and set(q) <= set(t):
        return 0.80 + 0.12 * len(query) / max(len(query), len(text))
    # Misheard words: compare without spaces ("son tung" / "sontung"), and word by word
    whole = _ratio(query.replace(" ", ""), text.replace(" ", ""))
    total = matched = 0.0
    for word in q:
        best = max(_ratio(word, other) for other in t)
        total += len(word)
        matched += len(word) * (best if best >= 0.7 else 0.0)
    words = matched / total if total else 0.0
    # All words roughly there but much shorter than the title: good, not perfect
    cover = min(1.0, len(query) / len(text))
    return max(whole, words * (0.72 + 0.2 * cover) if len(query) >= 4 else 0.0)


def score(query: str, *fields: str) -> float:
    """0..1 for how well a query names something described by the given texts."""
    best = 0.0
    for q in {normalize(query), strip_request(query)}:
        if q:
            best = max([best, *(_score_text(q, normalize(f)) for f in fields if f)])
    return best
