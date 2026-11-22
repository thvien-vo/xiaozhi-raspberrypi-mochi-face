"""Eye color of the robot face: palette, validation, modes and the shared runtime state.

No Qt here: the QML face (through FaceModel), the web panel and the voice tool all use
the same EyeColorState, so a change from any of them shows everywhere. Everything that
comes from outside (web page, voice, config file) goes through parse_* and comes out
as a valid "#rrggbb" string or a known mode name, or raises ValueError.

Modes:
  fixed    one color (FACE.EYE_COLOR)
  rainbow  the hue turns slowly and continuously
  emotion  each expression has its own color (EMOTION_COLORS), the base color otherwise
"""

from __future__ import annotations

import colorsys
import re
import threading
import unicodedata
from collections.abc import Callable

DEFAULT_COLOR = "#ffffff"  # What the face always drew
MODE_FIXED, MODE_RAINBOW, MODE_EMOTION = "fixed", "rainbow", "emotion"
MODES = (MODE_FIXED, MODE_RAINBOW, MODE_EMOTION)
RAINBOW_PERIOD_S = 12.0  # One full turn of the hue
RAINBOW_SATURATION = 0.7

# Eyes are drawn on pure black: below this relative luminance they get hard to see
# (contrast ratio against black of about 3.5:1)
MIN_LUMINANCE = 0.125

# (key, Vietnamese label, hex) in the order the page shows them
PALETTE: tuple[tuple[str, str, str], ...] = (
    ("white", "Trắng", "#ffffff"),
    ("blue", "Xanh dương", "#3d9bff"),
    ("cyan", "Xanh ngọc", "#33e0ff"),
    ("green", "Xanh lá", "#3dff7a"),
    ("purple", "Tím", "#b46bff"),
    ("pink", "Hồng", "#ff6fb5"),
    ("orange", "Cam", "#ff9a3c"),
    ("red", "Đỏ", "#ff4d4d"),
    ("yellow", "Vàng", "#ffd93d"),
)
PALETTE_HEX = {key: hex_ for key, _label, hex_ in PALETTE}

# Spoken / typed names (without diacritics, lower case) -> palette key
COLOR_NAMES = {
    "trang": "white", "white": "white", "mac dinh": "white", "binh thuong": "white",
    "xanh duong": "blue", "xanh da troi": "blue", "xanh nuoc bien": "blue", "xanh lam": "blue",
    "blue": "blue", "xanh": "blue",
    "xanh ngoc": "cyan", "xanh lo": "cyan", "lam ngoc": "cyan", "cyan": "cyan", "ngoc": "cyan",
    "xanh la": "green", "xanh la cay": "green", "xanh luc": "green", "luc": "green", "green": "green",
    "tim": "purple", "mau tim": "purple", "purple": "purple", "violet": "purple",
    "hong": "pink", "pink": "pink",
    "cam": "orange", "orange": "orange",
    "do": "red", "red": "red",
    "vang": "yellow", "yellow": "yellow",
}
MODE_NAMES = {
    "cau vong": MODE_RAINBOW, "rainbow": MODE_RAINBOW, "bay mau": MODE_RAINBOW,
    "doi mau": MODE_RAINBOW, "nhieu mau": MODE_RAINBOW,
    "theo cam xuc": MODE_EMOTION, "cam xuc": MODE_EMOTION, "emotion": MODE_EMOTION,
    "theo bieu cam": MODE_EMOTION, "bieu cam": MODE_EMOTION,
    "co dinh": MODE_FIXED, "fixed": MODE_FIXED, "mot mau": MODE_FIXED,
}

