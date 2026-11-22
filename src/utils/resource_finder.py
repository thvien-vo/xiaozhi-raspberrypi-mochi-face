"""
Unified resource path resolver - works in both development and packaged environments

Default user data directories (platformdirs):
- Windows: C:/Users/xxx/AppData/Local/{app_name}/
- macOS:   ~/Library/Application Support/{app_name}/
- Linux:   ~/.local/share/{app_name}/

Default subdirectories: config/ cache/ logs/ keywords/ mcp_plugins/

Overridable (recommended to keep config directory at default; others can be migrated):
- Environment variables: XIAOZHI_DATA_DIR / XIAOZHI_CACHE_DIR / XIAOZHI_LOG_DIR /
  XIAOZHI_MUSIC_CACHE_DIR / XIAOZHI_KEYWORDS_DIR
- Configuration PATHS.* (see apply_path_overrides_from_config; migrates old directories after changes)

Core API:
- get_app_root() / get_app_name()
- get_user_data_dir() / get_user_cache_dir() / get_user_log_dir()
- get_music_cache_dir() / get_keywords_dir()
- apply_path_overrides_from_config() / migrate_directory()
"""

from __future__ import annotations

import os
import platform as plat
import shutil
import sys
from functools import lru_cache
from pathlib import Path

import platformdirs

from src.constants.system import SystemConstants

# Environment variable names
ENV_DATA_DIR = "XIAOZHI_DATA_DIR"
ENV_CACHE_DIR = "XIAOZHI_CACHE_DIR"
ENV_LOG_DIR = "XIAOZHI_LOG_DIR"
ENV_MUSIC_CACHE_DIR = "XIAOZHI_MUSIC_CACHE_DIR"
ENV_KEYWORDS_DIR = "XIAOZHI_KEYWORDS_DIR"

# Runtime overrides (applied after config is loaded; env variables take precedence over these)
_override_cache: Path | None = None
_override_log: Path | None = None
_override_music: Path | None = None
_override_keywords: Path | None = None


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


@lru_cache(maxsize=1)
def get_app_root() -> Path:
    """Application root directory (for both development and packaged runs)

    - In development: project root directory
    - When packaged: _MEIPASS (PyInstaller onedir)
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    # src/utils/resource_finder.py -> 3 levels up
    return Path(__file__).resolve().parent.parent.parent


def get_app_name() -> str:
    """Get application name (constant value)

    Retrieved from SystemConstants.APP_NAME to ensure consistency.
    """
    return SystemConstants.APP_NAME


@lru_cache(maxsize=1)
def get_user_data_dir() -> Path:
    """User data directory (writable: contains config/ by default)

    Prefers environment variable XIAOZHI_DATA_DIR, otherwise uses platformdirs.
    Keeping configuration files under config/ within this directory ensures PATHS overrides can always be read.
    """
    env = _env_path(ENV_DATA_DIR)
    if env is not None:
        p = env
    else:
        p = Path(platformdirs.user_data_dir(get_app_name()))
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_user_cache_dir() -> Path:
    """Cache directory: XIAOZHI_CACHE_DIR > runtime override > {data}/cache."""
    env = _env_path(ENV_CACHE_DIR)
    if env is not None:
        p = env
    elif _override_cache is not None:
        p = _override_cache
    else:
        p = get_user_data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_user_log_dir() -> Path:
    """Log directory: XIAOZHI_LOG_DIR > runtime override > {data}/logs."""
    env = _env_path(ENV_LOG_DIR)
    if env is not None:
        p = env
    elif _override_log is not None:
        p = _override_log
    else:
        p = get_user_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_log_dir() -> Path:
    """Log directory (under user data directory, writable after packaging)."""
    return get_user_log_dir()


def get_music_cache_dir() -> Path:
    """Music cache directory: XIAOZHI_MUSIC_CACHE_DIR > override > {cache}/music."""
    env = _env_path(ENV_MUSIC_CACHE_DIR)
    if env is not None:
        p = env
    elif _override_music is not None:
        p = _override_music
    else:
        p = get_user_cache_dir() / "music"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_music_library_dir() -> Path:
    """The user's own music files (uploads from the web panel): {user_data}/music."""
    return _env_path("XIAOZHI_MUSIC_DIR") or get_user_data_dir() / "music"


