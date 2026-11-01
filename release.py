#!/usr/bin/env python3
"""Release script: reads version from system.py, auto-updates + generates build.json + creates git tag.

Usage: python release.py [--dry-run]
"""

import json
import re
import subprocess
import sys
from pathlib import Path

SYSTEM_PY = Path("src/constants/system.py")
BUILD_JSON = Path("build.json")

VERSION_RE = re.compile(r'(APP_VERSION\s*=\s*")([^"]+)(")')

VERSION_TYPES = [
    ("patch", "Bug fix", "1.0.0 → 1.0.1"),
    ("minor", "New feature", "1.0.0 → 1.1.0"),
    ("major", "Breaking change", "1.0.0 → 2.0.0"),
    ("prepatch", "Pre-release patch", "1.0.0 → 1.0.1-beta.0"),
    ("prerelease", "Continue pre-release", "1.0.1-beta.0 → 1.0.1-beta.1"),
]

BUILD_TEMPLATE = {
    "entry": "main.py",
    "name": "",
    "display_name": "",
    "version": "",
    "icon": "assets/icon.png",
    "pyinstaller": {
        "onefile": False,
        "windowed": True,
        "add_data": [
            "models:models",
            "scripts:scripts",
            "src:src",
            "libs:libs",
            "assets:assets",
        ],
        "clean": True,
        "noconfirm": True,
    },
    "platforms": {
        "macos": {
            "bundle_identifier": "",
            "minimum_system_version": "10.13",
            "category": "public.app-category.productivity",
            "microphone_usage_description": "This application requires access to the microphone for audio recording.",
            "speech_recognition_usage_description": "This application requires speech recognition to understand voice commands.",
            "camera_usage_description": "This application requires access to the camera for photo and video capture.",
            "copyright": "© 2024 Company. All rights reserved.",
            "dmg": {
                "volname": "",
                "window_size": [600, 450],
                "icon_size": 100,
                "format": "UDZO",
            },
        },
        "windows": {
            "inno_setup": {
                "create_desktop_icon": True,
                "create_start_menu_icon": True,
                "allow_run_after_install": True,
                "languages": ["chinesesimplified", "english"],
            },
            "pyinstaller": {"contents_directory": "."},
        },
        "linux": {
            "deb": {
                "package": "",
                "section": "utils",
                "priority": "optional",
                "desktop_entry": True,
                "categories": ["Utility"],
            }
        },
    },
}


def read_system_constants() -> dict:
    """Read APP_NAME / APP_DISPLAY_NAME / APP_VERSION from system.py."""
    content = SYSTEM_PY.read_text(encoding="utf-8")
    values = {}
    for key in ("APP_NAME", "APP_DISPLAY_NAME", "APP_VERSION"):
        m = re.search(rf'{key}\s*=\s*"([^"]+)"', content)
        if m:
            values[key] = m.group(1)
    return values


def parse_version(v: str) -> tuple:
    """Parse semver: major.minor.patch[-pre.N]"""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)(?:-(\w+)\.(\d+))?", v)
    if not m:
        raise ValueError(f"Failed to parse version number: {v}")
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre_tag = m.group(4)
    pre_num = int(m.group(5)) if m.group(5) is not None else None
    return major, minor, patch, pre_tag, pre_num


def bump_version(current: str, bump_type: str) -> str:
    """Calculate the new version number."""
    major, minor, patch, pre_tag, pre_num = parse_version(current)

    if bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "prepatch":
        return f"{major}.{minor}.{patch + 1}-beta.0"
    elif bump_type == "prerelease":
        if pre_tag and pre_num is not None:
            return f"{major}.{minor}.{patch}-{pre_tag}.{pre_num + 1}"
        return f"{major}.{minor}.{patch + 1}-beta.0"
    else:
        raise ValueError(f"Unknown version type: {bump_type}")


def update_system_py(new_version: str) -> None:
    """Update APP_VERSION in system.py."""
    content = SYSTEM_PY.read_text(encoding="utf-8")
    new_content = VERSION_RE.sub(rf"\g<1>{new_version}\g<3>", content)
    SYSTEM_PY.write_text(new_content, encoding="utf-8")


def generate_build_json(constants: dict) -> None:
    """Generate build.json from system.py constants."""
    cfg = json.loads(json.dumps(BUILD_TEMPLATE))

    name = constants["APP_NAME"]
    display_name = constants["APP_DISPLAY_NAME"]
    version = constants["APP_VERSION"]

    cfg["name"] = name
    cfg["display_name"] = display_name
    cfg["version"] = version
    cfg["platforms"]["macos"]["bundle_identifier"] = f"com.{name}.app"
    cfg["platforms"]["macos"]["dmg"]["volname"] = f"{display_name} Installer"
    cfg["platforms"]["linux"]["deb"]["package"] = name

    BUILD_JSON.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def run_git(new_version: str) -> None:
    """git commit + tag + push."""
    subprocess.run(["git", "add", str(SYSTEM_PY), str(BUILD_JSON)], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore: release v{new_version}"], check=True
    )
    subprocess.run(
        ["git", "tag", "-a", f"v{new_version}", "-m", f"v{new_version}"], check=True
    )
    subprocess.run(["git", "push", "--follow-tags"], check=True)


def main():
    dry_run = "--dry-run" in sys.argv

    constants = read_system_constants()
    current = constants.get("APP_VERSION")
    if not current:
        print("❌ Failed to read APP_VERSION from system.py")
        sys.exit(1)

    print(f"\nCurrent version: {current}")
    print("\nSelect version bump type:\n")
    for i, (_, label, desc) in enumerate(VERSION_TYPES, 1):
        print(f"  {i}. {label} — {desc}")

    try:
        choice = input("\nEnter option (1-5): ").strip()
        index = int(choice) - 1
        if not (0 <= index < len(VERSION_TYPES)):
            raise ValueError
    except (ValueError, EOFError):
        print("❌ Invalid option")
        sys.exit(1)

    bump_type = VERSION_TYPES[index][0]
    new_version = bump_version(current, bump_type)

    print(f"\nVersion change: {current} → {new_version}")

    if dry_run:
        print("\n[dry-run] The following actions would be performed:")
        print(f"  1. Update {SYSTEM_PY}: APP_VERSION = \"{new_version}\"")
        print(f"  2. Generate {BUILD_JSON}")
        print(f"  3. git commit + tag v{new_version} + push")
        print("\n[dry-run] No changes were actually made.")
        return

    update_system_py(new_version)
    constants["APP_VERSION"] = new_version
    generate_build_json(constants)

    print(f"\n✅ Updated {SYSTEM_PY}")
    print(f"✅ Generated {BUILD_JSON}")

    run_git(new_version)

    print(f"\n✅ Version v{new_version} released successfully! GitHub Actions will automatically start building.")


if __name__ == "__main__":
    main()
