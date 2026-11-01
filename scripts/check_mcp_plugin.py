#!/usr/bin/env python3
"""Validate external MCP plugin package structure (without starting the full application)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def check_plugin(path: Path, *, strict: bool = False) -> list[str]:
    """Return a list of errors; empty = passed."""
    errors: list[str] = []
    if not path.is_dir():
        return [f"Not a directory: {path}"]

    manifest_path = path / "manifest.json"
    plugin_py = path / "plugin.py"
    if not manifest_path.is_file() and not plugin_py.is_file():
        errors.append("Missing manifest.json and no plugin.py found")
        return errors

    manifest: dict = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"Failed to parse manifest.json: {e}")
            return errors
    else:
        if strict:
            errors.append("Strict mode requires manifest.json")
        manifest = {"id": path.name, "entry": "plugin:register"}

    pid = manifest.get("id") or path.name
    if not pid:
        errors.append("manifest.id is empty")

    entry = str(manifest.get("entry") or "plugin:register")
    if ":" not in entry:
        errors.append(f"entry must be module:attr, got: {entry}")
    else:
        mod, _attr = entry.split(":", 1)
        py = path / f"{mod.replace('.', '/')}.py"
        if not py.is_file() and not (path / mod).is_dir():
            errors.append(f"Entry module file not found: {py}")

    runtime = manifest.get("runtime", "python-inprocess")
    if runtime != "python-inprocess":
        errors.append(f"Currently only runtime=python-inprocess is supported, got: {runtime}")

    if strict:
        if "api_version" not in manifest:
            errors.append("Strict mode requires api_version")
        if "python_abi" not in manifest:
            errors.append("Strict mode requires python_abi")
        if "platforms" not in manifest:
            errors.append("Strict mode requires platforms")
        if "tool_name_prefix" not in manifest:
            errors.append("Strict mode requires tool_name_prefix")

    # lib is optional; if present, it must be a directory
    lib = path / "lib"
    if lib.exists() and not lib.is_dir():
        errors.append("lib exists but is not a directory")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check external MCP plugin package (manifest + entry + optional lib)"
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Plugin directory path, e.g., mcp_plugins/com.example.hello",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Require api_version / python_abi / platforms / tool_name_prefix",
    )
    args = parser.parse_args(argv)
    path = args.path.expanduser().resolve()
    errs = check_plugin(path, strict=args.strict)
    if errs:
        print(f"FAIL {path}")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(f"OK {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
