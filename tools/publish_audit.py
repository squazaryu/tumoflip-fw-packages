#!/usr/bin/env python3
"""Resumably publish one exact immutable protected-app audit release."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import quote

try:
    from .audit_chain import (
        ChainError,
        allocate_tag,
        bootstrap_identity,
        resolve_remote_chain,
    )
    from .audit_release import ASSET_NAMES, AuditReleaseError, sha256, verify_release
except ImportError:  # Direct script execution.
    from audit_chain import ChainError, allocate_tag, bootstrap_identity, resolve_remote_chain
    from audit_release import ASSET_NAMES, AuditReleaseError, sha256, verify_release


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Downloader = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
RELEASE_NOTES = (
    "Immutable cumulative Protected Apps audit ledger. "
    "Exact control, source, package, firmware, and issue provenance is in audit-provenance.json."
)


class PublishError(RuntimeError):
    """Raised when remote release state is ambiguous, mutable, or changed."""


def default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def default_downloader(command: Sequence[str], destination: Path) -> subprocess.CompletedProcess[str]:
    with destination.open("wb") as stream:
        result = subprocess.run(command, check=False, stdout=stream, stderr=subprocess.PIPE)
    return subprocess.CompletedProcess(
        command,
        result.returncode,
        "",
        result.stderr.decode("utf-8", errors="replace"),
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or "").strip() or (result.stdout or "").strip() or f"exit {result.returncode}"


def _run(runner: Runner, command: Sequence[str]) -> str:
    result = runner(command)
    if result.returncode != 0:
        raise PublishError(f"command failed ({' '.join(command)}): {_detail(result)}")
    return result.stdout


def _object(payload: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except ValueError as error:
        raise PublishError(f"invalid GitHub response for {label}") from error
    if not isinstance(value, dict):
        raise PublishError(f"GitHub response is not an object for {label}")
    return value


def _title(tag: str) -> str:
    return f"Protected App Audit {tag}"


def _require_immutable_releases_enabled(runner: Runner, repository: str) -> None:
    result = runner(
        (
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2026-03-10",
            f"repos/{repository}/immutable-releases",
        )
    )
    if result.returncode != 0:
        raise PublishError(
            "cannot prove immutable releases are enabled before mutation: "
            f"{_detail(result)}"
        )
    value = _object(result.stdout, "immutable release setting")
    if value.get("enabled") is not True:
        raise PublishError("immutable releases are not enabled")


def _verify_live_chain_state(
    *,
    local_verified: dict[str, Any],
    repository: str,
    tag: str,
    bootstrap_index: Path,
    bootstrap_ledger: Path,
    runner: Runner,
    downloader: Downloader,
) -> bool:
    """Return True only when this exact release is already the verified head."""
    with tempfile.TemporaryDirectory(prefix="audit-publisher-chain-") as temporary:
        try:
            chain = resolve_remote_chain(
                repository=repository,
                bootstrap_index=bootstrap_index,
                bootstrap_ledger=bootstrap_ledger,
                root=Path(temporary),
                runner=runner,
                downloader=downloader,
            )
        except ChainError as error:
            raise PublishError(str(error)) from error
        matches = [item for item in chain if item.tag == tag]
        if matches:
            current = matches[0]
            if current is not chain[-1]:
                raise PublishError("requested audit release is not the immutable chain head")
            for key in ("ledgerSHA256", "provenanceSHA256", "predecessor"):
                if local_verified.get(key) != current.verified.get(key):
                    raise PublishError(f"local audit release differs from chain head: {key}")
            return True
        predecessor = (
            chain[-1].predecessor_identity()
            if chain
            else bootstrap_identity(bootstrap_index, bootstrap_ledger)
        )
        if local_verified.get("predecessor") != predecessor:
            raise PublishError("audit release predecessor differs from immutable chain head")
        expected_tag = allocate_tag(local_verified["audit"]["sourceTag"], chain)
        if tag != expected_tag:
            raise PublishError(
                f"audit release tag is not the next chain revision: expected {expected_tag}"
            )
        return False


def _list_releases(runner: Runner, repository: str) -> list[dict[str, Any]]:
    payload = _run(
        runner,
        ("gh", "api", f"repos/{repository}/releases?per_page=100", "--paginate", "--slurp"),
    )
    try:
        pages = json.loads(payload)
    except ValueError as error:
        raise PublishError("invalid GitHub release list") from error
    if not isinstance(pages, list):
        raise PublishError("GitHub release list is not an array")
    releases: list[Any] = []
    if all(isinstance(item, dict) for item in pages):
        releases = pages
    else:
        for page in pages:
            if not isinstance(page, list):
                raise PublishError("GitHub paginated release response is invalid")
            releases.extend(page)
    if not all(isinstance(item, dict) for item in releases):
        raise PublishError("GitHub release item is invalid")
    return releases


def _find_release(releases: list[dict[str, Any]], tag: str) -> dict[str, Any] | None:
    matches = [item for item in releases if item.get("tag_name") == tag]
    if len(matches) > 1:
        raise PublishError(f"multiple releases use tag {tag}")
    return matches[0] if matches else None


def _release_id(release: dict[str, Any], tag: str) -> int:
    value = release.get("id")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PublishError(f"release ID is invalid: {tag}")
    return value


def _release_by_id(runner: Runner, repository: str, release_id: int) -> dict[str, Any]:
    return _object(
        _run(runner, ("gh", "api", f"repos/{repository}/releases/{release_id}")),
        f"release {release_id}",
    )


def _validate_metadata(
    release: dict[str, Any], tag: str, publisher_commit: str, *, draft: bool
) -> None:
    expected = {
        "tag_name": tag,
        "target_commitish": publisher_commit,
        "name": _title(tag),
        "body": RELEASE_NOTES,
        "draft": draft,
        "prerelease": False,
    }
    for key, value in expected.items():
        if release.get(key) != value:
            raise PublishError(f"release metadata differs: {tag}/{key}")
    if not draft and release.get("immutable") is not True:
        raise PublishError(f"public audit release is not immutable: {tag}")


def _tag_target(runner: Runner, repository: str, tag: str) -> str | None:
    result = runner(("gh", "api", f"repos/{repository}/git/ref/tags/{tag}"))
    if result.returncode != 0:
        if "404" in _detail(result) or "Not Found" in _detail(result):
            return None
        raise PublishError(f"cannot query release tag {tag}: {_detail(result)}")
    reference = _object(result.stdout, f"tag {tag}")
    item = reference.get("object")
    if not isinstance(item, dict) or item.get("type") != "commit":
        raise PublishError(f"audit tag is not a lightweight commit tag: {tag}")
    target = item.get("sha")
    if not isinstance(target, str):
        raise PublishError(f"audit tag target is invalid: {tag}")
    return target


def _asset_map(release: dict[str, Any], tag: str) -> dict[str, dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets):
        raise PublishError(f"release assets are invalid: {tag}")
    result: dict[str, dict[str, Any]] = {}
    for item in assets:
        name = item.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise PublishError(f"release asset names are invalid or duplicated: {tag}")
        result[name] = item
    unexpected = set(result) - set(ASSET_NAMES)
    if unexpected:
        raise PublishError(f"release contains unexpected asset: {tag}/{sorted(unexpected)[0]}")
    return result


def _download(
    downloader: Downloader,
    repository: str,
    tag: str,
    asset: dict[str, Any],
    destination: Path,
) -> None:
    asset_id = asset.get("id")
    if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id < 1:
        raise PublishError(f"asset ID is invalid: {tag}/{asset.get('name')}")
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
        raise PublishError(f"cannot download {tag}/{asset.get('name')}: {_detail(result)}")


def _verify_remote_assets(
    downloader: Downloader,
    assets_root: Path,
    repository: str,
    tag: str,
    publisher_commit: str,
    release: dict[str, Any],
    *,
    require_complete: bool,
) -> set[str]:
    assets = _asset_map(release, tag)
    missing = set(ASSET_NAMES) - set(assets)
    if require_complete and missing:
        raise PublishError(f"release is missing asset: {tag}/{sorted(missing)[0]}")
    with tempfile.TemporaryDirectory(prefix=f"audit-release-{tag}-") as temporary:
        remote = Path(temporary)
        for name, metadata in assets.items():
            local = assets_root / name
            if not local.is_file():
                raise PublishError(f"local audit asset is missing: {tag}/{name}")
            digest = sha256(local)
            if metadata.get("size") != local.stat().st_size:
                raise PublishError(f"GitHub asset size differs: {tag}/{name}")
            if metadata.get("digest") != f"sha256:{digest}":
                raise PublishError(f"GitHub asset digest differs: {tag}/{name}")
            downloaded = remote / name
            _download(downloader, repository, tag, metadata, downloaded)
            if downloaded.stat().st_size != local.stat().st_size or sha256(downloaded) != digest:
                raise PublishError(f"release asset bytes differ: {tag}/{name}")
        if not missing:
            try:
                verify_release(
                    root=remote,
                    tag=tag,
                    publisher_repository=repository,
                    publisher_commit=publisher_commit,
                )
            except AuditReleaseError as error:
                raise PublishError(str(error)) from error
    return missing


def _create_draft(
    runner: Runner, repository: str, tag: str, publisher_commit: str
) -> dict[str, Any]:
    release = _object(
        _run(
            runner,
            (
                "gh",
                "api",
                f"repos/{repository}/releases",
                "--method",
                "POST",
                "--raw-field",
                f"tag_name={tag}",
                "--raw-field",
                f"target_commitish={publisher_commit}",
                "--raw-field",
                f"name={_title(tag)}",
                "--raw-field",
                f"body={RELEASE_NOTES}",
                "--field",
                "draft=true",
                "--field",
                "prerelease=false",
                "--raw-field",
                "make_latest=false",
            ),
        ),
        f"created release {tag}",
    )
    _validate_metadata(release, tag, publisher_commit, draft=True)
    return release


def _upload_missing(
    runner: Runner,
    assets_root: Path,
    repository: str,
    release_id: int,
    tag: str,
    missing: set[str],
) -> None:
    for name in sorted(missing):
        asset = _object(
            _run(
                runner,
                (
                    "gh",
                    "api",
                    f"https://uploads.github.com/repos/{repository}/releases/{release_id}/assets?name={quote(name, safe='')}",
                    "--method",
                    "POST",
                    "--header",
                    "Content-Type: application/octet-stream",
                    "--input",
                    str(assets_root / name),
                ),
            ),
            f"uploaded asset {tag}/{name}",
        )
        if asset.get("name") != name or asset.get("size") != (assets_root / name).stat().st_size:
            raise PublishError(f"uploaded asset metadata differs: {tag}/{name}")


def _publish_draft(
    runner: Runner, repository: str, release_id: int, tag: str, publisher_commit: str
) -> dict[str, Any]:
    release = _object(
        _run(
            runner,
            (
                "gh",
                "api",
                f"repos/{repository}/releases/{release_id}",
                "--method",
                "PATCH",
                "--raw-field",
                f"tag_name={tag}",
                "--raw-field",
                f"target_commitish={publisher_commit}",
                "--raw-field",
                f"name={_title(tag)}",
                "--raw-field",
                f"body={RELEASE_NOTES}",
                "--field",
                "draft=false",
                "--field",
                "prerelease=false",
                "--raw-field",
                "make_latest=false",
            ),
        ),
        f"published release {tag}",
    )
    _validate_metadata(release, tag, publisher_commit, draft=False)
    return release


def verify_remote(
    *,
    assets_root: Path,
    repository: str,
    tag: str,
    publisher_commit: str,
    runner: Runner = default_runner,
    downloader: Downloader = default_downloader,
) -> int:
    releases = _list_releases(runner, repository)
    release = _find_release(releases, tag)
    if release is None:
        raise PublishError(f"audit release does not exist: {tag}")
    release_id = _release_id(release, tag)
    release = _release_by_id(runner, repository, release_id)
    _validate_metadata(release, tag, publisher_commit, draft=False)
    _verify_remote_assets(
        downloader,
        assets_root,
        repository,
        tag,
        publisher_commit,
        release,
        require_complete=True,
    )
    if _tag_target(runner, repository, tag) != publisher_commit:
        raise PublishError(f"release tag target differs: {tag}")
    return release_id


def publish(
    *,
    assets_root: Path,
    repository: str,
    tag: str,
    publisher_commit: str,
    bootstrap_index: Path,
    bootstrap_ledger: Path,
    runner: Runner = default_runner,
    downloader: Downloader = default_downloader,
) -> int:
    try:
        local_verified = verify_release(
            root=assets_root,
            tag=tag,
            publisher_repository=repository,
            publisher_commit=publisher_commit,
        )
    except AuditReleaseError as error:
        raise PublishError(str(error)) from error
    already_published = _verify_live_chain_state(
        local_verified=local_verified,
        repository=repository,
        tag=tag,
        bootstrap_index=bootstrap_index,
        bootstrap_ledger=bootstrap_ledger,
        runner=runner,
        downloader=downloader,
    )
    releases = _list_releases(runner, repository)
    release = _find_release(releases, tag)
    if release is not None and release.get("draft") is False:
        if not already_published:
            raise PublishError("public audit release is absent from the verified chain")
        return verify_remote(
            assets_root=assets_root,
            repository=repository,
            tag=tag,
            publisher_commit=publisher_commit,
            runner=runner,
            downloader=downloader,
        )
    if already_published:
        raise PublishError("verified chain head has no matching public release")
    _require_immutable_releases_enabled(runner, repository)
    if release is None:
        release = _create_draft(runner, repository, tag, publisher_commit)
    release_id = _release_id(release, tag)
    release = _release_by_id(runner, repository, release_id)
    _validate_metadata(release, tag, publisher_commit, draft=True)
    target = _tag_target(runner, repository, tag)
    if target not in {None, publisher_commit}:
        raise PublishError(f"draft tag target differs: {tag}")
    missing = _verify_remote_assets(
        downloader,
        assets_root,
        repository,
        tag,
        publisher_commit,
        release,
        require_complete=False,
    )
    _upload_missing(runner, assets_root, repository, release_id, tag, missing)
    release = _release_by_id(runner, repository, release_id)
    _validate_metadata(release, tag, publisher_commit, draft=True)
    _verify_remote_assets(
        downloader,
        assets_root,
        repository,
        tag,
        publisher_commit,
        release,
        require_complete=True,
    )
    # Close the release-allocation race after uploads but before the irreversible
    # public transition. A competing release must force this run to stop as a
    # harmless draft rather than create a fork.
    if _verify_live_chain_state(
        local_verified=local_verified,
        repository=repository,
        tag=tag,
        bootstrap_index=bootstrap_index,
        bootstrap_ledger=bootstrap_ledger,
        runner=runner,
        downloader=downloader,
    ):
        return verify_remote(
            assets_root=assets_root,
            repository=repository,
            tag=tag,
            publisher_commit=publisher_commit,
            runner=runner,
            downloader=downloader,
        )
    _require_immutable_releases_enabled(runner, repository)
    _publish_draft(runner, repository, release_id, tag, publisher_commit)
    for attempt in range(5):
        if _tag_target(runner, repository, tag) == publisher_commit:
            break
        if attempt == 4:
            raise PublishError(f"release tag did not become visible: {tag}")
        time.sleep(2**attempt)
    return verify_remote(
        assets_root=assets_root,
        repository=repository,
        tag=tag,
        publisher_commit=publisher_commit,
        runner=runner,
        downloader=downloader,
    )


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("publish", "verify"))
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--publisher-commit", required=True)
    parser.add_argument("--bootstrap-index", type=Path)
    parser.add_argument("--bootstrap-ledger", type=Path)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "publish":
            if args.bootstrap_index is None or args.bootstrap_ledger is None:
                raise PublishError("publish requires bootstrap index and ledger")
            release_id = publish(
                assets_root=args.assets,
                repository=args.repository,
                tag=args.tag,
                publisher_commit=args.publisher_commit,
                bootstrap_index=args.bootstrap_index,
                bootstrap_ledger=args.bootstrap_ledger,
            )
        else:
            release_id = verify_remote(
                assets_root=args.assets,
                repository=args.repository,
                tag=args.tag,
                publisher_commit=args.publisher_commit,
            )
    except (ChainError, PublishError, OSError, ValueError) as error:
        print(f"audit publication failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"tag": args.tag, "releaseId": release_id}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
