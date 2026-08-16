#!/usr/bin/env python3
"""Reverify a native release after an artifact or job boundary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .catalog_contract import ContractError
    from .native_release import load_native_plan, verify_native_release
except ImportError:  # Direct script execution.
    from catalog_contract import ContractError
    from native_release import load_native_plan, verify_native_release


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", type=Path, default=Path("."))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--base-directory", type=Path)
    parser.add_argument("--target-directory", type=Path)
    parser.add_argument("--channel", choices=("stable", "dev"), required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--publisher-commit", required=True)
    args = parser.parse_args()
    try:
        plan = load_native_plan(
            args.control_root.resolve(),
            args.channel,
            args.revision,
            args.source_commit,
            args.publisher_commit,
        )
        verify_native_release(
            args.directory.resolve(),
            plan,
            args.base_directory.resolve() if args.base_directory else None,
            args.target_directory.resolve() if args.target_directory else None,
        )
    except (ContractError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"verified native release: {plan['tag']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
