#!/usr/bin/env python3
"""Download and verify exact legacy channel heads without regenerating assets."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .catalog_contract import (
        CANONICAL_PACKAGE_ASSETS,
        ContractError,
        load_json,
        sha256,
        verify_release_directory,
    )
except ImportError:  # Direct script execution.
    from catalog_contract import (
        CANONICAL_PACKAGE_ASSETS,
        ContractError,
        load_json,
        sha256,
        verify_release_directory,
    )


def run(*command: str) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ContractError(f"command failed ({' '.join(command)}): {detail}")
    return result.stdout


def release_metadata(repository: str, tag: str) -> dict[str, Any]:
    value = json.loads(run("gh", "api", f"repos/{repository}/releases/tags/{tag}"))
    if not isinstance(value, dict) or value.get("tag_name") != tag or value.get("draft"):
        raise ContractError(f"legacy release metadata is invalid for {tag}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = load_json(args.root / "contracts/legacy-sources.json")
        repository = contract["repository"]
        if args.output.exists() and any(args.output.iterdir()):
            raise ContractError("output directory must be empty")
        args.output.mkdir(parents=True, exist_ok=True)
        index: dict[str, Any] = {
            "schema": 1,
            "sourceRepository": repository,
            "channels": {},
        }
        for channel in ("stable", "dev"):
            expected = contract["channels"][channel]
            tag = expected["tag"]
            metadata = release_metadata(repository, tag)
            if metadata.get("prerelease") is not expected.get("prerelease"):
                raise ContractError(f"legacy prerelease state differs for {tag}")
            tag_reference = json.loads(
                run("gh", "api", f"repos/{repository}/git/ref/tags/{tag}")
            )
            tag_object = tag_reference.get("object")
            if (
                not isinstance(tag_object, dict)
                or tag_object.get("type") != "commit"
                or tag_object.get("sha") != expected.get("tagCommit")
            ):
                raise ContractError(f"legacy tag target differs for {tag}")
            directory = args.output / channel
            directory.mkdir()
            run(
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                repository,
                "--dir",
                str(directory),
                "--pattern",
                "tumoflip-packages*",
                "--clobber",
            )
            run(
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                repository,
                "--dir",
                str(directory),
                "--pattern",
                f"{tag}-SHA256SUMS",
                "--clobber",
            )
            verify_release_directory(directory, expected)
            remote_assets = {
                item["name"]: item
                for item in metadata.get("assets", [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            assets: dict[str, Any] = {}
            canonical_assets = (*CANONICAL_PACKAGE_ASSETS, f"{tag}-SHA256SUMS")
            for name in canonical_assets:
                path = directory / name
                if not path.is_file():
                    raise ContractError(f"legacy release {tag} lacks {name}")
                remote = remote_assets.get(name)
                if remote is None:
                    raise ContractError(f"legacy API metadata lacks {name}")
                api_digest = remote.get("digest")
                actual = sha256(path)
                if api_digest not in {None, f"sha256:{actual}"}:
                    raise ContractError(f"GitHub asset digest differs for {tag}/{name}")
                assets[name] = {
                    "bytes": path.stat().st_size,
                    "sha256": actual,
                    "githubAssetId": remote.get("id"),
                }
            index["channels"][channel] = {
                "tag": tag,
                "legacyReleaseId": metadata.get("id"),
                "legacyReleaseURL": metadata.get("html_url"),
                "legacyTagCommit": tag_object["sha"],
                "sourceCommit": expected["sourceCommit"],
                "manifestReleaseId": expected["releaseId"],
                "assets": assets,
            }
        (args.output / "seed-index.json").write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (ContractError, KeyError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"verified legacy seed: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
