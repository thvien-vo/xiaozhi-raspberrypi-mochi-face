#!/usr/bin/env python3
"""Merge a JSON preset into the user configuration.

    python scripts/apply_config_preset.py deploy/raspberry-pi/config.pi5.json
    python scripts/apply_config_preset.py deploy/raspberry-pi/config.pi5.json --dry-run

Only the settings named in the preset change. Everything else stays as it is, including
the device identity and server URLs.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.config_manager import initialize_config  # noqa: E402


def flatten(section: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Turn nested settings into ConfigManager paths such as "FACE.FULLSCREEN"."""
    paths: dict[str, Any] = {}
    for key, value in section.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths.update(flatten(value, f"{path}."))
        else:
            paths[path] = value
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge a JSON preset into the configuration")
    parser.add_argument("preset", type=Path, help="Preset JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Show changes, write nothing")
    args = parser.parse_args()

    try:
        preset = json.loads(args.preset.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Cannot read preset {args.preset}: {e}", file=sys.stderr)
        return 1

    config = initialize_config()
    changes = {
        path: value
        for path, value in flatten(preset).items()
        if config.get_config(path) != value
    }
    for path, value in changes.items():
        print(f"{path}: {config.get_config(path)!r} -> {value!r}")
    if not changes:
        print("Configuration already matches the preset.")
    elif args.dry_run:
        print("Dry run, nothing written.")
    elif config.update_configs(changes):
        print(f"Updated {config.config_file}")
    else:
        print("Failed to write the configuration.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
