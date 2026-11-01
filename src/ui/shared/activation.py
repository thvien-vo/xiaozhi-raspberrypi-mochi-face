"""Activation UI base class.

Shares the "fetch code -> display -> service.activate -> result" flow; subclasses only handle display.
"""


class BaseActivation:
    """Activation UI base class (not ABC, avoiding metaclass conflicts with PySide6 QObject).

    Args:
        activation_service: ActivationService instance (obtained via create(), non-singleton)
        init_result: initialize() result passed by handle_activation to avoid duplicate initialization
    """

    def __init__(self, activation_service=None, init_result=None):
        # Optional parameters: QObject.__init__ super chain may call without args in PySide6 multiple inheritance
        self._service = activation_service
        self._init_result = init_result

    def needs_activation(self) -> bool:
        """Whether activation UI flow is needed."""
        if self._init_result is None:
            return False
        return bool(self._init_result.get("need_activation_ui", False))

    async def _core_activate(self) -> bool:
        """Core activation: fetch code -> UI display -> service.activate (including clipboard/announcement side effects)."""
        if self._service is None:
            self._show_error("Activation service not initialized")
            return False

        data = self._service.get_activation_data()
        if not data:
            self._show_error("Failed to obtain activation data")
            return False

        self._show_code(data)
        success = await self._service.activate(data)
        self._show_result(success)
        return success

    async def run(self) -> bool:
        """Run activation flow."""
        raise NotImplementedError

    def _show_code(self, data: dict) -> None:
        """Display activation verification code (CLI print / GUI write Model)."""
        raise NotImplementedError

    def _show_result(self, success: bool) -> None:
        """Display activation result."""
        raise NotImplementedError

    def _show_error(self, msg: str) -> None:
        """Display error (optional override)."""
        pass
