import copy
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from src.logging import get_logger
from src.utils.resource_finder import (
    get_config_dir,
    get_user_data_dir,
)

logger = get_logger()

# Process-wide authoritative configuration: created by initialize_config(), read by get_config(); no lazy singleton
_current: "ConfigManager | None" = None


def initialize_config() -> "ConfigManager":
    """Create or return the initialized configuration manager (called at application entry point)."""
    global _current
    if _current is None:
        _current = ConfigManager()
        # Apply PATHS overrides and migrate cache/logs/music/keywords (config directory is not migrated)
        try:
            from src.utils.resource_finder import apply_path_overrides_from_config

            apply_path_overrides_from_config(_current, migrate=True)
        except Exception as e:
            logger.warning("Failed to apply PATHS directory overrides: %s", e, exc_info=True)
    return _current


def get_config() -> "ConfigManager":
    """Get the initialized configuration manager.

    Raises:
        RuntimeError: initialize_config() has not been called yet
    """
    if _current is None:
        raise RuntimeError(
            "ConfigManager is not initialized: please call initialize_config() at entry point"
        )
    return _current


def reset_config() -> None:
    """Discard process configuration (for testing only)."""
    global _current
    _current = None


class ConfigManager:
    """
    Configuration manager (regular instance; process authority held by initialize_config/get_config).
    """

    # Current schema version; _migrate_config() upgrades to this version on load
    CONFIG_VERSION = 1

    # Default configuration (complete product schema; deepcopied on load, never share nested objects across instances)
    DEFAULT_CONFIG = {
        "CONFIG_VERSION": 1,
        "SYSTEM_OPTIONS": {
            "CLIENT_ID": None,
            "DEVICE_ID": None,
            "WINDOW_SIZE_MODE": "default",
            "NETWORK": {
                "OTA_VERSION_URL": "https://api.tenclass.net/xiaozhi/ota/",
                "WEBSOCKET_URL": None,
                "WEBSOCKET_ACCESS_TOKEN": None,
                "MQTT_INFO": None,
                "ACTIVATION_VERSION": "v2",  # Options: v1, v2
                "AUTHORIZATION_URL": "https://xiaozhi.me/",
            },
        },
        "WAKE_WORD_OPTIONS": {
            "USE_WAKE_WORD": True,
            "MODEL_PATH": "models/zh",
            "NUM_THREADS": 5,
            "PROVIDER": "cpu",
            "MAX_ACTIVE_PATHS": 2,
            "KEYWORDS_SCORE": 1.8,
            "KEYWORDS_THRESHOLD": 0.2,
            "NUM_TRAILING_BLANKS": 1,
            "WAKE_WORD": "Sophia",
            "WAKE_WORD_LANG": "zh",
        },
        "CAMERA": {
            "camera_index": 0,
            # Device path takes precedence over index, e.g. "/dev/video0" (Raspberry Pi USB/V4L2)
            "device": "",
            # auto | opencv | picamera2; auto tries OpenCV first, then falls back to CSI (picamera2)
            "backend": "auto",
            # none | horizontal | vertical | both; applied once, where frames are read,
            # so the preview, detection and take_photo all see the corrected image
            "flip": "none",
            # Pixel format requested from V4L2 cameras, e.g. "MJPG" (less USB bandwidth and CPU
            # than raw YUYV). Empty keeps the driver default.
            "fourcc": "",
            "frame_width": 640,
            "frame_height": 480,
            "fps": 30,
            # Number of warm-up frames to discard after opening (first few USB/Pi frames are often invalid)
            "warm_up_frames": 5,
            "Local_VL_url": "https://open.bigmodel.cn/api/paas/v4/",
            "VLapi_key": "",
            "models": "glm-4v-plus",
        },
        # GUI face (see docs/DECISIONS.md)
        "FACE": {
            # robot: code-drawn animated eyes; gif: classic window with assets/emojis
            "STYLE": "robot",
            # Kiosk mode: fullscreen, no title bar or controls, hidden cursor
            "FULLSCREEN": False,
            # Start in auto mode (tap to start/stop a continuously listening conversation)
            "AUTO_MODE": False,
            # Never stop listening: start on launch, resume after every reply and after any
            # disconnect (with backoff); a tap interrupts speech instead of ending the chat.
            # Implies AUTO_MODE.
            "ALWAYS_LISTEN": False,
            # With ALWAYS_LISTEN: greet out loud once per launch (at most once per
            # GREET_MIN_INTERVAL_S across restarts), then listen once the greeting ends. GREET_TEXT is sent as
            # a wake word, which the server answers with its own spoken greeting; it rejects long
            # text ("Detect is only for wake words"), so keep it to a word or two.
            "GREET_ON_START": False,
            "GREET_TEXT": "Xin chào",
            "GREET_MIN_INTERVAL_S": 300,
            "WINDOW_WIDTH": 1024,
            "WINDOW_HEIGHT": 600,
            "ESC_TO_EXIT": True,  # Kiosk mode only
            # Show the recognized user speech in a dimmer style above the assistant text
            "SHOW_USER_TEXT": True,
            "TYPEWRITER_CHARS_PER_S": 28,
            # How long finished text stays before fading out
            "TEXT_HOLD_MS": 1800,
            # Stop showing "thinking" if no reply arrives within this time
            "THINKING_TIMEOUT_S": 15,
            # Seconds of inactivity while idle; 0 disables the transition
            "SLEEPY_AFTER_S": 90,
            "ASLEEP_AFTER_S": 240,
            "BLINK_MIN_MS": 2000,
            "BLINK_MAX_MS": 6000,
            "DOUBLE_BLINK_CHANCE": 0.2,
            # Small frames-per-second readout, for checking performance on the Pi
            "SHOW_FPS": False,
        },
        # On-device object detection (needs the "vision" extra and an exported model)
        "VISION": {
            "ENABLED": True,
            "BACKEND": "auto",  # auto (ncnn, then onnxruntime) | ncnn | onnxruntime
            # {imgsz} is replaced by INPUT_SIZE; relative paths start at the app root
            "NCNN_MODEL_PATH": "models/yolo/yolo11n_{imgsz}_ncnn_model",
            "ONNX_MODEL_PATH": "models/yolo/yolo11n_{imgsz}.onnx",
            "INPUT_SIZE": 320,  # 320 or 640; each size needs its own export
            "NUM_THREADS": 2,
            "CONFIDENCE": 0.35,
            "IOU": 0.45,
            "INFERENCE_RATE": 4.0,  # Inferences per second
            # detect_objects reuses a result this recent instead of waiting for a new one
            "MAX_RESULT_AGE_S": 2.0,
            "DETECT_TIMEOUT_S": 8.0,
            # Without a preview, stop the camera this long after the last query
            "IDLE_STOP_S": 60.0,
            "PREVIEW": {
                "ENABLED": True,
                "CORNER": "top-right",  # top-left | top-right | bottom-left | bottom-right
                "WIDTH": 240,
                "FPS": 12.0,
                "MARGIN": 16,
                "SHOW_BOXES": True,
            },
        },
        # Pan/tilt head: two servos driven by a Yolo UNO (ESP32-S3) over USB serial running
        # firmware/servo_bridge. Angles are the head's own: pan + = turn to the robot's left,
        # tilt + = look up. See docs in firmware/servo_bridge/README.md.
        "SERVO": {
            "ENABLED": False,
            # Empty: /dev/yolo_uno (udev symlink), then any port with USB VID 0x303a
            "PORT": "",
            "BAUD": 115200,
            # Set from the webcam check: flip when a positive angle turns the wrong way
            "PAN_INVERT": False,
            "TILT_INVERT": False,
            # Servo degrees added to 90 so the head looks straight at "center"
            "PAN_OFFSET": 0,
            "TILT_OFFSET": 0,
            # Soft limits in head degrees (pan gear 1:1, at most 90; tilt gear 1:2, at most 45)
            "PAN_LIMIT": 90,
            "TILT_LIMIT": 45,
            # Follow the largest person YOLO sees (needs VISION.ENABLED)
            "TRACKING_ENABLED": True,
            # Head degrees per unit of normalized offset (-1..1 from the frame center)
            "KP_PAN": 12.0,
            "KP_TILT": 8.0,
            # Offsets smaller than this (normalized) are ignored, so the head stays still
            "DEADBAND": 0.08,
            # Largest correction per detection result, in head degrees
            "MAX_STEP_DEG": 6.0,
            # Weight of the newest offset in the moving average (1 = no smoothing)
            "EMA_ALPHA": 0.5,
            # No person for this long: drift back to center
            "LOST_TIMEOUT_SEC": 3.0,
            # Tracking pauses this long after a voice command moves the head
            "MANUAL_HOLD_SEC": 5.0,
        },
        # Web control panel (3D head control, camera, face, chat); see docs/web_panel.md.
        # Plain HTTP for the local network only: there is no other protection than PASSWORD.
        "WEB": {
            "ENABLED": False,
            "HOST": "0.0.0.0",
            "PORT": 8080,
            # Empty: no password (only on a trusted LAN)
            "PASSWORD": "",
            # Frames per second of the MJPEG camera stream (encoded only while someone watches)
            "CAMERA_STREAM_FPS": 8,
            # Also serve HTTPS with a self-signed certificate (made with openssl in the
            # app data dir on first start); phones need it for the tilt-to-steer mode
            "HTTPS_ENABLED": False,
            "HTTPS_PORT": 8443,
        },
        "SHORTCUTS": {
            "ENABLED": True,
            "MANUAL_PRESS": {"modifier": "ctrl", "key": "j", "description": "Hold to Talk"},
            "AUTO_TOGGLE": {"modifier": "ctrl", "key": "k", "description": "Auto Mode"},
            "ABORT": {"modifier": "ctrl", "key": "q", "description": "Abort Chat"},
            "MODE_TOGGLE": {"modifier": "ctrl", "key": "m", "description": "Toggle Mode"},
            "WINDOW_TOGGLE": {
                "modifier": "ctrl",
                "key": "w",
                "description": "Show/Hide Window",
            },
        },
        "AEC_OPTIONS": {
            "ENABLED": False,
            # When AEC is active, TTS does not pause music and mixes/ducks in parallel (automatically falls back to pausing if engine is bypassed)
            "MUSIC_PARALLEL": True,
            # Delay compensation (in protocol frames), actual delay_ms = 40 + N * frame_duration
            "FRAME_DELAY": 3,
            # Noise suppression / high-pass preprocessing
            "ENABLE_PREPROCESS": True,
        },
        # Writable directory overrides (config remains fixed at user_data/config; null = default)
        # Environment variables take precedence: XIAOZHI_CACHE_DIR / XIAOZHI_LOG_DIR /
        # XIAOZHI_MUSIC_CACHE_DIR / XIAOZHI_KEYWORDS_DIR / XIAOZHI_DATA_DIR
        # Changing CACHE/LOG/MUSIC/KEYWORDS copies contents from the old directory to the new one on startup (without deleting old files)
        "PATHS": {
            "CACHE_DIR": None,
            "LOG_DIR": None,
            "MUSIC_CACHE_DIR": None,
            "KEYWORDS_DIR": None,
        },
        # External MCP plugins (user directory mcp_plugins, bundled with lib/)
        "MCP_PLUGINS": {
            "ENABLED": True,
            "DIR": None,  # null = {user_data}/mcp_plugins
            "ENABLED_IDS": [],  # empty = follow plugin's enabled_by_default
            "DISABLED_IDS": [],
            "ALLOW_HOST_GET": ["config_readonly", "logger"],
            # Strict validation (disabled by default so sample packages without platforms/abi can still load)
            "ENFORCE_PREFIX": False,
            "REQUIRE_PYTHON_ABI": False,
            "REQUIRE_PLATFORMS": False,
        },
        # MCP tool exposure (blacklist: omitted from tools/list and rejected on call)
        "MCP_TOOLS": {
            "DISABLED": [],  # e.g. ["music_player.stop", "self.application.launch"]
        },
        "AUDIO_DEVICES": {
            "input_device_id": None,
            "input_device_name": None,
            "output_device_id": None,
            "output_device_name": None,
            "input_sample_rate": None,
            "output_sample_rate": None,
            "input_channels": None,
            "output_channels": None,
            "force_output_sample_rate": None,  # Play at this rate whatever the device reports (e.g. 48000)
            # True: a configured device name that is not found is an error (never falls back to
            # another device, such as a webcam microphone)
            "strict_names": False,
            # > 0: if the audio device cannot be opened at startup, keep retrying in the
            # background for this many seconds instead of running without audio
            "init_retry_s": 0,
            "opus_output_sample_rate": 24000,  # Opus decode sample rate: 24000 (official) or 16000 (third-party)
            "frame_duration": 20,  # Audio frame duration (ms): 20 (low latency) / 40 (balanced) / 60 (low CPU)
        },
        "LOGGING": {
            "LEVEL": "INFO",  # DEBUG, INFO, WARNING, ERROR, CRITICAL
            "FORMAT_TYPE": "colored",  # colored, json, simple
            "ENABLE_CONSOLE": True,
            "ENABLE_FILE": True,
            "ENABLE_ERROR_FILE": True,
            "ENABLE_JSON_FILE": False,
            "ENABLE_ASYNC": False,
            "ENABLE_SENSITIVE_FILTER": True,
            "MAX_BYTES": 10485760,  # 10MB
            "BACKUP_COUNT": 30,
            "ROTATION_WHEN": "midnight",  # midnight, H, D
            "THIRD_PARTY_LEVELS": {
                "urllib3": "WARNING",
                "websockets": "WARNING",
                "asyncio": "WARNING",
                "paho": "WARNING",
                "PIL": "WARNING",
            },
        },
        # Music API (empty string = use built-in default URL at runtime)
        "MUSIC": {
            "SEARCH_URL": "",
            "URL_API": "",
            "URL_API_KEY": "",
            "LYRICS_URL": "",
            "DEFAULT_PLATFORM": "kw",
            "DEFAULT_QUALITY": "320k",
        },
    }

    def __init__(self):
        """Initialize configuration manager (direct construction; application should use initialize_config)."""
        self._init_config_paths()
        self._config = self._load_config()

    def _init_config_paths(self):
        """
        Initialize configuration file paths.

        Configuration files are stored in the user data directory so they remain writable after packaging.
        Migrates default configuration from the installation directory on first run.
        """
        self.config_dir = get_user_data_dir() / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.config_file = self.config_dir / "config.json"

        # If no config file exists in the user directory, try migrating from the installation directory
        if not self.config_file.exists():
            install_config = get_config_dir() / "config.json"
            if install_config.exists():
                try:
                    # Validate JSON first to avoid copying a corrupted installation config
                    json.loads(install_config.read_text(encoding="utf-8"))
                    shutil.copy2(install_config, self.config_file)
                    logger.info(
                        f"Migrated configuration from installation directory: {install_config} -> {self.config_file}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to migrate configuration file: {e}, using default configuration", exc_info=True
                    )

        logger.info(f"Configuration directory: {self.config_dir.absolute()}")
        logger.info(f"Configuration file: {self.config_file.absolute()}")

    def _default_config_copy(self) -> Dict[str, Any]:
        """Deep-copy default configuration to prevent instances from sharing nested dicts/lists with class attributes."""
        return copy.deepcopy(self.DEFAULT_CONFIG)

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration file; create if missing; back up and fall back to defaults if corrupted."""
        try:
            if self.config_file.exists():
                logger.debug(f"Found configuration file: {self.config_file}")
                try:
                    raw = self.config_file.read_text(encoding="utf-8")
                    config = json.loads(raw)
                except Exception as e:
                    backup = self._backup_corrupt_config(e)
                    logger.error(
                        "Configuration file corrupted, backed up%s and falling back to defaults: %s",
                        f" to {backup}" if backup else "",
                        e,
                        exc_info=True,
                    )
                    defaults = self._default_config_copy()
                    self._save_config(defaults)
                    return defaults

                if not isinstance(config, dict):
                    backup = self._backup_corrupt_config(
                        TypeError(f"Root node must be an object, got {type(config).__name__}")
                    )
                    logger.error(
                        "Invalid configuration file root node, backed up%s and falling back to defaults",
                        f" to {backup}" if backup else "",
                    )
                    defaults = self._default_config_copy()
                    self._save_config(defaults)
                    return defaults

                merged = self._merge_configs(self._default_config_copy(), config)
                # Base version on the disk file; merge carries default CONFIG_VERSION which would falsely skip migration
                try:
                    file_ver = int(config.get("CONFIG_VERSION", 0) or 0)
                except (TypeError, ValueError):
                    file_ver = 0
                return self._migrate_config(merged, from_version=file_ver)

            logger.info("Configuration file does not exist, creating default configuration")
            defaults = self._default_config_copy()
            self._save_config(defaults)
            return defaults

        except Exception as e:
            logger.error(f"Error loading configuration: {e}", exc_info=True)
            return self._default_config_copy()

    def _migrate_config(
        self, config: Dict[str, Any], *, from_version: int | None = None
    ) -> Dict[str, Any]:
        """Perform forward migration based on CONFIG_VERSION; write back to disk when necessary.

        from_version: version from the disk file (before merge). If omitted, reads the field from config
        (which may already be at the latest version if defaults were merged, causing migration to be skipped).

        Conventions:
        - Missing / invalid version is treated as 0
        - Each step applies low-risk patches (renaming, adding sections, normalizing values)
        - Writes back after upgrading to CONFIG_VERSION to avoid repeating migration on next startup
        """
        if from_version is not None:
            ver = int(from_version)
        else:
            try:
                raw_ver = config.get("CONFIG_VERSION", 0)
                try:
                    ver = int(raw_ver)
                except (TypeError, ValueError):
                    ver = 0
            except Exception:
                ver = 0

        original = ver
        # --- Migration steps (append in ascending version order) ---
        if ver < 1:
            # v1: introduce version number; backfill MCP_TOOLS; normalize subscribe_topic string "null"
            config.setdefault("MCP_TOOLS", {"DISABLED": []})
            if not isinstance(config.get("MCP_TOOLS"), dict):
                config["MCP_TOOLS"] = {"DISABLED": []}
            config["MCP_TOOLS"].setdefault("DISABLED", [])

            try:
                net = config.get("SYSTEM_OPTIONS", {}).get("NETWORK", {})
                mqtt = net.get("MQTT_INFO")
                if isinstance(mqtt, dict) and mqtt.get("subscribe_topic") == "null":
                    mqtt["subscribe_topic"] = None
            except Exception:
                pass
            ver = 1

        # Future: if ver < 2: ...; ver = 2

        if ver != original or config.get("CONFIG_VERSION") != self.CONFIG_VERSION:
            config["CONFIG_VERSION"] = self.CONFIG_VERSION
            if self._save_config(config):
                logger.info(
                    "Configuration migrated: v%s -> v%s", original, self.CONFIG_VERSION
                )
            else:
                logger.warning(
                    "Failed to write back migrated configuration (in-memory is v%s)", self.CONFIG_VERSION
                )
        else:
            config["CONFIG_VERSION"] = self.CONFIG_VERSION
        return config

    def _backup_corrupt_config(self, error: Exception) -> str | None:
        """Back up corrupted config.json as .corrupt-<timestamp> and return the backup path."""
        try:
            if not self.config_file.exists():
                return None
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.config_file.with_name(f"config.json.corrupt-{ts}")
            # Avoid name collision in extreme cases
            n = 0
            while backup.exists():
                n += 1
                backup = self.config_file.with_name(f"config.json.corrupt-{ts}-{n}")
            shutil.copy2(self.config_file, backup)
            logger.warning("Backed up corrupted configuration: %s (%s)", backup, error)
            return str(backup)
        except Exception as e:
            logger.error("Failed to back up corrupted configuration: %s", e, exc_info=True)
            return None

    def _save_config(self, config: dict) -> bool:
        """Atomically write configuration file (temporary file + rename to prevent corruption on interrupted writes)."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)

            tmp_file = self.config_file.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp_file, self.config_file)
            logger.debug(f"Configuration saved to: {self.config_file}")
            return True

        except Exception as e:
            logger.error(f"Error saving configuration: {e}", exc_info=True)
            return False

    @staticmethod
    def _merge_configs(default: dict, custom: dict) -> dict:
        """Recursively merge configurations: default as base, custom overrides; deep-merge when both sides are dicts.

        Note: caller should pass a deepcopied default to avoid mutating class-level DEFAULT_CONFIG.
        """
        result = default  # Merge in-place when default is already an independent copy
        for key, value in custom.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = ConfigManager._merge_configs(result[key], value)
            else:
                result[key] = value
        return result

    def get_config(self, path: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-separated path.
        path: dot-separated configuration path, e.g. "SYSTEM_OPTIONS.NETWORK.MQTT_INFO"
        """
        try:
            value = self._config
            for key in path.split("."):
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def update_config(self, path: str, value: Any, *, save: bool = True) -> bool:
        """
        Update a specific configuration entry.
        path: dot-separated configuration path, e.g. "SYSTEM_OPTIONS.NETWORK.MQTT_INFO"
        save: whether to persist immediately; pass False for batch updates and call save_config()/update_configs at the end

        Intermediate nodes that are None or non-dict are promoted to dicts before writing (compatible with defaults like MQTT_INFO: null).
        """
        try:
            current: Any = self._config
            *parts, last = path.split(".")
            for part in parts:
                if not isinstance(current, dict):
                    raise TypeError(
                        f"Configuration path prefix is not an object, cannot write {path} (current node type {type(current).__name__})"
                    )
                existing = current.get(part, None)
                if not isinstance(existing, dict):
                    # Key missing, or value is None/scalar: create empty dict to continue traversal
                    existing = {}
                    current[part] = existing
                current = existing
            if not isinstance(current, dict):
                raise TypeError(f"Cannot write to configuration path: {path}")
            current[last] = value
            if not save:
                return True
            return self._save_config(self._config)
        except Exception as e:
            logger.error(f"Error updating configuration {path}: {e}", exc_info=True)
            return False

    def update_configs(self, updates: Dict[str, Any]) -> bool:
        """Batch-update multiple configuration paths, writing to disk only once.

        Args:
            updates: path -> value, e.g.
                {"SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL": "wss://..."}
        """
        if not updates:
            return True
        try:
            for path, value in updates.items():
                if not self.update_config(path, value, save=False):
                    return False
            return self._save_config(self._config)
        except Exception as e:
            logger.error(f"Error during batch configuration update: {e}", exc_info=True)
            return False

    def save_config(self) -> bool:
        """Persist current in-memory configuration to disk."""
        return self._save_config(self._config)

    def reload_config(self, *, apply_paths: bool = True) -> bool:
        """Reload configuration file from disk.

        apply_paths: whether to re-apply PATHS overrides (without migrating directories).
        PATHS directory migration is performed only during initialize_config.
        """
        try:
            self._config = self._load_config()
            if apply_paths:
                try:
                    from src.utils.resource_finder import apply_path_overrides_from_config

                    apply_path_overrides_from_config(self, migrate=False)
                except Exception as e:
                    logger.warning(
                        "Failed to apply PATHS after reload: %s", e, exc_info=True
                    )
            logger.info("Configuration file reloaded")
            return True
        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}", exc_info=True)
            return False

    def generate_uuid(self) -> str:
        """Generate a UUID v4 string."""
        return str(uuid.uuid4())

    def initialize_client_id(self):
        """Ensure a client ID exists."""
        if not self.get_config("SYSTEM_OPTIONS.CLIENT_ID"):
            client_id = self.generate_uuid()
            success = self.update_config("SYSTEM_OPTIONS.CLIENT_ID", client_id)
            if success:
                logger.info(f"Generated new client ID: {client_id}")
            else:
                logger.error("Failed to save new client ID")