def get_keywords_dir() -> Path:
    """Wake word keywords directory: XIAOZHI_KEYWORDS_DIR > override > {data}/keywords."""
    env = _env_path(ENV_KEYWORDS_DIR)
    if env is not None:
        p = env
    elif _override_keywords is not None:
        p = _override_keywords
    else:
        p = get_user_data_dir() / "keywords"
    p.mkdir(parents=True, exist_ok=True)
    return p


def clear_path_caches() -> None:
    """Clear lru caches such as get_user_data_dir (for testing or after DATA_DIR changes)."""
    get_user_data_dir.cache_clear()
    get_app_root.cache_clear()


def migrate_directory(
    src: Path,
    dst: Path,
    *,
    copy: bool = True,
    skip_dir_names: set[str] | frozenset[str] | None = None,
) -> dict:
    """Copy contents from an old directory to a new directory (does not delete source by default for safety).

    skip_dir_names: set of top-level directory names (relative to src) whose entire subtrees are skipped
        (e.g., skipping music/ during cache migration when music is configured separately).

    Returns:
        {ok, src, dst, files_copied, error?}
    """
    src = src.expanduser().resolve()
    dst = dst.expanduser().resolve()
    skip = set(skip_dir_names or ())
    result: dict = {
        "ok": False,
        "src": str(src),
        "dst": str(dst),
        "files_copied": 0,
    }
    if src == dst:
        result["ok"] = True
        result["skipped"] = "same_path"
        return result
    if not src.is_dir():
        result["ok"] = True
        result["skipped"] = "src_missing"
        return result
    try:
        dst.mkdir(parents=True, exist_ok=True)
        count = 0
        for root, dirs, files in os.walk(src):
            rel = Path(root).relative_to(src)
            # Top-level directory filtering: os.walk allows modifying dirs in-place
            if rel == Path("."):
                dirs[:] = [d for d in dirs if d not in skip]
            else:
                # Defensive check if the first segment of relative path is in skip
                if rel.parts and rel.parts[0] in skip:
                    dirs[:] = []
                    continue
            target_root = dst / rel
            target_root.mkdir(parents=True, exist_ok=True)
            for name in files:
                s = Path(root) / name
                d = target_root / name
                if d.exists():
                    continue  # Do not overwrite existing files
                if copy:
                    shutil.copy2(s, d)
                else:
                    shutil.move(str(s), str(d))
                count += 1
        result["ok"] = True
        result["files_copied"] = count
    except Exception as e:
        result["error"] = str(e)
    return result


def apply_path_overrides_from_config(
    config,
    *,
    migrate: bool = True,
) -> list[dict]:
    """Read PATHS from ConfigManager, set runtime overrides, and optionally migrate old directories.

    Environment variables always take precedence over configuration. The config directory is not migrated here.

    Returns:
        List of migration results (for UI/logging)
    """
    global _override_cache, _override_log, _override_music, _override_keywords

    migrations: list[dict] = []

    def _cfg(key: str) -> str | None:
        try:
            v = config.get_config(key, None)
        except Exception:
            return None
        if v is None or str(v).strip() == "":
            return None
        return str(v).strip()

    # Current effective paths (before applying overrides) serve as migration sources
    old_cache = get_user_cache_dir()
    old_log = get_user_log_dir()
    old_music = get_music_cache_dir()
    old_kw = get_keywords_dir()

    cache_s = _cfg("PATHS.CACHE_DIR")
    log_s = _cfg("PATHS.LOG_DIR")
    music_s = _cfg("PATHS.MUSIC_CACHE_DIR")
    kw_s = _cfg("PATHS.KEYWORDS_DIR")

    # Apply config overrides only when environment variables are not set
    if _env_path(ENV_CACHE_DIR) is None:
        _override_cache = Path(cache_s).expanduser().resolve() if cache_s else None
    if _env_path(ENV_LOG_DIR) is None:
        _override_log = Path(log_s).expanduser().resolve() if log_s else None
    if _env_path(ENV_MUSIC_CACHE_DIR) is None:
        _override_music = Path(music_s).expanduser().resolve() if music_s else None
    if _env_path(ENV_KEYWORDS_DIR) is None:
        _override_keywords = Path(kw_s).expanduser().resolve() if kw_s else None

    if not migrate:
        return migrations

    new_cache = get_user_cache_dir()
    new_log = get_user_log_dir()
    new_music = get_music_cache_dir()
    new_kw = get_keywords_dir()

    # During cache migration: if music points to a separate directory (or is about to be migrated separately), skip cache/music subtree
    cache_skip: set[str] = set()
    try:
        music_under_old_cache = old_music.resolve() == (old_cache / "music").resolve()
        music_stays_default_under_new = new_music.resolve() == (
            new_cache / "music"
        ).resolve()
        # Old music was under cache, but new music is not "new_cache/music" -> migrated separately by the music entry, skip in cache migration
        if music_under_old_cache and not music_stays_default_under_new:
            cache_skip.add("music")
        # Even if music remains defaulted under cache: migrating old->new cache copies music first, which is fine;
        # only skip when music path is explicitly independent
    except Exception:
        pass

    if old_cache.resolve() != new_cache.resolve():
        r = migrate_directory(
            old_cache, new_cache, copy=True, skip_dir_names=cache_skip or None
        )
        r["kind"] = "cache"
        migrations.append(r)
        _log_migration("cache", old_cache, new_cache, r)

    if old_log.resolve() != new_log.resolve():
        r = migrate_directory(old_log, new_log, copy=True)
        r["kind"] = "logs"
        migrations.append(r)
        _log_migration("logs", old_log, new_log, r)

    if old_music.resolve() != new_music.resolve():
        r = migrate_directory(old_music, new_music, copy=True)
        r["kind"] = "music"
        migrations.append(r)
        _log_migration("music", old_music, new_music, r)

    if old_kw.resolve() != new_kw.resolve():
        r = migrate_directory(old_kw, new_kw, copy=True)
        r["kind"] = "keywords"
        migrations.append(r)
        _log_migration("keywords", old_kw, new_kw, r)

    return migrations


