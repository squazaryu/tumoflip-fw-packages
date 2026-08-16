#!/usr/bin/env python3
"""Resumably publish one already-verified native FW Packages release.

This module is deliberately not called by the production workflow yet. Its
publication transaction is draft -> upload missing -> byte verification ->
publish by release ID -> byte verification. Existing public or draft bytes are
never overwritten.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import quote

try:
    from .catalog_contract import ContractError, load_json, sha256
    from .native_release import PROVENANCE_NAME, load_native_plan, verify_native_release
except ImportError:  # Direct script execution.
    from catalog_contract import ContractError, load_json, sha256
    from native_release import PROVENANCE_NAME, load_native_plan, verify_native_release


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Downloader = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
Sleeper = Callable[[float], None]
RELEASE_NOTES = (
    "Independent Tumoflip FW Packages release. Firmware flash assets are unchanged. "
    "See catalog-provenance.json for exact source and publisher commits."
)


def default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def default_downloader(
    command: Sequence[str], destination: Path
) -> subprocess.CompletedProcess[str]:
    try:
        with destination.open("wb") as stream:
            result = subprocess.run(
                command, check=False, stdout=stream, stderr=subprocess.PIPE
            )
    except OSError as error:
        return subprocess.CompletedProcess(command, 1, "", str(error))
    return subprocess.CompletedProcess(
        command,
        result.returncode,
        "",
        result.stderr.decode("utf-8", errors="replace"),
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout).strip() or f"exit {result.returncode}"


def _run(runner: Runner, command: Sequence[str]) -> str:
    result = runner(command)
    if result.returncode != 0:
        raise ContractError(f"command failed ({' '.join(command)}): {_detail(result)}")
    return result.stdout


def _json(payload: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except ValueError as error:
        raise ContractError(f"invalid GitHub response for {label}") from error
    if not isinstance(value, dict):
        raise ContractError(f"GitHub response for {label} is not an object")
    return value


def _title(plan: dict[str, Any]) -> str:
    return f"FW Packages {plan['channel']} {plan['revision']:03d}"


def _asset_names(plan: dict[str, Any]) -> set[str]:
    return {
        "tumoflip-packages.json",
        "tumoflip-packages.zip",
        f"{plan['tag']}-SHA256SUMS",
        PROVENANCE_NAME,
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
    matches = [item for item in releases if isinstance(item, dict) and item.get("tag_name") == tag]
    if len(matches) > 1:
        raise ContractError(f"multiple GitHub releases use tag {tag}")
    return matches[0] if matches else None


def _release_by_id(runner: Runner, repository: str, release_id: int) -> dict[str, Any]:
    return _json(
        _run(runner, ("gh", "api", f"repos/{repository}/releases/{release_id}")),
        f"release {release_id}",
    )


def _release_id(release: dict[str, Any], tag: str) -> int:
    value = release.get("id")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError(f"GitHub release ID is invalid: {tag}")
    return value


def _validate_metadata(
    release: dict[str, Any], plan: dict[str, Any], *, draft: bool
) -> None:
    _release_id(release, plan["tag"])
    expected = {
        "tag_name": plan["tag"],
        "target_commitish": plan["publisherCommit"],
        "name": _title(plan),
        "body": RELEASE_NOTES,
        "draft": draft,
        "prerelease": plan["prerelease"],
    }
    for key, value in expected.items():
        if release.get(key) != value:
            raise ContractError(f"release metadata differs: {plan['tag']}/{key}")


def _asset_map(release: dict[str, Any], plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets):
        raise ContractError(f"release assets are invalid: {plan['tag']}")
    result: dict[str, dict[str, Any]] = {}
    for item in assets:
        name = item.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise ContractError(f"release asset names are invalid: {plan['tag']}")
        result[name] = item
    unexpected = set(result) - _asset_names(plan)
    if unexpected:
        raise ContractError(f"release contains unexpected asset: {sorted(unexpected)[0]}")
    return result


def _download(
    downloader: Downloader,
    repository: str,
    asset: dict[str, Any],
    destination: Path,
) -> None:
    asset_id = asset.get("id")
    if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id < 1:
        raise ContractError("GitHub asset ID is invalid")
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
        raise ContractError(f"cannot download release asset: {_detail(result)}")


def _verify_assets(
    downloader: Downloader,
    directory: Path,
    repository: str,
    plan: dict[str, Any],
    release: dict[str, Any],
    *,
    complete: bool,
) -> set[str]:
    assets = _asset_map(release, plan)
    missing = _asset_names(plan) - set(assets)
    if complete and missing:
        raise ContractError(f"release is missing asset: {sorted(missing)[0]}")
    with tempfile.TemporaryDirectory(prefix=f"verify-{plan['tag']}-") as temporary:
        remote = Path(temporary)
        for name, metadata in sorted(assets.items()):
            local = directory / name
            if not local.is_file():
                raise ContractError(f"local release asset is missing: {name}")
            digest = sha256(local)
            if metadata.get("size") != local.stat().st_size:
                raise ContractError(f"GitHub asset size differs: {name}")
            api_digest = metadata.get("digest")
            if api_digest not in {None, f"sha256:{digest}"}:
                raise ContractError(f"GitHub asset digest differs: {name}")
            destination = remote / name
            _download(downloader, repository, metadata, destination)
            if destination.stat().st_size != local.stat().st_size or sha256(destination) != digest:
                raise ContractError(f"release asset bytes differ: {name}")
    return missing


def _tag_target(runner: Runner, repository: str, tag: str) -> str | None:
    result = runner(("gh", "api", f"repos/{repository}/git/ref/tags/{tag}"))
    if result.returncode != 0:
        detail = _detail(result)
        if "404" in detail or "Not Found" in detail:
            return None
        raise ContractError(f"cannot query release tag: {detail}")
    value = _json(result.stdout, f"tag {tag}").get("object")
    if not isinstance(value, dict) or value.get("type") != "commit":
        raise ContractError("release tag is not a lightweight commit tag")
    target = value.get("sha")
    if not isinstance(target, str):
        raise ContractError("release tag target is invalid")
    return target


def _create_draft(runner: Runner, repository: str, plan: dict[str, Any]) -> dict[str, Any]:
    if _tag_target(runner, repository, plan["tag"]) is not None:
        raise ContractError("release tag already exists without a matching release")
    release = _json(
        _run(
            runner,
            (
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{repository}/releases",
                "-f",
                f"tag_name={plan['tag']}",
                "-f",
                f"target_commitish={plan['publisherCommit']}",
                "-f",
                f"name={_title(plan)}",
                "-f",
                f"body={RELEASE_NOTES}",
                "-F",
                "draft=true",
                "-F",
                f"prerelease={str(plan['prerelease']).lower()}",
            ),
        ),
        "created draft",
    )
    _validate_metadata(release, plan, draft=True)
    return release


def _upload_missing(
    runner: Runner,
    directory: Path,
    repository: str,
    plan: dict[str, Any],
    release_id: int,
    missing: set[str],
) -> None:
    for name in sorted(missing):
        path = directory / name
        asset = _json(
            _run(
                runner,
                (
                    "gh",
                    "api",
                    (
                        f"https://uploads.github.com/repos/{repository}/releases/"
                        f"{release_id}/assets?name={quote(name, safe='')}"
                    ),
                    "--method",
                    "POST",
                    "--header",
                    "Content-Type: application/octet-stream",
                    "--input",
                    str(path),
                ),
            ),
            f"uploaded asset {name}",
        )
        if asset.get("name") != name or asset.get("size") != path.stat().st_size:
            raise ContractError(f"uploaded asset metadata differs: {name}")


def _publish(
    runner: Runner,
    repository: str,
    plan: dict[str, Any],
    release_id: int,
) -> dict[str, Any]:
    release = _json(
        _run(
            runner,
            (
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{repository}/releases/{release_id}",
                "-F",
                "draft=false",
                "-F",
                f"prerelease={str(plan['prerelease']).lower()}",
                "-f",
                "make_latest=false",
            ),
        ),
        "published release",
    )
    _validate_metadata(release, plan, draft=False)
    return release


def _wait_for_exact_tag(
    runner: Runner,
    repository: str,
    tag: str,
    expected_commit: str,
    sleeper: Sleeper,
    attempts: int = 5,
) -> None:
    for attempt in range(attempts):
        target = _tag_target(runner, repository, tag)
        if target == expected_commit:
            return
        if target is not None:
            raise ContractError("published release tag target differs")
        if attempt + 1 < attempts:
            sleeper(1.0)
    raise ContractError("published release tag did not become visible")


def publish_native(
    directory: Path,
    base_directory: Path,
    control_root: Path,
    repository: str,
    channel: str,
    revision: int,
    source_commit: str,
    publisher_commit: str,
    runner: Runner = default_runner,
    downloader: Downloader = default_downloader,
    sleeper: Sleeper = time.sleep,
    target_directory: Path | None = None,
) -> None:
    plan = load_native_plan(
        control_root, channel, revision, source_commit, publisher_commit
    )
    if repository != plan["publisherRepository"]:
        raise ContractError("publication repository differs from contract")
    # Re-prove the bounded delta from the pinned predecessor after the artifact
    # crosses into the privileged publication boundary.
    verify_native_release(directory, plan, base_directory, target_directory)
    release = _find_release(runner, repository, plan["tag"])
    if release is not None and release.get("draft") is False:
        _validate_metadata(release, plan, draft=False)
        _verify_assets(downloader, directory, repository, plan, release, complete=True)
        if _tag_target(runner, repository, plan["tag"]) != publisher_commit:
            raise ContractError("release tag target differs")
        return
    if release is None:
        release = _create_draft(runner, repository, plan)
    else:
        _validate_metadata(release, plan, draft=True)
        if _tag_target(runner, repository, plan["tag"]) is not None:
            raise ContractError("draft release unexpectedly has a tag reference")
    release_id = _release_id(release, plan["tag"])
    missing = _verify_assets(
        downloader, directory, repository, plan, release, complete=False
    )
    _upload_missing(runner, directory, repository, plan, release_id, missing)
    release = _release_by_id(runner, repository, release_id)
    _validate_metadata(release, plan, draft=True)
    _verify_assets(downloader, directory, repository, plan, release, complete=True)
    release = _publish(runner, repository, plan, release_id)
    _verify_assets(downloader, directory, repository, plan, release, complete=True)
    _wait_for_exact_tag(
        runner, repository, plan["tag"], publisher_commit, sleeper
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--directory", type=Path, required=True)
    value.add_argument("--base-directory", type=Path, required=True)
    value.add_argument("--target-directory", type=Path)
    value.add_argument("--control-root", type=Path, default=Path("."))
    value.add_argument("--repository", required=True)
    value.add_argument("--channel", choices=("stable", "dev"), required=True)
    value.add_argument("--revision", type=int, required=True)
    value.add_argument("--source-commit", required=True)
    value.add_argument("--publisher-commit", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        publish_native(
            args.directory.resolve(),
            args.base_directory.resolve(),
            args.control_root.resolve(),
            args.repository,
            args.channel,
            args.revision,
            args.source_commit,
            args.publisher_commit,
            target_directory=(
                args.target_directory.resolve() if args.target_directory else None
            ),
        )
    except (ContractError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"published native release: fw-packages-{args.channel}-{args.revision:03d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
