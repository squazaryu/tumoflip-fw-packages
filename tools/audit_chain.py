#!/usr/bin/env python3
"""Resolve the verified immutable protected-app audit chain and next release."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

try:
    from .audit_release import (
        ASSET_NAMES,
        LEDGER_ASSET,
        PROVENANCE_ASSET,
        AuditReleaseError,
        load_object,
        sha256,
        validate_predecessor,
        verify_release,
    )
    from .tumoflip import protected_app_audit as audit_tool
except ImportError:  # Direct script execution.
    from audit_release import (
        ASSET_NAMES,
        LEDGER_ASSET,
        PROVENANCE_ASSET,
        AuditReleaseError,
        load_object,
        sha256,
        validate_predecessor,
        verify_release,
    )
    from tumoflip import protected_app_audit as audit_tool


AUDIT_TAG = re.compile(r"^audit-ledger-([0-9]{8})-([0-9]{3})$")
SOURCE_TAG = re.compile(r"^([0-9]{1,2})([a-z]{3})([0-9]{4})(?:p[0-9]+)?$")
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Downloader = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


class ChainError(RuntimeError):
    """Raised when immutable audit history is absent, ambiguous, or divergent."""


@dataclass(frozen=True)
class RemoteAuditRelease:
    release_id: int
    tag: str
    tag_commit: str
    root: Path
    verified: dict[str, Any]
    ledger: dict[str, Any]

    def predecessor_identity(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "kind": "auditRelease",
            "tag": self.tag,
            "githubReleaseId": self.release_id,
            "tagCommit": self.tag_commit,
            "ledgerSHA256": self.verified["ledgerSHA256"],
            "provenanceSHA256": self.verified["provenanceSHA256"],
        }


def default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def default_downloader(
    command: Sequence[str], destination: Path
) -> subprocess.CompletedProcess[str]:
    with destination.open("wb") as stream:
        result = subprocess.run(command, check=False, stdout=stream, stderr=subprocess.PIPE)
    return subprocess.CompletedProcess(
        command,
        result.returncode,
        "",
        result.stderr.decode("utf-8", errors="replace"),
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"


def _run_json(runner: Runner, command: Sequence[str], label: str) -> Any:
    result = runner(command)
    if result.returncode != 0:
        raise ChainError(f"cannot query {label}: {_detail(result)}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise ChainError(f"invalid GitHub response for {label}") from error


def _list_releases(runner: Runner, repository: str) -> list[dict[str, Any]]:
    value = _run_json(
        runner,
        ("gh", "api", f"repos/{repository}/releases?per_page=100", "--paginate", "--slurp"),
        "audit releases",
    )
    if not isinstance(value, list):
        raise ChainError("GitHub release response is not an array")
    releases: list[Any] = []
    if all(isinstance(item, dict) for item in value):
        releases = value
    else:
        for page in value:
            if not isinstance(page, list):
                raise ChainError("GitHub paginated release response is invalid")
            releases.extend(page)
    if not all(isinstance(item, dict) for item in releases):
        raise ChainError("GitHub release item is invalid")
    return releases


def _asset_map(release: dict[str, Any], tag: str) -> dict[str, dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets):
        raise ChainError(f"audit release assets are invalid: {tag}")
    result: dict[str, dict[str, Any]] = {}
    for item in assets:
        name = item.get("name")
        asset_id = item.get("id")
        size = item.get("size")
        if (
            not isinstance(name, str)
            or name in result
            or not isinstance(asset_id, int)
            or isinstance(asset_id, bool)
            or asset_id < 1
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 1
        ):
            raise ChainError(f"audit release asset metadata is invalid: {tag}")
        result[name] = item
    if set(result) != set(ASSET_NAMES):
        raise ChainError(f"audit release asset set differs: {tag}")
    return result


def _download_assets(
    *,
    release: dict[str, Any],
    repository: str,
    root: Path,
    downloader: Downloader,
) -> None:
    tag = str(release["tag_name"])
    for name, asset in _asset_map(release, tag).items():
        destination = root / name
        result = downloader(
            (
                "gh",
                "api",
                "-H",
                "Accept: application/octet-stream",
                f"repos/{repository}/releases/assets/{asset['id']}",
            ),
            destination,
        )
        if result.returncode != 0:
            destination.unlink(missing_ok=True)
            raise ChainError(f"cannot download audit asset {tag}/{name}: {_detail(result)}")
        if destination.stat().st_size != asset["size"]:
            raise ChainError(f"audit asset size differs: {tag}/{name}")
        digest = asset.get("digest")
        if digest is not None and digest != f"sha256:{sha256(destination)}":
            raise ChainError(f"audit asset digest differs: {tag}/{name}")


def _tag_target(runner: Runner, repository: str, tag: str) -> str:
    value = _run_json(
        runner,
        ("gh", "api", f"repos/{repository}/git/ref/tags/{tag}"),
        f"audit tag {tag}",
    )
    item = value.get("object") if isinstance(value, dict) else None
    if not isinstance(item, dict) or item.get("type") != "commit":
        raise ChainError(f"audit tag is not a lightweight commit tag: {tag}")
    commit = item.get("sha")
    if not isinstance(commit, str) or HEX_40.fullmatch(commit) is None:
        raise ChainError(f"audit tag commit is invalid: {tag}")
    return commit


def bootstrap_identity(index_path: Path, ledger_path: Path) -> dict[str, Any]:
    ledger = load_object(ledger_path)
    try:
        audit_tool.validate_ledger(ledger)
    except audit_tool.AuditError as error:
        raise ChainError(f"bootstrap ledger is invalid: {error}") from error
    return {
        "schema": 1,
        "kind": "bootstrap",
        "indexSHA256": sha256(index_path),
        "ledgerSHA256": sha256(ledger_path),
    }


def _materialize_release(
    *,
    release: dict[str, Any],
    repository: str,
    root: Path,
    runner: Runner,
    downloader: Downloader,
) -> RemoteAuditRelease:
    tag = release.get("tag_name")
    release_id = release.get("id")
    if (
        not isinstance(tag, str)
        or AUDIT_TAG.fullmatch(tag) is None
        or not isinstance(release_id, int)
        or isinstance(release_id, bool)
        or release_id < 1
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("immutable") is not True
    ):
        raise ChainError("public audit release identity is invalid or mutable")
    destination = root / tag
    destination.mkdir()
    _download_assets(
        release=release,
        repository=repository,
        root=destination,
        downloader=downloader,
    )
    provenance = load_object(destination / PROVENANCE_ASSET)
    publisher = provenance.get("publisher")
    commit = publisher.get("commit") if isinstance(publisher, dict) else None
    if publisher != {"repository": repository, "commit": commit} or not isinstance(commit, str):
        raise ChainError(f"audit publisher identity is invalid: {tag}")
    if HEX_40.fullmatch(commit) is None or release.get("target_commitish") != commit:
        raise ChainError(f"audit release target differs: {tag}")
    if _tag_target(runner, repository, tag) != commit:
        raise ChainError(f"audit tag target differs: {tag}")
    try:
        verified = verify_release(
            root=destination,
            tag=tag,
            publisher_repository=repository,
            publisher_commit=commit,
        )
    except AuditReleaseError as error:
        raise ChainError(f"audit release verification failed for {tag}: {error}") from error
    return RemoteAuditRelease(
        release_id=release_id,
        tag=tag,
        tag_commit=commit,
        root=destination,
        verified=verified,
        ledger=load_object(destination / LEDGER_ASSET),
    )


def resolve_remote_chain(
    *,
    repository: str,
    bootstrap_index: Path,
    bootstrap_ledger: Path,
    root: Path,
    runner: Runner = default_runner,
    downloader: Downloader = default_downloader,
) -> list[RemoteAuditRelease]:
    expected_bootstrap = bootstrap_identity(bootstrap_index, bootstrap_ledger)
    releases = _list_releases(runner, repository)
    public: list[dict[str, Any]] = []
    tags: set[str] = set()
    for release in releases:
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("audit-ledger-"):
            continue
        if AUDIT_TAG.fullmatch(tag) is None or tag in tags:
            raise ChainError("audit release tags are invalid or duplicated")
        tags.add(tag)
        if release.get("draft") is False:
            public.append(release)
    materialized = [
        _materialize_release(
            release=release,
            repository=repository,
            root=root,
            runner=runner,
            downloader=downloader,
        )
        for release in public
    ]
    return validate_materialized_chain(
        materialized,
        expected_bootstrap=expected_bootstrap,
        bootstrap_ledger=load_object(bootstrap_ledger),
    )


def validate_materialized_chain(
    materialized: Sequence[RemoteAuditRelease],
    *,
    expected_bootstrap: dict[str, Any],
    bootstrap_ledger: dict[str, Any],
) -> list[RemoteAuditRelease]:
    """Return one bootstrap-anchored chain or reject every fork/orphan/rewrite."""
    validate_predecessor(expected_bootstrap)
    try:
        audit_tool.validate_ledger(bootstrap_ledger)
    except audit_tool.AuditError as error:
        raise ChainError(f"bootstrap ledger is invalid: {error}") from error
    if not materialized:
        return []

    by_identity = {
        json.dumps(item.predecessor_identity(), sort_keys=True, separators=(",", ":")): item
        for item in materialized
    }
    if len(by_identity) != len(materialized):
        raise ChainError("audit release identities are duplicated")
    children: dict[str, list[RemoteAuditRelease]] = {}
    genesis: list[RemoteAuditRelease] = []
    for item in materialized:
        predecessor = validate_predecessor(item.verified["predecessor"])
        if predecessor == expected_bootstrap:
            genesis.append(item)
            parent_ledger = bootstrap_ledger
        elif predecessor["kind"] == "auditRelease":
            key = json.dumps(predecessor, sort_keys=True, separators=(",", ":"))
            parent = by_identity.get(key)
            if parent is None:
                raise ChainError(f"audit release predecessor is missing: {item.tag}")
            children.setdefault(key, []).append(item)
            parent_ledger = parent.ledger
        else:
            raise ChainError(f"audit release predecessor is not chain-bound: {item.tag}")
        expected_ledger = audit_tool.merge_ledger(parent_ledger, item.verified["audit"])
        if expected_ledger != item.ledger:
            raise ChainError(f"audit release rewrites cumulative history: {item.tag}")
        if predecessor["kind"] == "auditRelease" and item.ledger == parent_ledger:
            raise ChainError(f"audit release does not advance cumulative history: {item.tag}")
    if len(genesis) != 1:
        raise ChainError("immutable audit chain must have exactly one bootstrap child")

    ordered: list[RemoteAuditRelease] = []
    current = genesis[0]
    seen: set[str] = set()
    while True:
        identity = json.dumps(
            current.predecessor_identity(), sort_keys=True, separators=(",", ":")
        )
        if identity in seen:
            raise ChainError("immutable audit chain contains a cycle")
        seen.add(identity)
        ordered.append(current)
        successors = children.get(identity, [])
        if len(successors) > 1:
            raise ChainError(f"immutable audit chain forks after {current.tag}")
        if not successors:
            break
        current = successors[0]
    if len(ordered) != len(materialized):
        raise ChainError("immutable audit releases do not form one linear chain")
    return ordered


def _source_date(source_tag: str) -> str:
    match = SOURCE_TAG.fullmatch(source_tag)
    if match is None:
        raise ChainError("audit source tag cannot be mapped to a release date")
    try:
        return datetime.strptime(
            f"{int(match.group(1)):02d}{match.group(2)}{match.group(3)}", "%d%b%Y"
        ).strftime("%Y%m%d")
    except ValueError as error:
        raise ChainError("audit source tag date is invalid") from error


def allocate_tag(source_tag: str, chain: Sequence[RemoteAuditRelease]) -> str:
    date = _source_date(source_tag)
    revisions = sorted(
        int(match.group(2))
        for item in chain
        if (match := AUDIT_TAG.fullmatch(item.tag)) is not None and match.group(1) == date
    )
    if revisions and revisions != list(range(1, revisions[-1] + 1)):
        raise ChainError(f"audit release revisions are not contiguous for {date}")
    revision = (revisions[-1] if revisions else 0) + 1
    if revision > 999:
        raise ChainError(f"audit release revision space is exhausted for {date}")
    return f"audit-ledger-{date}-{revision:03d}"


def resolve_current(
    *,
    repository: str,
    publisher_commit: str,
    bootstrap_index: Path,
    bootstrap_ledger: Path,
    audit_path: Path,
    output: Path,
    runner: Runner = default_runner,
    downloader: Downloader = default_downloader,
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ChainError("audit chain output must be empty")
    if HEX_40.fullmatch(publisher_commit) is None:
        raise ChainError("audit publisher commit is invalid")
    output.mkdir(parents=True, exist_ok=True)
    audit = audit_tool.read_json(audit_path)
    audit_tool.validate_audit(audit)
    with tempfile.TemporaryDirectory(prefix="audit-chain-") as temporary:
        chain = resolve_remote_chain(
            repository=repository,
            bootstrap_index=bootstrap_index,
            bootstrap_ledger=bootstrap_ledger,
            root=Path(temporary),
            runner=runner,
            downloader=downloader,
        )
        if chain:
            existing = chain[-1].ledger
            predecessor = chain[-1].predecessor_identity()
        else:
            existing = load_object(bootstrap_ledger)
            predecessor = bootstrap_identity(bootstrap_index, bootstrap_ledger)
        merged = audit_tool.merge_ledger(existing, audit)
        ledger_path = output / "ledger.json"
        audit_tool.write_json(ledger_path, merged)
        predecessor_path = output / "predecessor.json"
        audit_tool.write_json(predecessor_path, predecessor)

        if chain and merged == existing:
            head = chain[-1]
            mode = "reuse"
            tag = head.tag
            release_id: int | None = head.release_id
            release_publisher = head.tag_commit
            shutil.copytree(head.root, output / "release-assets")
        else:
            mode = "publish"
            tag = allocate_tag(audit["sourceTag"], chain)
            release_id = None
            release_publisher = publisher_commit
        resolution = {
            "schema": 1,
            "kind": "protectedAuditChainResolution",
            "mode": mode,
            "auditReleaseTag": tag,
            "githubReleaseId": release_id,
            "publisherCommit": release_publisher,
            "predecessor": predecessor,
        }
        audit_tool.write_json(output / "resolution.json", resolution)
        return resolution


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--publisher-commit", required=True)
    parser.add_argument("--bootstrap-index", type=Path, required=True)
    parser.add_argument("--bootstrap-ledger", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        resolution = resolve_current(
            repository=args.repository,
            publisher_commit=args.publisher_commit,
            bootstrap_index=args.bootstrap_index,
            bootstrap_ledger=args.bootstrap_ledger,
            audit_path=args.audit,
            output=args.output,
        )
    except (AuditReleaseError, ChainError, OSError, ValueError, audit_tool.AuditError) as error:
        print(f"audit chain resolution failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(resolution, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