def _log_migration(kind: str, src: Path, dst: Path, r: dict) -> None:
    try:
        from src.logging import get_logger

        get_logger().info(
            "Path migration %s: %s -> %s (copied=%s, ok=%s)",
            kind,
            src,
            dst,
            r.get("files_copied"),
            r.get("ok"),
        )
    except Exception:
        pass


def set_path_overrides(
    *,
    cache: Path | str | None | object = ...,
    log: Path | str | None | object = ...,
    music: Path | str | None | object = ...,
    keywords: Path | str | None | object = ...,
) -> None:
    """Set path overrides programmatically or in tests (environment variables still take precedence).

    - Omitting a parameter: leaves that override unchanged
    - Passing a path string/Path: sets the override
    - Explicitly passing None: clears that override (restoring default / env-only)
    """
    global _override_cache, _override_log, _override_music, _override_keywords

    def _p(v):
        if v is None or str(v).strip() == "":
            return None
        return Path(v).expanduser().resolve()

    if cache is not ...:
        _override_cache = _p(cache)
    if log is not ...:
        _override_log = _p(log)
    if music is not ...:
        _override_music = _p(music)
    if keywords is not ...:
        _override_keywords = _p(keywords)


def clear_path_overrides(
    *,
    cache: bool = False,
    log: bool = False,
    music: bool = False,
    keywords: bool = False,
    all: bool = False,
) -> None:
    """Clear runtime path overrides (for testing or restoring defaults)."""
    global _override_cache, _override_log, _override_music, _override_keywords
    if all or cache:
        _override_cache = None
    if all or log:
        _override_log = None
    if all or music:
        _override_music = None
    if all or keywords:
        _override_keywords = None


@lru_cache(maxsize=1)
def get_platform_info() -> tuple[str, str]:
    """Get platform and architecture identifiers.

    Returns:
        (platform_dir, arch_dir) e.g. ("mac", "arm64") / ("win", "x64")
    """
    machine = plat.machine().lower()
    # Windows ARM commonly reports ARM64; in some environments only environment variables are reliable
    env_arch = (os.environ.get("PROCESSOR_ARCHITECTURE") or "").lower()
    env_arch_w6432 = (os.environ.get("PROCESSOR_ARCHITEW6432") or "").lower()
    is_arm = any(
        token in s
        for s in (machine, env_arch, env_arch_w6432)
        for token in ("arm", "aarch64")
    )

    if sys.platform == "win32":
        return "win", "arm64" if is_arm else "x64"
    elif sys.platform == "darwin":
        return "mac", "arm64" if is_arm else "x64"
    else:
        return "linux", "arm64" if is_arm else "x64"


