"""Default application font for Qt platforms that supply none."""

from PySide6.QtGui import QFont, QFontInfo, QFontMetrics, QGuiApplication

# Noto covers Latin with Vietnamese marks; the CJK family is the fallback for Chinese.
_FAMILIES = ["Noto Sans", "Noto Sans CJK SC", "DejaVu Sans"]


def ensure_default_font() -> None:
    """Set a default font when the platform theme provides one that has no glyphs.

    Qt's ``linuxfb`` platform has no theme, so ``QFont()`` has an empty family and every
    Text item without an explicit family draws empty boxes.
    """
    font = QGuiApplication.font()
    if font.family() and QFontMetrics(font).inFont("A"):
        return
    probe = QFont()
    probe.setFamilies(_FAMILIES)
    if QFontInfo(probe).family():
        font = QFont(_FAMILIES[0])
        font.setFamilies(_FAMILIES)
        QGuiApplication.setFont(font)
