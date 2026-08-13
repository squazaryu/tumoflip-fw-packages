#!/usr/bin/env python3
"""Atomically publish and reverify exact legacy mirror channel heads."""

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
AssetDownloader = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
RELEASE_NOTES = (
    "Byte-for-byte migration mirror from squazaryu/tumoflip. "
    "See migration-provenance.json."
)


def default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def default_asset_downloader(
    command: Sequence[str], destination: Path
) -> subprocess.CompletedProcess[str]:
    try:
        with destination.open("wb") as stream:
            result = subprocess.run(
                command,
                check=False,
                stdout=stream,
                stderr=subprocess.PIPE,
            )
    except OSError as error:
        return subprocess.CompletedProcess(command, 1, "", str(error))
    stderr = result.stderr.decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(command, result.returncode, "", stderr)


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    return stderr.strip() or stdout.strip() or f"exit {result.returncode}"


def _run(runner: Runner, command: Sequence[str]) -> str:
    result = runner(command)
    if result.returncode != 0:
        raise ContractError(f"command failed ({' '.join(command)}): {_detail(result)}")
    return result.stdout


def _json_object(payload: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except ValueError as error:
        raise ContractError(f"invalid GitHub response for {label}") from error
    if not isinstance(value, dict):
        raise ContractError(f"GitHub response for {label} is not an object")
    return value


def _release_title(channel: str, tag: str) -> str:
    return f"FW Packages {channel} {tag.rsplit('-', 1)[-1]} (legacy mirror)"


def _canonical_names(tag: str) -> set[str]:
    return {
        "tumoflip-packages.json",
        "tumoflip-packages.zip",
        f"{tag}-SHA256SUMS",
        "migration-provenance.json",
    }


def _find_release(runner: Runner, repository: str, tag: str) -> dict[str, Any] | None:
    payload = _run(
        runner,
        (
            "gh",
            "api",
            f"repos/{repository}/releases?per_page=100",
            "--paginate",
            "--slurp",
        ),
    )
    try:
        pages = json.loads(payload)
    except ValueError as error:
        raise ContractError("invalid GitHub release-list response") from error
    if not isinstance(pages, list):
        raise ContractError("GitHub release-list response is not an array")
    releases: list[Any] = []
    if all(isinstance(item, dict) for item in pages):
        releases = pages
    else:
        for page in pages:
            if not isinstance(page, list):
                raise ContractError("GitHub paginated release response is invalid")
            releases.extend(page)
    if not all(isinstance(item, dict) for item in releases):
        raise ContractError("GitHub release-list item is invalid")
    matches = [item for item in releases if item.get("tag_name") == tag]
    if len(matches) > 1:
        raise ContractError(f"multiple GitHub releases use tag {tag}")
    return matches[0] if matches else None


def _release_by_id(runner: Runner, repository: str, release_id: int) -> dict[str, Any]:
    return _json_object(
        _run(runner, ("gh", "api", f"repos/{repository}/releases/{release_id}")),
        f"release {release_id}",
    )


def _release_id(release: dict[str, Any], tag: str) -> int:
    value = release.get("id")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError(f"GitHub release ID is invalid: {tag}")
    return value


def _validate_release_metadata(
    release: dict[str, Any],
    publisher_commit: str,
    channel: str,
    tag: str,
    prerelease: bool,
    draft: bool,
) -> None:
    _release_id(release, tag)
    expected = {
        "tag_name": tag,
        "target_commitish": publisher_commit,
        "name": _release_title(channel, tag),
        "body": RELEASE_NOTES,
        "draft": draft,
        "prerelease": prerelease,
    }
    for key, value in expected.items():
        if release.get(key) != value:
            raise ContractError(f"release metadata differs: {tag}/{key}")


def _tag_target_or_none(runner: Runner, repository: str, tag: str) -> str | None:
    command = ("gh", "api", f"repos/{repository}/git/ref/tags/{tag}")
    result = runner(command)
    if result.returncode != 0:
        detail = _detail(result)
        if "HTTP 404" in detail or "Not Found" in detail:
            return None
        raise ContractError(f"cannot query tag {tag}: {detail}")
    reference = _json_object(result.stdout, f"tag {tag}")
    tag_object = reference.get("object")
    if not isinstance(tag_object, dict) or tag_object.get("type") != "commit":
        raise ContractError(f"release tag is not a lightweight commit tag: {tag}")
    target = tag_object.get("sha")
    if not isinstance(target, str):
        raise ContractError(f"release tag target is invalid: {tag}")
    return target


def _require_tag_target(
    runner: Runner,
    repository: str,
    tag: str,
    publisher_commit: str,
    *,
    allow_missing: bool,
) -> None:
    target = _tag_target_or_none(runner, repository, tag)
    if target is None and allow_missing:
        return
    if target != publisher_commit:
        raise ContractError(f"release tag target differs: {tag}")


def _asset_map(release: dict[str, Any], tag: str) -> dict[str, dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets):
        raise ContractError(f"release assets are invalid: {tag}")
    result: dict[str, dict[str, Any]] = {}
    for item in assets:
        name = item.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise ContractError(f"release asset names are invalid or duplicated: {tag}")
        result[name] = item
    unexpected = set(result) - _canonical_names(tag)
    if unexpected:
        raise ContractError(f"release contains unexpected asset: {tag}/{sorted(unexpected)[0]}")
    return result


def _download_asset(
    downloader: AssetDownloader,
    repository: str,
    tag: str,
    asset: dict[str, Any],
    destination: Path,
) -> None:
    asset_id = asset.get("id")
    if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id < 1:
        raise ContractError(f"GitHub asset ID is invalid: {tag}/{asset.get('name')}")
    result = downloader(
        (
            "gh",
            "api",
            "-H",
            "Accept: application/octet-stream",
            f"repos/{repository}/releases/assets/{asset_id}",
        ),
        destination,
    )
    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        raise ContractError(f"cannot download {tag}/{asset.get('name')}: {_detail(result)}")


def _verify_remote_assets(
    downloader: AssetDownloader,
    seed_root: Path,
    expected_contract: dict[str, Any],
    repository: str,
    channel: str,
    release: dict[str, Any],
    *,
    require_complete: bool,
) -> set[str]:
    tag = expected_contract["tag"]
    assets = _asset_map(release, tag)
    expected_names = _canonical_names(tag)
    missing = expected_names - set(assets)
    if require_complete and missing:
        raise ContractError(f"release is missing asset: {tag}/{sorted(missing)[0]}")

    local = seed_root / channel
    with tempfile.TemporaryDirectory(prefix=f"verify-{tag}-") as temporary:
        remote = Path(temporary)
        for name, metadata in sorted(assets.items()):
            local_path = local / name
            if not local_path.is_file():
                raise ContractError(f"local seed asset is missing: {tag}/{name}")
            size = local_path.stat().st_size
            digest = sha256(local_path)
            if metadata.get("size") != size:
                raise ContractError(f"GitHub asset size differs: {tag}/{name}")
            api_digest = metadata.get("digest")
            if api_digest not in {None, f"sha256:{digest}"}:
                raise ContractError(f"GitHub asset digest differs: {tag}/{name}")
            remote_path = remote / name
            _download_asset(downloader, repository, tag, metadata, remote_path)
            if remote_path.stat().st_size != size or sha256(remote_path) != digest:
                raise ContractError(f"release asset bytes differ: {tag}/{name}")
        if not missing:
            verify_release_directory(remote, expected_contract)
    return missing


def _create_draft(
    runner: Runner,
    repository: str,
    publisher_commit: str,
    channel: str,
    tag: str,
    prerelease: bool,
) -> dict[str, Any]:
    command: list[str] = [
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        repository,
        "--target",
        publisher_commit,
        "--title",
        _release_title(channel, tag),
        "--notes",
        RELEASE_NOTES,
        "--draft",
    ]
    if prerelease:
        command.append("--prerelease")
    _run(runner, command)
    release = _find_release(runner, repository, tag)
    if release is None:
        raise ContractError(f"created draft cannot be found: {tag}")
    _validate_release_metadata(
        release, publisher_commit, channel, tag, prerelease, draft=True
    )
    return release


def _upload_missing_assets(
    runner: Runner,
    seed_root: Path,
    repository: str,
    channel: str,
    tag: str,
    missing: set[str],
) -> None:
    if not missing:
        return
    directory = seed_root / channel
    _run(
        runner,
        (
            "gh",
            "release",
            "upload",
            tag,
            *(str(directory / name) for name in sorted(missing)),
            "--repo",
            repository,
        ),
    )


def _publish_draft(
    runner: Runner,
    repository: str,
    release_id: int,
    publisher_commit: str,
    channel: str,
    tag: str,
    prerelease: bool,
) -> dict[str, Any]:
    _run(
        runner,
        (
            "gh",
            "release",
            "edit",
            tag,
            "--repo",
            repository,
            "--target",
            publisher_commit,
            "--title",
            _release_title(channel, tag),
            "--notes",
            RELEASE_NOTES,
            "--draft=false",
            f"--prerelease={str(prerelease).lower()}",
            "--latest=false",
        ),
    )
    return _release_by_id(runner, repository, release_id)


def _verify_published_channel(
    runner: Runner,
    downloader: AssetDownloader,
    seed_root: Path,
    expected: dict[str, Any],
    repository: str,
    publisher_commit: str,
    channel: str,
    release: dict[str, Any],
) -> None:
    tag = expected["tag"]
    _validate_release_metadata(
        release,
        publisher_commit,
        channel,
        tag,
        expected["prerelease"],
        draft=False,
    )
    _verify_remote_assets(
        downloader,
        seed_root,
        expected,
        repository,
        channel,
        release,
        require_complete=True,
    )
    _require_tag_target(
        runner, repository, tag, publisher_commit, allow_missing=False
    )


def _publish_channel(
    runner: Runner,
    downloader: AssetDownloader,
    seed_root: Path,
    expected: dict[str, Any],
    repository: str,
    publisher_commit: str,
    channel: str,
) -> None:
    tag = expected["tag"]
    prerelease = expected["prerelease"]
    release = _find_release(runner, repository, tag)
    if release is not None and release.get("draft") is False:
        _verify_published_channel(
            runner,
            downloader,
            seed_root,
            expected,
            repository,
            publisher_commit,
            channel,
            release,
        )
        return
    if release is None:
        release = _create_draft(
            runner, repository, publisher_commit, channel, tag, prerelease
        )

    release_id = _release_id(release, tag)
    release = _release_by_id(runner, repository, release_id)
    _validate_release_metadata(
        release, publisher_commit, channel, tag, prerelease, draft=True
    )
    _require_tag_target(
        runner, repository, tag, publisher_commit, allow_missing=True
    )
    missing = _verify_remote_assets(
        downloader,
        seed_root,
        expected,
        repository,
        channel,
        release,
        require_complete=False,
    )
    _upload_missing_assets(runner, seed_root, repository, channel, tag, missing)

    release = _release_by_id(runner, repository, release_id)
    _validate_release_metadata(
        release, publisher_commit, channel, tag, prerelease, draft=True
    )
    _require_tag_target(
        runner, repository, tag, publisher_commit, allow_missing=True
    )
    _verify_remote_assets(
        downloader,
        seed_root,
        expected,
        repository,
        channel,
        release,
        require_complete=True,
    )

    release = _publish_draft(
        runner,
        repository,
        release_id,
        publisher_commit,
        channel,
        tag,
        prerelease,
    )
    _verify_published_channel(
        runner,
        downloader,
        seed_root,
        expected,
        repository,
        publisher_commit,
        channel,
        release,
    )


def publish_seed(
    seed_root: Path,
    contract_root: Path,
    repository: str,
    publisher_commit: str,
    runner: Runner = default_runner,
    downloader: AssetDownloader = default_asset_downloader,
) -> None:
    legacy = load_json(contract_root / "contracts/legacy-sources.json")
    verify_migration_provenance(seed_root, legacy, repository, publisher_commit)
    for channel in ("stable", "dev"):
        expected = legacy["channels"][channel]
        verify_release_directory(seed_root / channel, expected)
        _publish_channel(
            runner,
            downloader,
            seed_root,
            expected,
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
