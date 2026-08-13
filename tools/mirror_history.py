#!/usr/bin/env python3
"""Prepare, verify, and publish exact historical FW Packages mirrors."""

from __future__ import annotations

import argparse
import json
import re
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
    from .publish_seed import (
        _find_release,
        _list_releases,
        _publish_channel,
        default_asset_downloader,
        default_runner,
    )
except ImportError:
    from catalog_contract import (
        CANONICAL_PACKAGE_ASSETS,
        ContractError,
        load_json,
        sha256,
        verify_release_directory,
    )
    from publish_seed import (
        _find_release,
        _list_releases,
        _publish_channel,
        default_asset_downloader,
        default_runner,
    )


HEX_40 = re.compile(r"[0-9a-f]{40}")
HEX_64 = re.compile(r"[0-9a-f]{64}")


def run(*command: str) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ContractError(f"command failed ({' '.join(command)}): {detail}")
    return result.stdout


def load_contract(root: Path) -> dict[str, Any]:
    contract = load_json(root / "contracts/legacy-history.json")
    if set(contract) != {"schema", "repository", "releases"}:
        raise ContractError("legacy history contract keys differ")
    if contract.get("schema") != 1 or contract.get("repository") != "squazaryu/tumoflip":
        raise ContractError("legacy history source identity differs")
    releases = contract.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ContractError("legacy history releases must be a non-empty list")
    tags: set[str] = set()
    revisions: set[tuple[str, int]] = set()
    required = {
        "assets",
        "channel",
        "legacyGitHubReleaseId",
        "prerelease",
        "releaseId",
        "revision",
        "sourceCommit",
        "tag",
        "tagCommit",
        "targetFirmwareCommit",
        "targetFirmwareTag",
    }
    for item in releases:
        if not isinstance(item, dict) or set(item) != required:
            raise ContractError("legacy history release keys differ")
        channel = item["channel"]
        revision = item["revision"]
        tag = item["tag"]
        if channel not in {"stable", "dev"} or not isinstance(revision, int):
            raise ContractError("legacy history channel/revision is invalid")
        if tag != f"fw-packages-{channel}-{revision:03d}":
            raise ContractError(f"legacy history tag differs: {tag}")
        if tag in tags or (channel, revision) in revisions:
            raise ContractError(f"duplicate legacy history identity: {tag}")
        tags.add(tag)
        revisions.add((channel, revision))
        if item["prerelease"] is not (channel == "dev"):
            raise ContractError(f"legacy history prerelease state differs: {tag}")
        if not isinstance(item["legacyGitHubReleaseId"], int) or item["legacyGitHubReleaseId"] < 1:
            raise ContractError(f"legacy GitHub release ID is invalid: {tag}")
        for key in (item["releaseId"], *item["assets"].values()):
            if not isinstance(key, str) or HEX_64.fullmatch(key) is None:
                raise ContractError(f"legacy history digest is invalid: {tag}")
        for key in ("sourceCommit", "tagCommit", "targetFirmwareCommit"):
            if not isinstance(item[key], str) or HEX_40.fullmatch(item[key]) is None:
                raise ContractError(f"legacy history commit is invalid: {tag}/{key}")
        expected_assets = {
            *CANONICAL_PACKAGE_ASSETS,
            f"{tag}-SHA256SUMS",
        }
        if set(item["assets"]) != expected_assets:
            raise ContractError(f"legacy history assets differ: {tag}")
    return contract


def _provenance(
    contract: dict[str, Any],
    item: dict[str, Any],
    metadata: dict[str, Any],
    assets: dict[str, Any],
    publisher_repository: str,
    publisher_commit: str,
) -> dict[str, Any]:
    return {
        "schema": 1,
        "kind": "legacyByteMirror",
        "channel": item["channel"],
        "publisher": {"repository": publisher_repository, "commit": publisher_commit},
        "legacy": {
            "repository": contract["repository"],
            "tag": item["tag"],
            "releaseId": metadata["id"],
            "releaseURL": metadata["html_url"],
            "tagCommit": item["tagCommit"],
        },
        "firmwareSourceCommit": item["sourceCommit"],
        "manifestReleaseId": item["releaseId"],
        "assets": assets,
    }


