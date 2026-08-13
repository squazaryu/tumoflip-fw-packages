#!/usr/bin/env python3
"""Publish the transitional raw audit branch after immutable release verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from .audit_bootstrap import BootstrapError, load_object as load_bootstrap_object, validate_index
    from .audit_release import LEDGER_ASSET, sha256
    from .publish_audit import PublishError, verify_remote
    from .tumoflip import protected_app_audit as audit_tool
except ImportError:  # Direct script execution.
    from audit_bootstrap import BootstrapError, load_object as load_bootstrap_object, validate_index
    from audit_release import LEDGER_ASSET, sha256
    from publish_audit import PublishError, verify_remote
    from tumoflip import protected_app_audit as audit_tool


BRANCH = "protected-app-audit-ledger"


class BranchError(RuntimeError):
    """Raised when raw branch publication would lose or rewrite audit history."""


def _run(command: Sequence[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BranchError(f"command failed ({' '.join(command)}): {detail}")
    return result.stdout.strip()


def _run_bytes(command: Sequence[str], *, cwd: Path | None = None) -> bytes:
    result = subprocess.run(command, cwd=cwd, check=False, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = result.stdout.decode("utf-8", errors="replace").strip()
        raise BranchError(f"command failed ({' '.join(command)}): {detail}")
    return result.stdout


def _remote_head(repository: str) -> str | None:
    result = subprocess.run(
        ("git", "ls-remote", "--heads", f"https://github.com/{repository}.git", BRANCH),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BranchError(f"cannot resolve remote audit branch: {result.stderr.strip()}")
    if not result.stdout.strip():
        return None
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != f"refs/heads/{BRANCH}":
        raise BranchError("remote audit branch identity is ambiguous")
    return rows[0][0]


def _semantic_history_exists(history: Path, audit: dict[str, Any]) -> bool:
    expected = audit_tool.semantic_audit_sha256(audit)
    for path in history.glob("*.json"):
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise BranchError(f"invalid audit history file {path.name}: {error}") from error
        if not isinstance(existing, dict):
            raise BranchError(f"invalid audit history root: {path.name}")
        if audit_tool.semantic_audit_sha256(existing) == expected:
            return True
    return False


def prepare_tree(*, root: Path, audit_path: Path, released_ledger: Path) -> list[str]:
    latest = root / "latest.json"
    history = root / "history"
    if not latest.is_file() or not history.is_dir():
        raise BranchError("raw audit branch lacks cumulative latest/history")
    try:
        existing = audit_tool.read_json(latest)
        audit = audit_tool.read_json(audit_path)
        audit_tool.validate_ledger(existing, allow_client_duplicate_provenance=True)
        audit_tool.validate_audit(audit)
        merged = audit_tool.merge_ledger(existing, audit)
    except audit_tool.AuditError as error:
        raise BranchError(str(error)) from error
    released_bytes = released_ledger.read_bytes()
    generated = root / ".generated-latest.json"
    audit_tool.write_json(generated, merged)
    generated_bytes = generated.read_bytes()
    generated.unlink()
    if generated_bytes != released_bytes:
        raise BranchError("raw cumulative ledger differs from immutable release ledger")
    changed: list[str] = []
    if latest.read_bytes() != released_bytes:
        latest.write_bytes(released_bytes)
        changed.append("latest.json")
    if not _semantic_history_exists(history, audit):
        archives = {item["pack"]: item["sha256"] for item in audit["archives"]}
        semantic = audit_tool.semantic_audit_sha256(audit)
        filename = (
            f"{audit['sequence']}-{audit['sourceTag']}-{archives['base'][:12]}-"
            f"{archives['extra'][:12]}-{semantic}.json"
        )
        destination = history / filename
        if destination.exists():
            raise BranchError(f"audit history filename collision: {filename}")
        shutil.copyfile(audit_path, destination)
        changed.append(f"history/{filename}")
    return changed


def publish_branch(
    *,
    repository: str,
    publisher_commit: str,
    tag: str,
    release_assets: Path,
    audit_path: Path,
    bootstrap_index: Path,
) -> str:
    verify_remote(
        assets_root=release_assets,
        repository=repository,
        tag=tag,
        publisher_commit=publisher_commit,
    )
    released_ledger = release_assets / LEDGER_ASSET
    index = validate_index(load_bootstrap_object(bootstrap_index))
    expected_head = _remote_head(repository)
    with tempfile.TemporaryDirectory(prefix="audit-branch-") as temporary:
        work = Path(temporary)
        _run(("git", "init", "-q"), cwd=work)
        _run(("git", "remote", "add", "origin", f"https://github.com/{repository}.git"), cwd=work)
        if expected_head is not None:
            _run(("git", "fetch", "--no-tags", "origin", expected_head), cwd=work)
            _run(("git", "checkout", "-q", "-b", "audit-publish", "FETCH_HEAD"), cwd=work)
        else:
            legacy = index["legacy"]
            _run(
                (
                    "git",
                    "fetch",
                    "--no-tags",
                    f"https://github.com/{legacy['repository']}.git",
                    legacy["commit"],
                ),
                cwd=work,
            )
            _run(("git", "checkout", "-q", "-b", "audit-publish", "FETCH_HEAD"), cwd=work)
            if _run(("git", "rev-parse", "HEAD"), cwd=work) != legacy["commit"]:
                raise BranchError("legacy bootstrap checkout commit differs")
        try:
            audit_tool.validate_ledger(audit_tool.read_json(work / "latest.json"))
        except audit_tool.AuditError as error:
            raise BranchError(f"remote latest is not client-strict: {error}") from error
        changed = prepare_tree(root=work, audit_path=audit_path, released_ledger=released_ledger)
        if changed:
            _run(("git", "add", "--", *changed), cwd=work)
            _run(("git", "diff", "--cached", "--check"), cwd=work)
            _run(
                (
                    "git",
                    "-c",
                    "user.name=github-actions[bot]",
                    "-c",
                    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit",
                    "-m",
                    f"chore(audit): publish {tag}",
                ),
                cwd=work,
            )
            _run(
                (
                    "git",
                    "push",
                    "origin",
                    f"HEAD:refs/heads/{BRANCH}",
                ),
                cwd=work,
            )
        published_head = _run(("git", "rev-parse", "HEAD"), cwd=work)
        remote_head = _remote_head(repository)
        if remote_head != published_head:
            raise BranchError("remote audit branch head differs after publication")
        _run(("git", "fetch", "--no-tags", "origin", remote_head), cwd=work)
        remote_ledger = _run_bytes(("git", "show", f"{remote_head}:latest.json"), cwd=work)
        if sha256(released_ledger) != hashlib.sha256(remote_ledger).hexdigest():
            raise BranchError("remote raw latest differs from immutable release ledger")
        return remote_head


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--publisher-commit", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-assets", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--bootstrap-index", type=Path, default=Path("audit/bootstrap/index.json"))
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        head = publish_branch(
            repository=args.repository,
            publisher_commit=args.publisher_commit,
            tag=args.tag,
            release_assets=args.release_assets,
            audit_path=args.audit,
            bootstrap_index=args.bootstrap_index,
        )
    except (BranchError, BootstrapError, PublishError, OSError, ValueError) as error:
        print(f"raw audit branch publication failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"branch": BRANCH, "commit": head}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
