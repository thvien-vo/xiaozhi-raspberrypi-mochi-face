import argparse
import asyncio
import locale
import os
import signal
import sys

# Windows: force C/C++ runtime to use UTF-8 to prevent garbled characters when sherpa-onnx reads toned Pinyin files
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        locale.setlocale(locale.LC_ALL, ".UTF-8")
    except locale.Error:
        pass

os.environ["QSG_RHI_BACKEND"] = "opengl"
# Force qasync to use PySide6
os.environ["QT_API"] = "pyside6"
# Use Basic style to support custom controls
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"


def parse_args():
    """Parse command-line arguments."""
    from src.constants.system import SystemConstants

    parser = argparse.ArgumentParser(description=SystemConstants.APP_DISPLAY_NAME)
    # Run mode selection
    # - gui: Graphical interface mode using PySide6 + QML
    # - cli: Command-line mode using terminal interaction (lightweight, suitable for headless/SSH)
    # - tui: Full-screen TUI (Textual) with config editing; requires uv sync --extra tui
    # - gpio: GPIO button mode, Linux (Raspberry Pi) only, controlled via physical buttons
    parser.add_argument(
        "--mode",
        choices=["gui", "cli", "tui", "gpio"],
        default="gui",
        help="Run mode (default: gui): gui / cli / tui (full-screen terminal) / gpio (Linux only)",
    )
    parser.add_argument(
        "--protocol",
        choices=["mqtt", "websocket"],
        default="websocket",
        metavar="PROTOCOL",
        help="Communication protocol: mqtt or websocket (default: websocket; specify --protocol mqtt for MQTT)",
    )
    parser.add_argument(
        "--skip-activation",
        action="store_true",
        help="Skip activation flow and start the application directly (for debugging only)",
    )
    parser.add_argument(
        "--face-demo",
        action="store_true",
        help="Cycle the robot face through all emotions and states, no server needed",
    )
    parser.add_argument(
        "--demo-step-ms",
        type=int,
        default=2500,
        metavar="MS",
        help="Time per step in --face-demo (default: 2500)",
    )
    parser.add_argument(
        "--demo-camera",
        choices=["off", "test-pattern", "real"],
        default="off",
        help="Camera preview in --face-demo: off, synthetic frames, or the real camera",
    )
    return parser.parse_args()


# Parse arguments first, then initialize configuration and logging (forbid ConfigManager lazy singleton)
_args = parse_args()

from src.utils.config_manager import initialize_config  # noqa: E402

initialize_config()

from src.logging import load_logging_config, setup_logging  # noqa: E402

# Disable console log output in CLI/TUI mode (managed by the interface)
setup_logging(
    enable_console=(_args.mode not in ("cli", "tui")),
    config=load_logging_config(),
)

from src.bootstrap.container import ServiceContainer  # noqa: E402
from src.constants.system import SystemConstants  # noqa: E402
from src.logging import get_logger  # noqa: E402

logger = get_logger()


async def handle_activation(mode: str) -> bool:
    """Handle the device activation flow.

    Args:
        mode: Run mode, "gui", "cli", "tui", or "gpio"

    Returns:
        bool: Whether activation succeeded
    """
    try:
        from src.activation import ActivationService, create_activation_ui

        logger.info("Starting device activation check...")
        activation_service = await ActivationService.create()
        init_result = await activation_service.initialize()

        if not init_result.get("success", False):
            logger.error(f"Initialization failed: {init_result.get('error', 'Unknown error')}")
            return False

        if not init_result.get("need_activation_ui", False):
            logger.info("Device is already activated; skipping activation flow")
            return True

        ui = create_activation_ui(mode, activation_service, init_result)
        return await ui.run()

    except Exception as e:
        logger.error(f"Activation flow exception: {e}", exc_info=True)
        return False


async def start_app(mode: str, protocol: str, skip_activation: bool) -> int:
    """Unified entry point for starting the application."""
    global _container  # Used for SIGINT handling
    logger.info(f"Starting {SystemConstants.APP_DISPLAY_NAME}")

    # Handle activation flow
    if not skip_activation:
        activation_success = await handle_activation(mode)
        if not activation_success:
            logger.error("Device activation failed, exiting program")
            return 1
    else:
        logger.warning("Skipping activation flow (debug mode)")

    # Create and start the application
    _container = ServiceContainer()
    return await _container.run(mode=mode, protocol=protocol)


# Global container reference used for SIGINT handling
_container = None