def get_lib_path(lib_name: str) -> Path | None:
    """Get dynamic library file path.

    Args:
        lib_name: Library name, e.g. "libopus", "webrtc_apm"

    Returns:
        Full path to the library file, or None if not found
    """
    plat_dir, arch = get_platform_info()
    root = get_app_root() / "libs" / lib_name

    # Platform directory aliases (mac/macos, win/windows)
    plat_aliases = {
        "mac": ["mac", "macos"],
        "win": ["win", "windows"],
        "linux": ["linux"],
    }

    # Extensions and preferred filenames (avoids picking the wrong file when multiple candidates exist)
    ext_map = {"mac": ".dylib", "win": ".dll", "linux": ".so"}
    ext = ext_map.get(plat_dir, ".so")
    preferred_names = {
        "libopus": {
            "mac": ["libopus.dylib", "libopus.0.dylib"],
            "linux": ["libopus.so", "libopus.so.0"],
            "win": ["opus.dll", "libopus.dll", "libopus-0.dll"],
        },
        "webrtc_apm": {
            "mac": ["libwebrtc_apm.dylib", "webrtc_apm.dylib"],
            "linux": ["libwebrtc_apm.so", "webrtc_apm.so"],
            "win": ["webrtc_apm.dll", "libwebrtc_apm.dll"],
        },
    }
    prefer = preferred_names.get(lib_name, {}).get(plat_dir, [])

    for plat_name in plat_aliases.get(plat_dir, [plat_dir]):
        lib_dir = root / plat_name / arch
        if not lib_dir.is_dir():
            continue

        # 1) Exact preferred filename match
        for pname in prefer:
            cand = lib_dir / pname
            if cand.is_file():
                return cand

        # 2) Fallback: match by extension, preferring shorter names without extra suffixes
        candidates: list[Path] = []
        for f in lib_dir.iterdir():
            if not f.is_file():
                continue
            # Skip documentation/metadata files
            if f.suffix.lower() in {".txt", ".md", ".json"}:
                continue
            if f.suffix == ext or ext in f.name:
                candidates.append(f)
        if not candidates:
            continue
        candidates.sort(key=lambda p: (len(p.name), p.name))
        return candidates[0]

    return None


def get_lib_dir(lib_name: str) -> Path | None:
    """
    Get the directory containing a dynamic library.
    """
    lib_path = get_lib_path(lib_name)
    return lib_path.parent if lib_path else None


def get_ffmpeg_path() -> str:
    """Get ffmpeg executable path.

    Search order: bundled libs/ffmpeg/ -> system PATH -> imageio-ffmpeg wheel (the Pi has
    no system ffmpeg, and installing one needs root)
    """
    import shutil

    plat_dir, arch = get_platform_info()
    ext = ".exe" if sys.platform == "win32" else ""
    bundled = get_app_root() / "libs" / "ffmpeg" / plat_dir / arch / f"ffmpeg{ext}"
    if bundled.exists():
        return str(bundled)
    return shutil.which("ffmpeg") or _wheel_ffmpeg_path() or "ffmpeg"


@lru_cache(maxsize=1)
def _wheel_ffmpeg_path() -> str | None:
    """ffmpeg binary shipped inside the imageio-ffmpeg wheel, if installed."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # Not installed, or no binary for this platform
        return None


def get_ffprobe_path() -> str:
    """Get ffprobe executable path.

    Search order: bundled libs/ffmpeg/ -> system PATH
    """
    import shutil

    plat_dir, arch = get_platform_info()
    ext = ".exe" if sys.platform == "win32" else ""
    bundled = get_app_root() / "libs" / "ffmpeg" / plat_dir / arch / f"ffprobe{ext}"
    if bundled.exists():
        return str(bundled)
    return shutil.which("ffprobe") or "ffprobe"


def get_models_dir() -> Path:
    """
    Models directory (read-only, inside installation directory).
    """
    return get_app_root() / "models"


def get_assets_dir() -> Path:
    """
    Assets directory (read-only, inside installation directory).
    """
    return get_app_root() / "assets"


def get_config_dir() -> Path:
    """
    Configuration directory (bundled with application, read-only)
    """
    return get_app_root() / "config"


def get_user_keywords_path(lang: str) -> Path:
    """Get keywords file path, always using the user directory

    Automatically copies the default file from the installation directory to the user directory on first run.

    Args:
        lang: Language code, e.g. "zh" or "en"

    Returns:
        Path to the keywords file under the user directory
    """
    import shutil

    user_keywords_dir = get_keywords_dir()
    user_keywords = user_keywords_dir / f"{lang}_keywords.txt"

    if not user_keywords.exists():
        # Copy default file from installation directory
        default_keywords = get_app_root() / "models" / lang / "keywords.txt"
        if default_keywords.exists():
            user_keywords_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(default_keywords, user_keywords)

    return user_keywords
