#!/usr/bin/env python3
"""Watch non-GitHub and repository-level protected sources by exact refs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

if __package__:
    from . import github_lifecycle
else:
    import github_lifecycle


HEX_40 = re.compile(r"^[0-9a-f]{40}$")
SCHEMAS = {1, 2}


class WatchError(ValueError):
    """Raised for malformed watcher input or unverifiable source state."""


def document_schema(document: dict[str, Any]) -> int:
    value = document.get("schema")
    return value if value in SCHEMAS else 1


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
    schema = document.get("schema")
    if schema not in SCHEMAS or document.get("kind") != "sourceWatchers":
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
        normalized = {
            "id": watcher_id,
            "repository": repository,
            "ref": ref,
            "reviewedCommit": commit(
                item.get("reviewedCommit"), f"{watcher_id}.reviewedCommit"
            ),
            "relatedLocalPaths": related,
        }
        github = item.get("githubLifecycle")
        if github is not None:
            if schema != 2 or not isinstance(github, dict):
                raise WatchError(f"{watcher_id}.githubLifecycle requires schema 2")
            github_repository = github.get("repository")
            branch = github.get("branch")
            reviewed_at = github.get("reviewedAt")
            if not isinstance(github_repository, str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", github_repository
            ):
                raise WatchError(f"{watcher_id}.githubLifecycle.repository is invalid")
            if repository != f"https://github.com/{github_repository}.git":
                raise WatchError(f"{watcher_id}.githubLifecycle repository differs")
            if not isinstance(branch, str) or ref != f"refs/heads/{branch}":
                raise WatchError(f"{watcher_id}.githubLifecycle branch differs")
            if not isinstance(reviewed_at, str):
                raise WatchError(f"{watcher_id}.githubLifecycle.reviewedAt is invalid")
            try:
                reviewed_time = datetime.strptime(
                    reviewed_at, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
            except ValueError as error:
                raise WatchError(
                    f"{watcher_id}.githubLifecycle.reviewedAt is invalid"
                ) from error
            try:
                policy = github_lifecycle.validate_policy(github.get("policy"))
            except github_lifecycle.LifecycleError as error:
                raise WatchError(f"{watcher_id}: {error}") from error
            if policy["trackReleases"]:
                raise WatchError(
                    f"{watcher_id}.githubLifecycle release tracking needs an exact release contract"
                )
            normalized["githubLifecycle"] = {
                "repository": github_repository,
                "branch": branch,
                "reviewedAt": reviewed_time,
                "policy": policy,
            }
        elif schema == 2 and "githubLifecycle" in item:
            raise WatchError(f"{watcher_id}.githubLifecycle is invalid")
        result.append(normalized)
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
    lifecycle_fetch=github_lifecycle.gh_json,
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
        lifecycle: dict[str, Any]
        github = item.get("githubLifecycle")
        if github is None:
            lifecycle = {
                "capability": "gitOnly",
                "reviewRequired": False,
                "taskPolicy": "branch-head-only",
            }
        elif current is None:
            lifecycle = {
                "capability": "unavailable",
                "reviewRequired": True,
                "error": "branch head is unavailable; GitHub lifecycle was not queried",
            }
        else:
            try:
                lifecycle = github_lifecycle.collect(
                    repository=github["repository"],
                    branch=github["branch"],
                    since=github["reviewedAt"],
                    reviewed_commit=item["reviewedCommit"],
                    branch_head=current,
                    release_commit=None,
                    policy=github["policy"],
                    fetch=lifecycle_fetch,
                )
                lifecycle["capability"] = "github"
            except github_lifecycle.LifecycleError as exc:
                lifecycle = {
                    "capability": "unavailable",
                    "reviewRequired": True,
                    "error": str(exc),
                }
            if lifecycle["reviewRequired"]:
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
                "upstreamLifecycle": lifecycle,
                **({"error": error} if error else {}),
            }
        )
    overall = "needsReview" if any(item["status"] != "verified" for item in results) else "verified"
    return {
        "schema": document_schema(contract),
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
    details: list[str] = []
    for item in report["watchers"]:
        current = item["currentCommit"] or "unavailable"
        paths = ", ".join(f"`{path}`" for path in item["relatedLocalPaths"]) or "—"
        lines.append(
            f"| `{item['id']}` | `{item['ref']}` | **{item['status']}** | "
            f"`{item['reviewedCommit']}` | `{current}` | {paths} |"
        )
        if item.get("error"):
            details.append(f"Error `{item['id']}`: `{item['error']}`")
        lifecycle = item["upstreamLifecycle"]
        if lifecycle["capability"] == "github":
            summary = lifecycle["summary"]
            details.append(
                f"Lifecycle `{item['id']}`: eligible `{summary['eligible']}`, "
                f"blocked `{summary['blocked']}`, pending `{summary['pending']}`, "
                f"deferred `{summary['deferred']}`, declined `{summary['declined']}`, "
                f"issues `{summary['issues']}`. "
                "Only eligible PRs may authorize a Tumoflip implementation task."
            )
        elif lifecycle["capability"] == "unavailable":
            details.append(
                f"Lifecycle `{item['id']}` unavailable: `{lifecycle['error']}`"
            )
    if details:
        lines.append("")
        lines.extend(f"- {item}" for item in details)
    lines.extend(
        [
            "",
            "This watcher is review-only. A changed or unavailable source never updates the baseline automatically.",
            "Open, declined, and issue-only upstream records never authorize creating a Tumoflip implementation task.",
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
    contract: dict[str, Any] = {}
    try:
        contract = read_json(args.contract)
        report = scan(contract)
    except WatchError as error:
        report = {
            "schema": document_schema(contract),
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