if __name__ == "__main__":
    exit_code = 1
    try:
        # Use already parsed arguments
        args = _args

        if args.face_demo:
            from src.ui.gui.face_demo import run_face_demo

            exit_code = run_face_demo(args.demo_step_ms, args.demo_camera)
            sys.exit(exit_code)

        # Detect Wayland environment and configure Qt platform plugin
        import os

        is_wayland = (
            os.environ.get("WAYLAND_DISPLAY")
            or os.environ.get("XDG_SESSION_TYPE") == "wayland"
        )

        if args.mode == "gui" and is_wayland:
            if "QT_QPA_PLATFORM" not in os.environ:
                os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
                logger.info("Wayland environment: setting QT_QPA_PLATFORM=wayland;xcb")
            os.environ.setdefault("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
            logger.info("Wayland environment detected, compatibility settings applied")

        # Signal handling
        try:
            if hasattr(signal, "SIGTRAP"):
                signal.signal(signal.SIGTRAP, signal.SIG_IGN)
        except Exception:
            pass

        if args.mode == "gui":
            # GUI mode: use PySide6 + qasync
            try:
                import qasync
                from PySide6.QtWidgets import QApplication
            except ImportError as e:
                logger.error(
                    "GUI mode requires PySide6 + qasync, which are not installed in the current environment.\n"
                    "Please install GUI dependencies using the project venv and try again:\n"
                    "  uv sync --extra gui\n"
                    "  # or: pip install '.[gui]'\n"
                    "Then run:\n"
                    "  uv run python main.py\n"
                    "  # or: .venv/bin/python main.py\n"
                    "If GUI is not needed, you can use:\n"
                    "  python main.py --mode cli\n"
                    "  python main.py --mode tui   # requires uv sync --extra tui\n"
                    f"(Original error: {e})"
                )
                sys.exit(1)

            qt_app = QApplication.instance() or QApplication(sys.argv)
            qt_app.setQuitOnLastWindowClosed(False)

            loop = qasync.QEventLoop(qt_app)
            asyncio.set_event_loop(loop)
            logger.info("Created PySide6 + qasync event loop")

            # Set up SIGINT signal handler - request shutdown via TaskManager
            shutdown_state = {"requested": False}

            def handle_sigint(*_):
                if shutdown_state["requested"]:
                    return
                shutdown_state["requested"] = True
                logger.info("Received SIGINT signal, shutting down...")

                # Request graceful shutdown via TaskManager
                try:
                    if _container and _container.tasks:
                        _container.tasks.request_shutdown()
                    else:
                        # Container not ready, quit Qt directly
                        if loop.is_running():
                            loop.call_soon_threadsafe(qt_app.quit)
                except Exception:
                    qt_app.quit()

            signal.signal(signal.SIGINT, handle_sigint)

            try:
                with loop:
                    exit_code = loop.run_until_complete(
                        start_app(args.mode, args.protocol, args.skip_activation)
                    )
            except RuntimeError as e:
                # Catch qasync's "Event loop stopped before Future completed" error
                if "Event loop stopped before Future completed" in str(e):
                    logger.debug("Event loop terminated normally")
                    exit_code = 0
                else:
                    raise
        else:
            # CLI / TUI / GPIO: standard asyncio
            if args.mode == "tui":
                try:
                    import textual  # noqa: F401
                except ImportError as e:
                    logger.error(
                        "TUI mode requires textual. Please run:\n"
                        "  uv sync --extra tui\n"
                        "  pip install '.[tui]'\n"
                        "For headless/SSH environments, continue using: python main.py --mode cli\n"
                        f"(Original error: {e})"
                    )
                    sys.exit(1)

            # CLI / GPIO mode: standard asyncio; SIGINT requests TaskManager shutdown
            shutdown_state = {"requested": False}

            def handle_sigint_cli(*_):
                if shutdown_state["requested"]:
                    # Second Ctrl+C: hard exit
                    logger.warning("Received SIGINT again, forcing exit")
                    os._exit(130)
                shutdown_state["requested"] = True
                logger.info("Received SIGINT signal, shutting down...")
                try:
                    if _container and _container.tasks:
                        _container.tasks.request_shutdown()
                except Exception:
                    pass

            signal.signal(signal.SIGINT, handle_sigint_cli)
            exit_code = asyncio.run(
                start_app(args.mode, args.protocol, args.skip_activation)
            )

    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
        exit_code = 0
    except Exception as e:
        logger.error(f"Program exited abnormally: {e}", exc_info=True)
        exit_code = 1
    finally:
        sys.exit(exit_code)
