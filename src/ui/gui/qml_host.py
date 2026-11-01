"""QML engine host: loading, context injection, root window operations."""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine

from src.logging import get_logger

logger = get_logger()


def _show_window(window, *, activate: bool) -> None:
    """Show window; when activate=False, avoid stealing focus (macOS fullscreen Space friendly)."""
    if window is None:
        return

    if activate:
        window.show()
        window.raise_()
        window.requestActivate()
        return

    # QWidget path: ShowWithoutActivating
    set_attr = getattr(window, "setAttribute", None)
    if callable(set_attr):
        set_attr(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        try:
            window.show()
        finally:
            set_attr(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        return

    # QQuickWindow / QWindow: temporarily disable focus acceptance to avoid becoming the key window
    # (on macOS, a key window switches away from other fullscreen app Spaces)
    flags = window.flags()
    try:
        window.setFlags(flags | Qt.WindowType.WindowDoesNotAcceptFocus)
        window.show()
    finally:
        window.setFlags(flags)


class QmlAppHost:
    """Responsible only for QML engine lifecycle and root object access, without touching business logic."""

    def __init__(self) -> None:
        self._engine: QQmlApplicationEngine | None = None

    @property
    def engine(self) -> QQmlApplicationEngine | None:
        return self._engine

    def create_engine(self) -> QQmlApplicationEngine:
        self._engine = QQmlApplicationEngine()
        return self._engine

    def inject_context(self, properties: dict) -> None:
        if not self._engine:
            raise RuntimeError("QML engine not created")
        ctx = self._engine.rootContext()
        for name, obj in properties.items():
            ctx.setContextProperty(name, obj)
        logger.debug("QmlAppHost: Injected QML context %s", list(properties))

    def load_main(self) -> None:
        if not self._engine:
            raise RuntimeError("QML engine not created")
        qml_dir = Path(__file__).parent / "qml"
        self._engine.addImportPath(str(qml_dir))
        main_qml = qml_dir / "main.qml"
        self._engine.load(QUrl.fromLocalFile(str(main_qml)))
        if not self._engine.rootObjects():
            logger.error("QmlAppHost: Failed to load QML")
            raise RuntimeError("Failed to load QML")
        logger.debug("QmlAppHost: Loaded %s", main_qml)

    def root_window(self):
        if not self._engine:
            return None
        roots = self._engine.rootObjects()
        return roots[0] if roots else None

    def show_root(self, *, activate: bool = True) -> None:
        """Show main window.

        Args:
            activate: True=bring to foreground (tray "Show Window" / shortcut);
                      False=show only without requestActivate (cold start, avoids switching away from macOS fullscreen apps)
        """
        _show_window(self.root_window(), activate=activate)

    def toggle_root_visible(self) -> None:
        window = self.root_window()
        if window is None:
            return
        if window.isVisible():
            window.hide()
        else:
            # User actively toggled: bring to foreground
            self.show_root(activate=True)

    def shutdown(self) -> None:
        if self._engine:
            self._engine.deleteLater()
            self._engine = None
