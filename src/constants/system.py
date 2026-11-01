# System constants definition
from enum import Enum


class InitializationStage(Enum):
    """
    Initialization stage enumeration.
    """

    DEVICE_FINGERPRINT = "Phase 1: Device Identity Preparation"
    CONFIG_MANAGEMENT = "Phase 2: Configuration Management Initialization"
    OTA_CONFIG = "Phase 3: OTA Configuration Retrieval"
    ACTIVATION = "Phase 4: Activation Process"


class SystemConstants:
    """
    System constants.
    """

    # Application info
    APP_NAME = "py-xiaozhi"  # Application identifier (ASCII, used for directories, config, bundle_id)
    APP_DISPLAY_NAME = "Xiaozhi"  # Display name (used for window title, Launchpad, installer UI)
    APP_VERSION = "2.1.2"
    BOARD_TYPE = "bread-compact-wifi"

    # Default timeout settings
    DEFAULT_TIMEOUT = 10
    ACTIVATION_MAX_RETRIES = 60
    ACTIVATION_RETRY_INTERVAL = 5

    # File name constants
    CONFIG_FILE = "config.json"
    EFUSE_FILE = "efuse.json"
