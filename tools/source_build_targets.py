#!/usr/bin/env python3
"""Resolve package build targets from the exact firmware source contract."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

try:
    from .catalog_contract import ContractError
except ImportError:  # Direct script execution.
    from catalog_contract import ContractError


FILENAME = re.compile(r"^[a-z0-9_]+\.(?:fap|fal)$")


def source_overlay_exports(source_root: Path) -> dict[str, str]:
    """Return source-owned package path -> exact built FAP/FAL filename."""

    source_root = source_root.resolve()
    try:
        module_path = source_root / "tools/tumoflip/validate_release.py"
        specification = importlib.util.spec_from_file_location(
            "tumoflip_source_validate_release", module_path
        )
        if specification is None or specification.loader is None:
            raise ImportError(f"cannot create import spec for {module_path}")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError) as error:
        raise ContractError(f"cannot load source package contract: {error}") from error
    overlay_files = getattr(module, "PACKAGE_RELEASE_OVERLAY_FILES", None)
    exports_function = getattr(module, "package_extapp_exports", None)
    if overlay_files is None and exports_function is None:
        raise ContractError("source checkout predates selective package overlays")
    if not isinstance(overlay_files, (set, frozenset)) or not callable(exports_function):
        raise ContractError("source package overlay contract is partial")
    if not overlay_files or len(overlay_files) > 128:
        raise ContractError("source package overlay count is invalid")
    exports = exports_function()
    if not isinstance(exports, dict):
        raise ContractError("source package export contract is invalid")
    resolved: dict[str, str] = {}
    for package_path in sorted(overlay_files):
        if not isinstance(package_path, str) or not package_path:
            raise ContractError("source package overlay path is invalid")
        names = sorted(name for name, target in exports.items() if target == package_path)
        if len(names) != 1 or not isinstance(names[0], str) or FILENAME.fullmatch(names[0]) is None:
            raise ContractError(
                f"source package overlay must have one canonical export: {package_path}"
            )
        resolved[package_path] = names[0]
    if len(set(resolved.values())) != len(resolved):
        raise ContractError("source package exports are duplicated")
    return resolved


def selected_overlay_paths(
    control_root: Path, channel: str, revision: int
) -> dict[str, str]:
    tag = f"fw-packages-{channel}-{revision:03d}"
    try:
        policy = json.loads(
            (control_root / "contracts/native-build-policy.json").read_text(
                encoding="utf-8"
            )
        )
        allowed = policy["allowedOverlays"]
        selected = policy["releasePlans"][tag]["selectedOverlays"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ContractError(f"cannot load exact overlay plan for {tag}") from error
    if (
        not isinstance(allowed, dict)
        or not isinstance(selected, list)
        or not selected
        or len(set(selected)) != len(selected)
        or any(not isinstance(name, str) or name not in allowed for name in selected)
    ):
        raise ContractError(f"overlay plan is invalid for {tag}")
    return {name: allowed[name] for name in sorted(selected)}


def source_build_targets(
    source_root: Path, selected_paths: dict[str, str]
) -> tuple[str, ...]:
    """Return only fbt targets explicitly selected by the release plan."""

    exports = source_overlay_exports(source_root)
    paths = set(selected_paths.values())
    if not paths or not paths.issubset(exports):
        raise ContractError("release overlay plan differs from source package contract")
    targets = tuple(f"fap_{Path(exports[path]).stem}" for path in sorted(paths))
    if len(targets) != len(set(targets)):
        raise ContractError("selected source build targets are duplicated")
    return targets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--channel", choices=("stable", "dev"), required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument(
        "--format", choices=("lines", "shell"), default="lines"
    )
    args = parser.parse_args()
    try:
        selected = selected_overlay_paths(
            args.control_root.resolve(), args.channel, args.revision
        )
        targets = source_build_targets(args.source_root, selected)
    except ContractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(("\n" if args.format == "lines" else " ").join(targets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
