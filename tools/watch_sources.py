#!/usr/bin/env python3
"""Watch non-GitHub and repository-level protected sources by exact refs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable


HEX_40 = re.compile(r"^[0-9a-f]{40}$")
SCHEMA = 1


class WatchError(ValueError):
    """Raised for malformed watcher input or unverifiable source state."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WatchError(f"unable to read JSON: {path}") from error
    if not isinstance(value, dict):
        raise WatchError("watcher contract root must be an object")
    return value


def commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX_40.fullmatch(value):
        raise WatchError(f"{label} must be a full commit SHA")
    return value


def validate_contract(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema") != SCHEMA or document.get("kind") != "sourceWatchers":
        raise WatchError("source watcher contract has an unsupported schema or kind")
    if document.get("policy") != "review-only":
        raise WatchError("source watcher policy must be review-only")
    watchers = document.get("watchers")
    if not isinstance(watchers, list) or not watchers:
        raise WatchError("watchers must be a non-empty list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(watchers):
        if not isinstance(item, dict):
            raise WatchError(f"watchers[{index}] must be an object")
        watcher_id = item.get("id")
        repository = item.get("repository")
        ref = item.get("ref")
        if not isinstance(watcher_id, str) or not watcher_id:
            raise WatchError(f"watchers[{index}].id is invalid")
        if watcher_id in seen:
            raise WatchError(f"duplicate watcher id: {watcher_id}")
        if not isinstance(repository, str) or not repository:
            raise WatchError(f"{watcher_id}.repository is invalid")
        if not isinstance(ref, str) or not ref.startswith("refs/"):
            raise WatchError(f"{watcher_id}.ref is invalid")
        related = item.get("relatedLocalPaths", [])
        if not isinstance(related, list) or not all(isinstance(value, str) and value for value in related):
            raise WatchError(f"{watcher_id}.relatedLocalPaths is invalid")
        seen.add(watcher_id)
        result.append(
            {
                "id": watcher_id,
                "repository": repository,
                "ref": ref,
                "reviewedCommit": commit(item.get("reviewedCommit"), f"{watcher_id}.reviewedCommit"),
                "relatedLocalPaths": related,
            }
        )
    return result


def fetch_head(repository: str, ref: str) -> str:
    try:
        result = subprocess.run(
            ["git", "ls-remote", repository, ref],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired as error:
        raise WatchError(f"source fetch timed out for {repository} {ref}") from error
    if result.returncode:
        raise WatchError(f"source fetch failed for {repository} {ref}")
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) < 2:
        raise WatchError(f"source ref is missing or ambiguous: {repository} {ref}")
    return commit(rows[0][0], f"{repository} {ref} head")


def scan(
    contract: dict[str, Any],
    *,
    generated_at: str | None = None,
    fetch=fetch_head,
) -> dict[str, Any]:
    watchers = validate_contract(contract)
    results: list[dict[str, Any]] = []
    for item in watchers:
        try:
            current = fetch(item["repository"], item["ref"])
            error = None
        except WatchError as exc:
            current = None
            error = str(exc)
        status = "verified" if current == item["reviewedCommit"] else "needsReview"
        if error:
            status = "needsReview"
        results.append(
            {
                "id": item["id"],
                "repository": item["repository"],
                "ref": item["ref"],
                "reviewedCommit": item["reviewedCommit"],
                "currentCommit": current,
                "relatedLocalPaths": item["relatedLocalPaths"],
                "status": status,
                **({"error": error} if error else {}),
            }
        )
    overall = "needsReview" if any(item["status"] != "verified" for item in results) else "verified"
    return {
        "schema": SCHEMA,
        "kind": "sourceWatchReport",
        "generatedAt": generated_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "status": overall,
        "watchers": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "<!-- source-matrix-watch -->",
        "# Protected source matrix",
        "",
        f"- Status: **{report['status']}**",
        f"- Generated: `{report['generatedAt']}`",
        "",
        "| Source | Ref | Status | Reviewed | Current | Related local paths |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if report.get("error"):
        lines.extend([f"- Error: `{report['error']}`", ""])
    for item in report["watchers"]:
        current = item["currentCommit"] or "unavailable"
        paths = ", ".join(f"`{path}`" for path in item["relatedLocalPaths"]) or "—"
        lines.append(
            f"| `{item['id']}` | `{item['ref']}` | **{item['status']}** | "
            f"`{item['reviewedCommit']}` | `{current}` | {paths} |"
        )
        if item.get("error"):
            lines.append(f"\nError: `{item['error']}`\n")
    lines.extend(
        [
            "",
            "This watcher is review-only. A changed or unavailable source never updates the baseline automatically.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan", choices=["scan"])
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = scan(read_json(args.contract))
    except WatchError as error:
        report = {
            "schema": SCHEMA,
            "kind": "sourceWatchReport",
            "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "status": "needsReview",
            "error": str(error),
            "watchers": [],
        }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    return 1 if report["status"] == "needsReview" else 0


if __name__ == "__main__":
    raise SystemExit(main())
