#!/usr/bin/env python3
"""Create provenance sidecars for verified byte-for-byte legacy mirrors."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from .catalog_contract import ContractError, load_json
except ImportError:  # Direct script execution.
    from catalog_contract import ContractError, load_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--publisher-repository", required=True)
    parser.add_argument("--publisher-commit", required=True)
    args = parser.parse_args()
    try:
        if re.fullmatch(r"[0-9a-f]{40}", args.publisher_commit) is None:
            raise ContractError("publisher commit must be an exact 40-character SHA")
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.publisher_repository) is None:
            raise ContractError("publisher repository must be owner/name")
        index = load_json(args.seed / "seed-index.json")
        if index.get("schema") != 1:
            raise ContractError("seed index schema must be 1")
        for channel in ("stable", "dev"):
            source = index["channels"][channel]
            document = {
                "schema": 1,
                "kind": "legacyByteMirror",
                "channel": channel,
                "publisher": {
                    "repository": args.publisher_repository,
                    "commit": args.publisher_commit,
                },
                "legacy": {
                    "repository": index["sourceRepository"],
                    "tag": source["tag"],
                    "releaseId": source["legacyReleaseId"],
                    "releaseURL": source["legacyReleaseURL"],
                    "tagCommit": source["legacyTagCommit"],
                },
                "firmwareSourceCommit": source["sourceCommit"],
                "manifestReleaseId": source["manifestReleaseId"],
                "assets": source["assets"],
            }
            path = args.seed / channel / "migration-provenance.json"
            path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (ContractError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