def prepare(
    contract_root: Path,
    output: Path,
    publisher_repository: str,
    publisher_commit: str,
) -> None:
    if HEX_40.fullmatch(publisher_commit) is None:
        raise ContractError("publisher commit must be an exact SHA")
    if output.exists() and any(output.iterdir()):
        raise ContractError("history output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    contract = load_contract(contract_root)
    index: dict[str, Any] = {"schema": 1, "releases": {}}
    for item in contract["releases"]:
        tag = item["tag"]
        metadata = json.loads(run("gh", "api", f"repos/{contract['repository']}/releases/tags/{tag}"))
        if (
            metadata.get("id") != item["legacyGitHubReleaseId"]
            or metadata.get("tag_name") != tag
            or metadata.get("draft")
            or metadata.get("prerelease") is not item["prerelease"]
        ):
            raise ContractError(f"legacy release metadata differs: {tag}")
        reference = json.loads(run("gh", "api", f"repos/{contract['repository']}/git/ref/tags/{tag}"))
        tag_object = reference.get("object")
        if (
            not isinstance(tag_object, dict)
            or tag_object.get("sha") != item["tagCommit"]
            or tag_object.get("type") != "commit"
        ):
            raise ContractError(f"legacy tag target differs: {tag}")
        directory = output / tag
        directory.mkdir()
        run("gh", "release", "download", tag, "--repo", contract["repository"], "--dir", str(directory))
        verify_release_directory(directory, item)
        remote = {asset.get("name"): asset for asset in metadata.get("assets", [])}
        if set(remote) != set(item["assets"]):
            raise ContractError(f"legacy release asset set differs: {tag}")
        asset_evidence: dict[str, Any] = {}
        for name, expected_digest in sorted(item["assets"].items()):
            path = directory / name
            asset = remote.get(name)
            if not isinstance(asset, dict) or not path.is_file():
                raise ContractError(f"legacy asset is missing: {tag}/{name}")
            digest = sha256(path)
            if digest != expected_digest or asset.get("digest") not in {None, f"sha256:{digest}"}:
                raise ContractError(f"legacy asset digest differs: {tag}/{name}")
            asset_evidence[name] = {
                "bytes": path.stat().st_size,
                "sha256": digest,
                "githubAssetId": asset.get("id"),
            }
        document = _provenance(
            contract,
            item,
            metadata,
            asset_evidence,
            publisher_repository,
            publisher_commit,
        )
        (directory / "migration-provenance.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        index["releases"][tag] = document
    (output / "history-index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def verify(
    contract_root: Path,
    root: Path,
    publisher_repository: str,
    publisher_commit: str,
) -> None:
    contract = load_contract(contract_root)
    index = load_json(root / "history-index.json")
    if set(index) != {"schema", "releases"} or index.get("schema") != 1:
        raise ContractError("history index is invalid")
    expected_tags = {item["tag"] for item in contract["releases"]}
    if not isinstance(index.get("releases"), dict) or set(index["releases"]) != expected_tags:
        raise ContractError("history index release set differs")
    for item in contract["releases"]:
        tag = item["tag"]
        directory = root / tag
        verify_release_directory(directory, item)
        document = load_json(directory / "migration-provenance.json")
        if document != index["releases"][tag]:
            raise ContractError(f"history provenance/index differs: {tag}")
        if set(document) != {
            "schema",
            "kind",
            "channel",
            "publisher",
            "legacy",
            "firmwareSourceCommit",
            "manifestReleaseId",
            "assets",
        } or document.get("schema") != 1 or document.get("kind") != "legacyByteMirror":
            raise ContractError(f"history provenance shape differs: {tag}")
        if document.get("channel") != item["channel"]:
            raise ContractError(f"history provenance channel differs: {tag}")
        if document.get("publisher") != {
            "repository": publisher_repository,
            "commit": publisher_commit,
        }:
            raise ContractError(f"history publisher identity differs: {tag}")
        legacy = document.get("legacy")
        if not isinstance(legacy, dict) or legacy != {
            "repository": contract["repository"],
            "tag": tag,
            "releaseId": item["legacyGitHubReleaseId"],
            "releaseURL": f"https://github.com/{contract['repository']}/releases/tag/{tag}",
            "tagCommit": item["tagCommit"],
        }:
            raise ContractError(f"history legacy identity differs: {tag}")
        if document.get("firmwareSourceCommit") != item["sourceCommit"]:
            raise ContractError(f"history source commit differs: {tag}")
        if document.get("manifestReleaseId") != item["releaseId"]:
            raise ContractError(f"history manifest release ID differs: {tag}")
        evidence = document.get("assets")
        if not isinstance(evidence, dict) or set(evidence) != set(item["assets"]):
            raise ContractError(f"history asset evidence differs: {tag}")
        for name, expected_digest in item["assets"].items():
            path = directory / name
            record = evidence[name]
            if not isinstance(record, dict) or set(record) != {
                "bytes",
                "sha256",
                "githubAssetId",
            } or not isinstance(record.get("githubAssetId"), int):
                raise ContractError(f"history asset evidence shape differs: {tag}/{name}")
            if (
                record.get("sha256") != expected_digest
                or record.get("bytes") != path.stat().st_size
                or sha256(path) != expected_digest
            ):
                raise ContractError(f"history asset proof differs: {tag}/{name}")


def publish(
    contract_root: Path,
    root: Path,
    repository: str,
    publisher_commit: str,
) -> None:
    verify(contract_root, root, repository, publisher_commit)
    contract = load_contract(contract_root)
    releases = _list_releases(default_runner, repository)
    for item in contract["releases"]:
        _publish_channel(
            default_runner,
            default_asset_downloader,
            root,
            item,
            repository,
            publisher_commit,
            item["channel"],
            _find_release(releases, item["tag"]),
            local_key=item["tag"],
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "verify", "publish"))
    parser.add_argument("--contract-root", type=Path, default=Path("."))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--publisher-repository", required=True)
    parser.add_argument("--publisher-commit", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.contract_root, args.root, args.publisher_repository, args.publisher_commit)
        elif args.command == "verify":
            verify(args.contract_root, args.root, args.publisher_repository, args.publisher_commit)
        else:
            publish(args.contract_root, args.root, args.publisher_repository, args.publisher_commit)
    except (ContractError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
