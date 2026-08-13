#!/usr/bin/env python3
"""Verify and seed the protected-app ledger from its immutable legacy branch."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from tools.tumoflip import protected_app_audit as audit_tool
except ModuleNotFoundError:  # Direct script execution.
    from tumoflip import protected_app_audit as audit_tool


class BootstrapError(RuntimeError):
    """Raised when the legacy ledger differs from the checked-in contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BootstrapError(f"invalid JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise BootstrapError(f"JSON root must be an object: {path}")
    return value


def _require_file(root: Path, item: dict[str, Any]) -> Path:
    relative = item.get("path")
    if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
        raise BootstrapError("bootstrap path is invalid")
    path = root / relative
    if not path.is_file():
        raise BootstrapError(f"legacy ledger file is missing: {relative}")
    if path.stat().st_size != item.get("bytes"):
        raise BootstrapError(f"legacy ledger size differs: {relative}")
    if sha256(path) != item.get("sha256"):
        raise BootstrapError(f"legacy ledger SHA-256 differs: {relative}")
    return path


def validate_index(index: dict[str, Any]) -> dict[str, Any]:
    if index.get("schema") != 1 or index.get("kind") != "protectedAuditLedgerBootstrap":
        raise BootstrapError("bootstrap index contract is invalid")
    legacy = index.get("legacy")
    destination = index.get("destination")
    client = index.get("clientContract")
    if not all(isinstance(value, dict) for value in (legacy, destination, client)):
        raise BootstrapError("bootstrap identity is incomplete")
    if legacy.get("repository") != "squazaryu/tumoflip":
        raise BootstrapError("legacy repository is invalid")
    if legacy.get("branch") != "protected-app-audit-ledger":
        raise BootstrapError("legacy branch is invalid")
    commit = legacy.get("commit")
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise BootstrapError("legacy commit is invalid")
    for field in ("tree", "historyTree"):
        value = legacy.get(field)
        if not isinstance(value, str) or len(value) != 40 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise BootstrapError(f"legacy {field} is invalid")
    if destination != {
        "repository": "squazaryu/tumoflip-fw-packages",
        "branch": "protected-app-audit-ledger",
    }:
        raise BootstrapError("destination identity is invalid")
    if client.get("schema") != 2 or client.get("provenanceIdentity") != [
        "targetMD5",
        "channel",
        "releaseTag",
        "manifestSHA256",
    ]:
        raise BootstrapError("client identity contract is invalid")
    latest = legacy.get("latest")
    history = legacy.get("history")
    if not isinstance(latest, dict) or latest.get("path") != "latest.json":
        raise BootstrapError("legacy latest contract is invalid")
    if not isinstance(history, list) or not history:
        raise BootstrapError("legacy history contract is empty")
    paths = [item.get("path") for item in history if isinstance(item, dict)]
    if len(paths) != len(history) or len(paths) != len(set(paths)):
        raise BootstrapError("legacy history paths are invalid or duplicated")
    legacy_generator = client.get("legacyGeneratorSnapshots")
    if not isinstance(legacy_generator, list) or set(legacy_generator) != {
        item["path"] for item in history if item.get("clientStrict") is False
    }:
        raise BootstrapError("legacy generator snapshot classification differs")
    return index


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BootstrapError("legacy ledger checkout is not a Git repository")
    return result.stdout.strip()


def verify(*, index_path: Path, normalized_latest: Path, legacy_root: Path | None) -> dict[str, Any]:
    index = validate_index(load_object(index_path))
    legacy = index["legacy"]
    if normalized_latest.stat().st_size != legacy["latest"]["bytes"]:
        raise BootstrapError("checked-in normalized latest size differs")
    if sha256(normalized_latest) != legacy["latest"]["sha256"]:
        raise BootstrapError("checked-in normalized latest SHA-256 differs")
    try:
        audit_tool.validate_ledger(load_object(normalized_latest))
    except audit_tool.AuditError as error:
        raise BootstrapError(f"checked-in latest is not client-strict: {error}") from error
    if legacy_root is not None:
        if _git_head(legacy_root) != legacy["commit"]:
            raise BootstrapError("legacy checkout commit differs")
        tree = subprocess.run(
            ["git", "-C", str(legacy_root), "rev-parse", "HEAD^{tree}"],
            check=False,
            capture_output=True,
            text=True,
        )
        history_tree = subprocess.run(
            ["git", "-C", str(legacy_root), "rev-parse", "HEAD:history"],
            check=False,
            capture_output=True,
            text=True,
        )
        if tree.returncode != 0 or tree.stdout.strip() != legacy["tree"]:
            raise BootstrapError("legacy ledger tree differs")
        if history_tree.returncode != 0 or history_tree.stdout.strip() != legacy["historyTree"]:
            raise BootstrapError("legacy history tree differs")
        legacy_latest = _require_file(legacy_root, legacy["latest"])
        if legacy_latest.read_bytes() != normalized_latest.read_bytes():
            raise BootstrapError("checked-in latest differs byte-for-byte from legacy branch")
        for item in legacy["history"]:
            path = _require_file(legacy_root, item)
            document = load_object(path)
            try:
                audit_tool.validate_audit(
                    document,
                    allow_client_duplicate_provenance=not item["clientStrict"],
                )
            except audit_tool.AuditError as error:
                raise BootstrapError(f"legacy history is invalid: {item['path']}: {error}") from error
            if item["clientStrict"]:
                try:
                    audit_tool.validate_audit(document)
                except audit_tool.AuditError as error:
                    raise BootstrapError(f"strict history classification differs: {item['path']}: {error}") from error
    return index


def seed(*, index_path: Path, normalized_latest: Path, legacy_root: Path, output: Path) -> None:
    index = verify(
        index_path=index_path,
        normalized_latest=normalized_latest,
        legacy_root=legacy_root,
    )
    if output.exists() and any(output.iterdir()):
        raise BootstrapError("bootstrap output must be empty")
    (output / "history").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(normalized_latest, output / "latest.json")
    for item in index["legacy"]["history"]:
        source = legacy_root / item["path"]
        destination = output / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=Path("audit/bootstrap/index.json"))
    parser.add_argument("--latest", type=Path, default=Path("audit/bootstrap/latest.json"))
    parser.add_argument("--legacy-root", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.output is None:
            verify(
                index_path=args.index,
                normalized_latest=args.latest,
                legacy_root=args.legacy_root,
            )
        else:
            if args.legacy_root is None:
                raise BootstrapError("--legacy-root is required with --output")
            seed(
                index_path=args.index,
                normalized_latest=args.latest,
                legacy_root=args.legacy_root,
                output=args.output,
            )
    except (BootstrapError, OSError, ValueError) as error:
        print(f"audit bootstrap validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
