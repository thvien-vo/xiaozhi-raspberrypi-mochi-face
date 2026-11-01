"""CLI mode device activation flow."""

from datetime import datetime

from src.constants.system import SystemConstants
from src.logging import get_logger
from src.ui.shared.activation import BaseActivation

logger = get_logger()


class CliActivation(BaseActivation):
    """CLI mode device activation handler.

    Inherits from BaseActivation and overrides terminal output display methods only.
    """

    def __init__(self, activation_service, init_result: dict):
        super().__init__(activation_service, init_result)

    async def run(self) -> bool:
        self._print_header()

        if not self.needs_activation():
            self._log("Device is already activated, no further action required")
            self._print_success()
            return True

        self._print_device_info()
        try:
            return await self._core_activate()
        except KeyboardInterrupt:
            self._log("\nActivation process interrupted by user")
            return False

    # ---- BaseActivation display methods ----

    def _show_code(self, data: dict) -> None:
        self._print_activation_info(data)

    def _show_result(self, success: bool) -> None:
        if success:
            self._print_success()
        else:
            self._print_failure()

    def _show_error(self, msg: str) -> None:
        self._log(msg)

    # ---- Terminal display helper methods ----

    def _print_header(self):
        print("\n" + "=" * 60)
        print(f"{SystemConstants.APP_DISPLAY_NAME} - Device Activation")
        print("=" * 60)

    def _print_device_info(self):
        """Print device information."""
        serial = self._service.get_serial_number() or "--"
        mac = self._service.get_mac_address() or "--"
        status = self._service.get_activation_status()

        print("\nDevice Information:")
        print(f"  Serial Number: {serial}")
        print(f"  MAC Address: {mac}")

        local = status.get("local_activated", False)
        server = status.get("server_activated", False)
        consistent = status.get("status_consistent", True)

        if not consistent:
            status_text = "Reactivation Required" if local and not server else "Auto-Repaired"
        else:
            status_text = "Activated" if local else "Not Activated"

        print(f"  Status: {status_text}")

    def _print_activation_info(self, data: dict):
        """Print activation information."""
        code = data.get("code", "------")
        message = data.get("message", "Please visit xiaozhi.me and enter the verification code")

        print("\n" + "-" * 60)
        print("Activation Information")
        print("-" * 60)
        print(f"Verification Code: {' '.join(code)}")
        print(f"Instructions: {message}")
        print("-" * 60)
        print("\nActivation Steps:")
        print("  1. Open your browser and visit xiaozhi.me")
        print("  2. Sign in to your account")
        print("  3. Select Add Device")
        print(f"  4. Enter verification code: {code}")
        print("  5. Confirm adding the device")

    def _print_success(self):
        print("\n" + "=" * 60)
        print("Device Activation Successful!")
        print("=" * 60)
        print("The device has been successfully added to your account")
        print(f"Starting {SystemConstants.APP_DISPLAY_NAME}...")
        print("=" * 60 + "\n")

    def _print_failure(self):
        print("\n" + "=" * 60)
        print("Device Activation Failed")
        print("=" * 60)
        print("Possible Causes:")
        print("  - Unstable network connection")
        print("  - Incorrect or expired verification code")
        print("  - Server temporarily unavailable")
        print("\nSolutions:")
        print("  - Check your network connection")
        print("  - Re-run the program to obtain a new verification code")
        print("  - Ensure the verification code is entered accurately")
        print("=" * 60 + "\n")

    def _log(self, message: str):
        """Print timestamped log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")
        logger.info(message)