# Emotion mode: expression (the preset the face shows) -> color
EMOTION_COLORS = {
    "neutral": PALETTE_HEX["white"],
    "happy": PALETTE_HEX["yellow"], "laughing": PALETTE_HEX["yellow"], "funny": PALETTE_HEX["yellow"],
    "delicious": PALETTE_HEX["orange"],
    "sad": PALETTE_HEX["blue"], "crying": PALETTE_HEX["blue"],
    "angry": PALETTE_HEX["red"],
    "loving": PALETTE_HEX["pink"], "kissy": PALETTE_HEX["pink"], "embarrassed": "#ff9ec7",
    "surprised": PALETTE_HEX["orange"], "shocked": "#ff7a2e",
    "thinking": PALETTE_HEX["purple"], "confused": PALETTE_HEX["purple"],
    "winking": PALETTE_HEX["green"], "silly": PALETTE_HEX["green"],
    "cool": PALETTE_HEX["cyan"], "confident": PALETTE_HEX["cyan"],
    "relaxed": "#8cffb0", "sleepy": "#8fb8ff", "asleep": "#6f8fd6",
}

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _plain(text: str) -> str:
    """Lower case, no Vietnamese diacritics, single spaces ("Xanh Dương" -> "xanh duong")."""
    text = unicodedata.normalize("NFD", str(text).replace("đ", "d").replace("Đ", "D"))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn").lower()
    text = re.sub(r"[^a-z0-9#]+", " ", text)
    return " ".join(text.split())


def normalize_hex(value: str) -> str:
    """'#ABC' / 'aabbcc' / '#AABBCC' -> '#aabbcc'; anything else raises ValueError."""
    if not isinstance(value, str):
        raise ValueError("mã màu phải là chuỗi")
    m = _HEX_RE.match(value.strip())
    if not m:
        raise ValueError(f"mã màu không hợp lệ: {value!r}")
    digits = m.group(1).lower()
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    digits = normalize_hex(value)[1:]
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in (r, g, b))


def relative_luminance(value: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in hex_to_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ensure_visible(value: str) -> str:
    """Lighten a color toward white until it stands out on the black screen."""
    color = normalize_hex(value)
    if relative_luminance(color) >= MIN_LUMINANCE:
        return color
    r, g, b = hex_to_rgb(color)
    lo, hi = 0.0, 1.0  # Fraction of white mixed in; luminance grows with it
    for _ in range(20):
        mid = (lo + hi) / 2
        mixed = rgb_to_hex(r + (255 - r) * mid, g + (255 - g) * mid, b + (255 - b) * mid)
        if relative_luminance(mixed) >= MIN_LUMINANCE:
            hi = mid
        else:
            lo = mid
    return rgb_to_hex(r + (255 - r) * hi, g + (255 - g) * hi, b + (255 - b) * hi)


def rainbow_color(t: float) -> str:
    """The rainbow mode's color at time t (seconds); the page uses the same formula."""
    hue = (t / RAINBOW_PERIOD_S) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, RAINBOW_SATURATION, 1.0)
    return rgb_to_hex(r * 255, g * 255, b * 255)


