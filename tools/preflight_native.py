#!/usr/bin/env python3
"""Authorize native-release inputs before any source checkout or build."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .catalog_contract import ContractError
    from .native_release import load_native_plan
except ImportError:  # Direct script execution.
    from catalog_contract import ContractError
    from native_release import load_native_plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", type=Path, default=Path("."))
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
    except (ContractError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"authorized native release: {plan['tag']} @ {plan['sourceCommit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
