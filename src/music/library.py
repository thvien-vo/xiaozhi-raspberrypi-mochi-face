"""Music files in one directory on the robot: scan, tags, covers, uploads, search.

Everything that names a file from outside (browser, voice) goes through a track id, the
hash of the file's relative path, looked up in the scan. No path from outside is ever
joined to the directory, and symbolic links are ignored, so nothing outside the directory
can be read, played or deleted.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from src.logging import get_logger
from src.music.matching import MATCH_THRESHOLD, normalize, score, strip_request

logger = get_logger()

EXTENSIONS = (".mp3", ".m4a", ".wav", ".ogg", ".flac")
MAX_TRACKS = 5000
MAX_SECONDS = 4 * 3600
MAX_NAME_BYTES = 180
MAX_COVER_BYTES = 8 * 1024 * 1024
MAX_COVER_PIXELS = 40_000_000
COVER_SIZE = 320
RESCAN_AFTER_S = 5.0
MB = 1024 * 1024

_TRACK_ID = re.compile(r"[0-9a-f]{16}\Z")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class LibraryError(ValueError):
    """Message (Vietnamese) that can be shown to the person."""


@dataclass(frozen=True)
class Track:
    id: str
    path: Path
    title: str
    artist: str
    album: str
    duration: float
    size: int
    mtime: float
    has_cover: bool

    @property
    def display(self) -> str:
        return f"{self.title} - {self.artist}" if self.artist else self.title

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": round(self.duration, 1),
            "size": self.size,
            "file": self.path.name,
            "ext": self.path.suffix.lower().lstrip("."),
            "cover": self.has_cover,
        }


def valid_track_id(value) -> bool:
    return isinstance(value, str) and bool(_TRACK_ID.match(value))


def safe_filename(name: str) -> str:
    """File name from an untrusted one: no directories, control or special characters,
    an allowed audio extension, a bounded length."""
    text = unicodedata.normalize("NFC", str(name or ""))
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = re.sub(r'[<>:"|?*]', "", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    stem, ext = os.path.splitext(text)
    ext = ext.lower()
    if ext not in EXTENSIONS:
        raise LibraryError("chỉ nhận file " + ", ".join(e[1:] for e in EXTENSIONS))
    stem = stem.strip(" .-")
    while len(stem.encode("utf-8")) > MAX_NAME_BYTES:
        stem = stem[:-1]
    stem = stem.strip()
    if not stem or stem.lower() in _RESERVED:
        raise LibraryError("tên file không hợp lệ")
    return stem + ext


def magic_matches(head: bytes, ext: str) -> bool:
    """The first bytes look like the format the extension claims."""
    if ext == ".mp3":
        return head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    if ext == ".m4a":
        return head[4:8] == b"ftyp"
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == ".ogg":
        return head[:4] == b"OggS"
    if ext == ".flac":
        return head[:4] == b"fLaC"
    return False


def _open_tagged(path: Path):
    """mutagen object for exactly the format of the extension (no content guessing)."""
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.oggflac import OggFLAC
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    from mutagen.wave import WAVE

    kinds = {
        ".mp3": (MP3,),
        ".m4a": (MP4,),
        ".wav": (WAVE,),
        ".flac": (FLAC,),
        ".ogg": (OggVorbis, OggOpus, OggFLAC),
    }[path.suffix.lower()]
    error: Exception | None = None
    for kind in kinds:
        try:
            return kind(path)
        except Exception as e:  # mutagen raises format-specific errors on bad files
            error = e
    raise LibraryError(f"không đọc được file âm thanh ({type(error).__name__})")


def check_audio(path: Path) -> float:
    """Duration of a valid audio file; LibraryError otherwise."""
    with path.open("rb") as f:
        head = f.read(16)
    if not magic_matches(head, path.suffix.lower()):
        raise LibraryError("nội dung không phải file âm thanh " + path.suffix.lower().lstrip("."))
    info = _open_tagged(path).info
    length = float(getattr(info, "length", 0) or 0)
    if getattr(info, "sketchy", False) or not length > 0.2:
        raise LibraryError("file âm thanh hỏng hoặc trống")
    if length > MAX_SECONDS:
        raise LibraryError("bài dài quá 4 giờ")
    if str(getattr(info, "codec", "")).lower().startswith("drm"):
        raise LibraryError("file m4a có khóa bản quyền (DRM), không phát được")
    return length


def _clean_tag(value) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    text = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:200]


def _tag(tags, *names: str) -> str:
    for name in names:
        try:
            value = tags.get(name)
        except Exception:  # A key the tag format cannot hold
            continue
        text = _clean_tag(getattr(value, "text", value))
        if text:
            return text
    return ""


def _names_from_file(stem: str) -> tuple[str, str]:
    """(title, artist) from "01. Artist - Title"."""
    text = re.sub(r"\s+", " ", stem.replace("_", " ")).strip()
    text = re.sub(r"^\d{1,3}\s*[.\-)]\s+", "", text) or text
    if " - " in text:
        artist, title = (p.strip() for p in text.split(" - ", 1))
        if artist and title:
            return title, artist
    return text, ""


def _read_cover(audio) -> bytes | None:
    tags = getattr(audio, "tags", None)
    pictures = list(getattr(audio, "pictures", None) or [])
    data = pictures[0].data if pictures else None
    if data is None and tags is not None:
        if hasattr(tags, "getall"):  # ID3
            apic = tags.getall("APIC")
            data = apic[0].data if apic else None
        else:
            try:
                covr = tags.get("covr")
                block = tags.get("metadata_block_picture")
            except Exception:
                covr = block = None
            if covr:
                data = bytes(covr[0])
            elif block:
                import base64

                from mutagen.flac import Picture

                data = Picture(base64.b64decode(block[0])).data
    return bytes(data) if data and len(data) <= MAX_COVER_BYTES else None


def read_track(path: Path, track_id: str, stat) -> Track:
    title, artist = _names_from_file(path.stem)
    album, duration, cover = "", 0.0, False
    try:
        audio = _open_tagged(path)
        duration = float(getattr(audio.info, "length", 0) or 0)
        tags = audio.tags if audio.tags is not None else {}
        tag_title = _tag(tags, "TIT2", "title", "\xa9nam")
        if tag_title:
            title, artist = tag_title, ""
        artist = _tag(tags, "TPE1", "artist", "\xa9ART") or artist
        album = _tag(tags, "TALB", "album", "\xa9alb")
        cover = _read_cover(audio) is not None
    except Exception as e:
        logger.debug("No tags for %s: %s", path.name, e)
    return Track(track_id, path, title, artist, album, duration, stat.st_size, stat.st_mtime, cover)


class Upload:
    """One file being received; nothing shows in the library until commit()."""

    def __init__(self, library: MusicLibrary, name: str) -> None:
        self._library = library
        self.name = safe_filename(name)
        self.size = 0
        self.limit = library.upload_allowance()
        self.too_big = f"file quá lớn (tối đa {self.limit // MB} MB)"
        library.work_dir.mkdir(parents=True, exist_ok=True)
        # Keeps the real extension so the checks open it as that format
        self._path = library.work_dir / f"upload-{os.urandom(6).hex()}{Path(self.name).suffix}"
        self._file = self._path.open("xb")

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.limit:
            raise LibraryError(self.too_big)
        self._file.write(chunk)

    def commit(self) -> Track:
        self._file.close()
        try:
            check_audio(self._path)
            return self._library._adopt(self._path, self.name)
        except BaseException:
            self.abort()
            raise

    def abort(self) -> None:
        try:
            self._file.close()
            self._path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Cannot remove partial upload: %s", e)


class MusicLibrary:
    """Thread-safe; used from the app loop, the web thread and worker threads."""

    def __init__(self, root: Path, max_upload_mb: int = 100, max_total_mb: int = 8192, min_free_mb: int = 500) -> None:
        self.root = Path(root)
        self.work_dir = self.root / ".work"
        self.max_upload = max(1, int(max_upload_mb)) * MB
        self.max_total = max(1, int(max_total_mb)) * MB
        self.min_free = max(0, int(min_free_mb)) * MB
        self._lock = threading.RLock()
        self._tracks: dict[str, Track] = {}
        self._scanned = 0.0
        self._covers: OrderedDict[tuple, bytes | None] = OrderedDict()
        self._beats: dict | None = None
        self.revision = 0

    # ----- Scan -----

    def _walk(self):
        for folder, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(folder, d)))
            for name in sorted(files):
                if not name.startswith(".") and os.path.splitext(name)[1].lower() in EXTENSIONS:
                    yield Path(folder) / name

    @staticmethod
    def _id_for(relative: str) -> str:
        return hashlib.sha1(relative.encode("utf-8", "surrogateescape")).hexdigest()[:16]

    def scan(self, force: bool = False) -> None:
        with self._lock:
            if not force and self._scanned and time.monotonic() - self._scanned < RESCAN_AFTER_S:
                return
            self.root.mkdir(parents=True, exist_ok=True)
            found: dict[str, Track] = {}
            for path in self._walk():
                if len(found) >= MAX_TRACKS:
                    logger.warning("Music library: only the first %d files are used", MAX_TRACKS)
                    break
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    stat = path.stat()
                    track_id = self._id_for(path.relative_to(self.root).as_posix())
                    known = self._tracks.get(track_id)
                    if known and known.size == stat.st_size and known.mtime == stat.st_mtime:
                        found[track_id] = known
                    else:
                        found[track_id] = read_track(path, track_id, stat)
                except (OSError, UnicodeError) as e:
                    logger.warning("Music library: skipping a file: %s", e)
            if found != self._tracks:
                self._tracks = found
                self.revision += 1
                logger.info("Music library: %d tracks in %s", len(found), self.root)
            self._scanned = time.monotonic()

    def tracks(self) -> list[Track]:
        self.scan()
        with self._lock:
            return sorted(self._tracks.values(), key=lambda t: (normalize(t.title), normalize(t.artist), t.id))

    def get(self, track_id: str) -> Track | None:
        """The track, if its file is still there."""
        if not valid_track_id(track_id):
            return None
        track = self._tracks.get(track_id)
        if track is None:
            self.scan()
            track = self._tracks.get(track_id)
        if track is None or track.path.is_symlink() or not track.path.is_file():
            return None
        return track

    def peek(self, track_id: str) -> Track | None:
        """Last known track, without touching the disk (for frequent snapshots)."""
        return self._tracks.get(track_id)

    # ----- Search -----

    def search(self, query: str, limit: int = 20, threshold: float = MATCH_THRESHOLD) -> list[tuple[float, Track]]:
        results = []
        for t in self.tracks():
            value = max(
                score(query, t.title, t.path.stem.replace("_", " ")),
                0.99 * score(query, f"{t.title} {t.artist}", f"{t.artist} {t.title}") if t.artist else 0.0,
                0.95 * score(query, t.artist, t.album),
            )
            if value >= threshold:
                results.append((round(value, 3), t))
        results.sort(key=lambda r: (-r[0], normalize(r[1].title)))
        return results[:limit]

    def by_artist(self, query: str) -> list[Track]:
        """Every track of the artist a query names ("nhạc của Sơn Tùng")."""
        if not strip_request(query):
            return []
        return [t for t in self.tracks() if t.artist and score(query, t.artist) >= 0.8]

    # ----- Changes -----

    def usage(self) -> int:
        with self._lock:
            return sum(t.size for t in self._tracks.values())

    def upload_allowance(self) -> int:
        """Bytes the next upload may have; LibraryError when there is no room."""
        self.scan()
        if len(self._tracks) >= MAX_TRACKS:
            raise LibraryError("thư viện nhạc đã đầy")
        room = min(self.max_total - self.usage(), shutil.disk_usage(self.root).free - self.min_free)
        if room <= 0:
            raise LibraryError("robot hết chỗ chứa nhạc, hãy xóa bớt bài")
        return min(self.max_upload, room)

    def begin_upload(self, name: str) -> Upload:
        return Upload(self, name)

    def _adopt(self, temp: Path, name: str) -> Track:
        with self._lock:
            target = self.root / name
            n = 2
            while target.exists() or target.is_symlink():
                target = self.root / f"{Path(name).stem} ({n}){Path(name).suffix}"
                n += 1
            temp.replace(target)
            self.scan(force=True)
            track = self._tracks.get(self._id_for(target.name))
        if track is None:
            raise LibraryError("không lưu được file")
        logger.info("Music library: added %s (%d bytes)", target.name, track.size)
        return track

    def delete(self, track_id: str) -> Track | None:
        track = self.get(track_id)
        if track is None:
            return None
        track.path.unlink()
        logger.info("Music library: deleted %s", track.path.name)
        self.scan(force=True)
        return track

    def clean_work_dir(self) -> None:
        """Remove partial uploads left by a restart."""
        if self.work_dir.is_dir():
            for path in self.work_dir.glob("upload-*"):
                try:
                    path.unlink()
                except OSError as e:
                    logger.debug("Cannot remove %s: %s", path, e)

    # ----- Covers -----

    def cover(self, track_id: str) -> bytes | None:
        """Embedded picture as a small JPEG."""
        track = self.get(track_id)
        if track is None or not track.has_cover:
            return None
        key = (track.id, track.size, track.mtime)
        with self._lock:
            if key in self._covers:
                self._covers.move_to_end(key)
                return self._covers[key]
        data = None
        try:
            from PIL import Image

            raw = _read_cover(_open_tagged(track.path))
            if raw:
                with Image.open(io.BytesIO(raw), formats=("JPEG", "PNG", "WEBP", "GIF", "BMP")) as image:
                    if image.width * image.height > MAX_COVER_PIXELS:
                        raise ValueError("cover too large")
                    image.draft("RGB", (COVER_SIZE * 2, COVER_SIZE * 2))
                    small = image.convert("RGB")
                    small.thumbnail((COVER_SIZE, COVER_SIZE))
                    out = io.BytesIO()
                    small.save(out, "JPEG", quality=82)
                    data = out.getvalue()
        except Exception as e:
            logger.debug("Cannot read the cover of %s: %s", track.path.name, e)
        with self._lock:
            self._covers[key] = data
            while len(self._covers) > 64:
                self._covers.popitem(last=False)
        return data

    # ----- Beat cache -----

    def _beats_file(self) -> Path:
        return self.work_dir / "beats.json"

    @staticmethod
    def _beat_key(track: Track) -> str:
        return f"{track.id}:{track.size}:{int(track.mtime)}"

    def load_beat(self, track: Track) -> dict | None:
        with self._lock:
            if self._beats is None:
                try:
                    data = json.loads(self._beats_file().read_text(encoding="utf-8"))
                    self._beats = data if isinstance(data, dict) else {}
                except (OSError, ValueError):
                    self._beats = {}
            value = self._beats.get(self._beat_key(track))
            return value if isinstance(value, dict) else None

    def save_beat(self, track: Track, beat: dict) -> None:
        with self._lock:
            self.load_beat(track)
            self._beats[self._beat_key(track)] = beat
            while len(self._beats) > MAX_TRACKS:
                self._beats.pop(next(iter(self._beats)))
            try:
                self.work_dir.mkdir(parents=True, exist_ok=True)
                tmp = self._beats_file().with_suffix(".tmp")
                tmp.write_text(json.dumps(self._beats), encoding="utf-8")
                tmp.replace(self._beats_file())
            except OSError as e:
                logger.debug("Cannot save the beat cache: %s", e)