def parse_mode(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("chế độ màu phải là chuỗi")
    plain = _plain(value)
    if plain in MODES:
        return plain
    if plain in MODE_NAMES:
        return MODE_NAMES[plain]
    raise ValueError(f"chế độ màu không có: {value!r} (fixed, rainbow, emotion)")


def parse_color(value: str) -> str:
    """A hex code or a color name (Vietnamese or English) -> visible '#rrggbb'."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("thiếu màu")
    try:
        return ensure_visible(normalize_hex(value))
    except ValueError:
        pass
    plain = _plain(value)
    for prefix in ("mau ", "sang mau ", "sang "):
        if plain.startswith(prefix) and plain[len(prefix):] in COLOR_NAMES:
            plain = plain[len(prefix):]
    key = COLOR_NAMES.get(plain) or (plain if plain in PALETTE_HEX else None)
    if key is None:
        raise ValueError(f"không biết màu: {value!r}")
    return PALETTE_HEX[key]


def parse_request(value: str) -> tuple[str | None, str]:
    """Free text from the voice tool: a color, or a mode name -> (color or None, mode)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("thiếu màu")
    plain = _plain(value)
    for prefix in ("mau mat ", "mat ", "che do ", "mau "):
        if plain.startswith(prefix):
            plain = plain[len(prefix):]
    for candidate in (plain, plain.replace("mau ", "")):
        if candidate in MODE_NAMES or candidate in MODES:
            return None, parse_mode(candidate)
    if plain.startswith(("tat ", "bo ", "dung ")) or plain in ("tat", "giu mot mau", "giu mau"):
        return None, MODE_FIXED  # "tắt cầu vồng", "bỏ màu theo cảm xúc": keep the color
    if plain in ("mac dinh", "binh thuong", "nhu cu", "ban dau"):
        return DEFAULT_COLOR, MODE_FIXED
    return parse_color(value), MODE_FIXED


def color_label(color: str) -> str:
    for _key, label, hex_ in PALETTE:
        if hex_ == color:
            return label
    return color


def active_expression(emotion: str, visual_state: str = "idle", activity: str = "awake") -> str:
    """The preset the face draws (mirrors activeName() in qml/face/FacePresets.js)."""
    if activity == "asleep":
        return "asleep"
    if activity == "sleepy":
        return "sleepy"
    if emotion not in EMOTION_COLORS:  # Same keys as EMOTIONS in FacePresets.js
        return "neutral"
    if emotion == "neutral" and visual_state == "thinking":
        return "thinking"
    return emotion


def effective_color(color: str, mode: str, expression: str, t: float) -> str:
    """The color the eyes show right now (mirrors RobotFace.qml)."""
    if mode == MODE_RAINBOW:
        return rainbow_color(t)
    if mode == MODE_EMOTION:
        return EMOTION_COLORS.get(expression, color)
    return color


class EyeColorState:
    """Current eye color and mode; thread-safe, with change listeners.

    save(path, value) persists a setting (FACE.EYE_COLOR, FACE.EYE_COLOR_MODE); it is
    called on the thread that made the change, so it must be safe there (the app passes a
    function that hops onto its own loop).
    """

    def __init__(self, color: str = DEFAULT_COLOR, mode: str = MODE_FIXED,
                 save: Callable[[str, object], None] | None = None) -> None:
        self._color = ensure_visible(color)
        self._mode = mode if mode in MODES else MODE_FIXED
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str, str], None]] = []
        self._saved = (self._color, self._mode)  # What the config file holds
        self.save = save

    @classmethod
    def from_config(cls, config, save: Callable[[str, object], None] | None = None) -> EyeColorState:
        """Bad values in the config fall back to the defaults (they must not break the face)."""
        try:
            color = ensure_visible(normalize_hex(str(config.get_config("FACE.EYE_COLOR", DEFAULT_COLOR))))
        except ValueError:
            color = DEFAULT_COLOR
        try:
            mode = parse_mode(str(config.get_config("FACE.EYE_COLOR_MODE", MODE_FIXED)))
        except ValueError:
            mode = MODE_FIXED
        return cls(color, mode, save)

    @property
    def color(self) -> str:
        return self._color

    @property
    def mode(self) -> str:
        return self._mode

    def add_listener(self, callback: Callable[[str, str], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str, str], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def set(self, color: str | None = None, mode: str | None = None, persist: bool = True) -> tuple[str, str]:
        """Change color and/or mode (validated); returns (color, mode)."""
        new_color = self._color if color is None else ensure_visible(normalize_hex(color))
        new_mode = self._mode if mode is None else parse_mode(mode)
        with self._lock:
            changed_color = new_color != self._color
            changed_mode = new_mode != self._mode
            self._color, self._mode = new_color, new_mode
            saved_color, saved_mode = self._saved
            if persist and self.save is not None:
                self._saved = (new_color, new_mode)
        # A live preview (persist=False) followed by the final value still gets saved
        if persist and self.save is not None:
            if new_color != saved_color:
                self.save("FACE.EYE_COLOR", new_color)
            if new_mode != saved_mode:
                self.save("FACE.EYE_COLOR_MODE", new_mode)
        if changed_color or changed_mode:
            for callback in list(self._listeners):
                try:
                    callback(new_color, new_mode)
                except Exception:
                    pass
        return new_color, new_mode

    def snapshot(self) -> dict:
        return {
            "color": self._color,
            "mode": self._mode,
            "label": color_label(self._color),
        }


_shared: EyeColorState | None = None
_shared_lock = threading.Lock()


def get_eye_colors(config=None) -> EyeColorState:
    """The process-wide state, created from the config on first use."""
    global _shared
    with _shared_lock:
        if _shared is None:
            if config is None:
                from src.utils.config_manager import get_config

                try:
                    config = get_config()
                except RuntimeError:
                    config = None
            _shared = EyeColorState.from_config(config) if config is not None else EyeColorState()
        return _shared


def reset_eye_colors() -> None:
    """Forget the shared state (tests only)."""
    global _shared
    with _shared_lock:
        _shared = None


def palette_snapshot() -> list[dict]:
    return [{"key": k, "label": label, "color": hex_} for k, label, hex_ in PALETTE]
