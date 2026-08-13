#!/usr/bin/env python3
"""Idempotently publish and reverify exact legacy mirror channel heads."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from .catalog_contract import (
        ContractError,
        load_json,
        sha256,
        verify_migration_provenance,
        verify_release_directory,
    )
except ImportError:  # Direct script execution.
    from catalog_contract import (
        ContractError,
        load_json,
        sha256,
        verify_migration_provenance,
        verify_release_directory,
    )


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _run(runner: Runner, command: Sequence[str]) -> str:
    result = runner(command)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ContractError(f"command failed ({' '.join(command)}): {detail}")
    return result.stdout


def _release_or_none(runner: Runner, repository: str, tag: str) -> dict[str, Any] | None:
    command = ("gh", "api", f"repos/{repository}/releases/tags/{tag}")
    result = runner(command)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        if "HTTP 404" in detail or "Not Found" in detail:
            return None
        raise ContractError(f"cannot query release {tag}: {detail}")
    try:
        value = json.loads(result.stdout)
    except ValueError as error:
        raise ContractError(f"invalid GitHub release response for {tag}") from error
    if not isinstance(value, dict):
        raise ContractError(f"GitHub release response for {tag} is not an object")
    return value


def _canonical_names(tag: str) -> set[str]:
    return {
        "tumoflip-packages.json",
        "tumoflip-packages.zip",
        f"{tag}-SHA256SUMS",
        "migration-provenance.json",
    }


def _verify_published_channel(
    runner: Runner,
    seed_root: Path,
    legacy_contract: dict[str, Any],
    repository: str,
    publisher_commit: str,
    channel: str,
) -> None:
    expected = legacy_contract["channels"][channel]
    tag = expected["tag"]
    release = _release_or_none(runner, repository, tag)
    if release is None:
        raise ContractError(f"release disappeared after publication: {tag}")
    if release.get("tag_name") != tag or release.get("draft") is not False:
        raise ContractError(f"published release metadata is invalid: {tag}")
    if release.get("prerelease") is not expected["prerelease"]:
        raise ContractError(f"published prerelease state differs: {tag}")
    assets = release.get("assets")
    if not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets):
        raise ContractError(f"published release assets are invalid: {tag}")
    asset_map = {item.get("name"): item for item in assets}
    if (
        None in asset_map
        or len(assets) != len(asset_map)
        or set(asset_map) != _canonical_names(tag)
    ):
        raise ContractError(f"published release asset names differ: {tag}")

    reference = json.loads(
        _run(runner, ("gh", "api", f"repos/{repository}/git/ref/tags/{tag}"))
    )
    tag_object = reference.get("object") if isinstance(reference, dict) else None
    if (
        not isinstance(tag_object, dict)
        or tag_object.get("type") != "commit"
        or tag_object.get("sha") != publisher_commit
    ):
        raise ContractError(f"published tag target differs: {tag}")

    local = seed_root / channel
    with tempfile.TemporaryDirectory(prefix=f"verify-{tag}-") as temporary:
        remote = Path(temporary)
        _run(
            runner,
            (
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                repository,
                "--dir",
                str(remote),
                "--clobber",
            ),
        )
        remote_names = {path.name for path in remote.iterdir() if path.is_file()}
        if remote_names != _canonical_names(tag):
            raise ContractError(f"downloaded release asset names differ: {tag}")
        verify_release_directory(remote, expected)
        for name in sorted(_canonical_names(tag)):
            local_path = local / name
            remote_path = remote / name
            if not local_path.is_file() or local_path.stat().st_size != remote_path.stat().st_size:
                raise ContractError(f"published asset size differs: {tag}/{name}")
            digest = sha256(local_path)
            if digest != sha256(remote_path):
                raise ContractError(f"published asset bytes differ: {tag}/{name}")
            metadata = asset_map[name]
            if metadata.get("size") != local_path.stat().st_size:
                raise ContractError(f"GitHub asset size differs: {tag}/{name}")
            api_digest = metadata.get("digest")
            if api_digest not in {None, f"sha256:{digest}"}:
                raise ContractError(f"GitHub asset digest differs: {tag}/{name}")


def _create_channel(
    runner: Runner,
    seed_root: Path,
    repository: str,
    publisher_commit: str,
    channel: str,
    tag: str,
) -> None:
    directory = seed_root / channel
    command: list[str] = [
        "gh",
        "release",
        "create",
        tag,
        *(str(directory / name) for name in sorted(_canonical_names(tag))),
        "--repo",
        repository,
        "--target",
        publisher_commit,
        "--title",
        f"FW Packages {channel} {tag.rsplit('-', 1)[-1]} (legacy mirror)",
        "--notes",
        "Byte-for-byte migration mirror from squazaryu/tumoflip. See migration-provenance.json.",
    ]
    if channel == "dev":
        command.append("--prerelease")
    _run(runner, command)


def publish_seed(
    seed_root: Path,
    contract_root: Path,
    repository: str,
    publisher_commit: str,
    runner: Runner = default_runner,
) -> None:
    legacy = load_json(contract_root / "contracts/legacy-sources.json")
    verify_migration_provenance(seed_root, legacy, repository, publisher_commit)
    for channel in ("stable", "dev"):
        expected = legacy["channels"][channel]
        verify_release_directory(seed_root / channel, expected)
        tag = expected["tag"]
        if _release_or_none(runner, repository, tag) is None:
            _create_channel(runner, seed_root, repository, publisher_commit, channel, tag)
        _verify_published_channel(
            runner,
            seed_root,
            legacy,
            repository,
            publisher_commit,
            channel,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--contract-root", type=Path, default=Path("."))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--publisher-commit", required=True)
    args = parser.parse_args()
    try:
        publish_seed(
            args.seed,
            args.contract_root,
            args.repository,
            args.publisher_commit,
        )
    except (ContractError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
