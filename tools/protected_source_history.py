#!/usr/bin/env python3
"""Audit protected Community Pack source paths across every published release.

The daily parity scan answers whether the current release is compatible with the
recorded Tumoflip import. This historical scan answers where a protected source
directory changed over the complete release history, while treating the
registry's last-reviewed release as the explicit human boundary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


HEX_40 = re.compile(r"^[0-9a-f]{40}$")
RELEASE_TAG = re.compile(r"^\d{1,2}[a-z]{3}\d{4}(p\d+)?$")
SCHEMA = 1


class HistoryError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HistoryError(f"invalid JSON {path}: {error}") from error


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoryError(f"{label} must be a non-empty string")
    return value


def require_commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX_40.fullmatch(value):
        raise HistoryError(f"{label} must be a full 40-character lowercase commit")
    return value


def git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    if result.returncode:
        detail = result.stderr.strip() or "git command failed"
        raise HistoryError(f"{detail}: git {' '.join(args)}")
    return result.stdout.strip()


def git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    ).returncode == 0


def path_exists(repo: Path, commit: str, path: str) -> bool:
    return git_ok(repo, "cat-file", "-e", f"{commit}:{path}")


def path_changed(repo: Path, previous: str, current: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "diff", "--quiet", previous, current, "--", path],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.strip() or "git diff failed"
        raise HistoryError(f"unable to compare {path}: {detail}")
    return result.returncode == 1


def load_apps(registry_path: Path) -> list[dict[str, str]]:
    document = read_json(registry_path)
    if not isinstance(document, dict) or document.get("schema") != 2:
        raise HistoryError("protected app registry must use schema 2")
    apps = document.get("apps")
    if not isinstance(apps, list) or not apps:
        raise HistoryError("protected app registry apps must be a non-empty array")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, app in enumerate(apps):
        if not isinstance(app, dict):
            raise HistoryError(f"registry apps[{index}] must be an object")
        author = app.get("author")
        if not isinstance(author, dict):
            raise HistoryError(f"registry apps[{index}].author must be an object")
        if author.get("ref") != "release-source":
            continue
        app_id = require_string(app.get("id"), f"registry apps[{index}].id")
        if app_id in seen:
            raise HistoryError(f"duplicate registry app id: {app_id}")
        seen.add(app_id)
        result.append(
            {
                "appId": app_id,
                "path": require_string(app.get("packSourcePath"), f"registry {app_id}.packSourcePath"),
                "reviewedCommit": require_commit(
                    author.get("lastReviewedCommit"),
                    f"registry {app_id}.author.lastReviewedCommit",
                ),
            }
        )
    if not result:
        raise HistoryError("registry has no release-source protected apps")
    return result


def load_releases(path: Path, repo: Path) -> list[dict[str, str]]:
    raw = read_json(path)
    if not isinstance(raw, list) or not raw:
        raise HistoryError("release list must be a non-empty array")
    releases: list[dict[str, str]] = []
    seen_tags: set[str] = set()
    seen_commits: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise HistoryError(f"release list[{index}] must be an object")
        tag = require_string(item.get("tag"), f"release list[{index}].tag")
        if not RELEASE_TAG.fullmatch(tag):
            raise HistoryError(f"release tag is not a published Community Pack tag: {tag}")
        if tag in seen_tags:
            raise HistoryError(f"duplicate release tag: {tag}")
        seen_tags.add(tag)
        published_at = require_string(item.get("publishedAt"), f"release list[{index}].publishedAt")
        commit = require_commit(git_output(repo, "rev-parse", f"{tag}^{{commit}}"), f"release {tag}")
        if commit in seen_commits:
            raise HistoryError(f"duplicate release commit: {commit}")
        seen_commits.add(commit)
        releases.append({"tag": tag, "publishedAt": published_at, "commit": commit})
    releases.sort(key=lambda item: (item["publishedAt"], item["tag"]))
    return releases


def after_review_boundary(repo: Path, reviewed: str, commit: str) -> bool:
    if commit == reviewed:
        return False
    if not git_ok(repo, "cat-file", "-e", f"{reviewed}^{{commit}}"):
        raise HistoryError(f"review boundary commit is unavailable: {reviewed}")
    if git_ok(repo, "merge-base", "--is-ancestor", reviewed, commit):
        return True
    if git_ok(repo, "merge-base", "--is-ancestor", commit, reviewed):
        return False
    raise HistoryError(
        f"review boundary and release commit are not on one history: {reviewed} <-> {commit}"
    )


def scan(
    *,
    registry_path: Path,
    releases_path: Path,
    community_repo: Path,
    community_head: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    apps = load_apps(registry_path)
    releases = load_releases(releases_path, community_repo)
    head = require_commit(git_output(community_repo, "rev-parse", "HEAD"), "Community Pack HEAD")
    if community_head is not None and head != require_commit(community_head, "expected Community Pack HEAD"):
        raise HistoryError(
            f"Community Pack checkout does not match expected latest commit: expected={community_head}, actual={head}"
        )
    events: list[dict[str, Any]] = []
    unresolved: list[str] = []
    app_summaries: list[dict[str, Any]] = []
    for app in apps:
        previous_commit: str | None = None
        previous_present = False
        app_events: list[dict[str, Any]] = []
        for release in releases:
            present = path_exists(community_repo, release["commit"], app["path"])
            if not present:
                if previous_present:
                    needs_review = after_review_boundary(
                        community_repo, app["reviewedCommit"], release["commit"]
                    )
                    event = {
                        "appId": app["appId"],
                        "kind": "pathMissing",
                        "path": app["path"],
                        "tag": release["tag"],
                        "commit": release["commit"],
                        "requiresReview": needs_review,
                        "note": "protected source path disappeared after being present in an earlier release",
                    }
                    app_events.append(event)
                    events.append(event)
                    if needs_review and app["appId"] not in unresolved:
                        unresolved.append(app["appId"])
                previous_present = False
                continue

            if not previous_present:
                needs_review = after_review_boundary(
                    community_repo, app["reviewedCommit"], release["commit"]
                )
                if previous_commit is None:
                    kind = "pathIntroduced"
                    note = "protected source path first appears in the release history"
                else:
                    kind = "pathRestored"
                    note = "protected source path reappeared after a missing release"
                event = {
                    "appId": app["appId"],
                    "kind": kind,
                    "path": app["path"],
                    "tag": release["tag"],
                    "commit": release["commit"],
                    "requiresReview": needs_review,
                    "note": note,
                }
                app_events.append(event)
                events.append(event)
                if needs_review and app["appId"] not in unresolved:
                    unresolved.append(app["appId"])
            elif previous_commit is not None and path_changed(
                community_repo, previous_commit, release["commit"], app["path"]
            ):
                needs_review = after_review_boundary(
                    community_repo, app["reviewedCommit"], release["commit"]
                )
                event = {
                    "appId": app["appId"],
                    "kind": "sourceChanged",
                    "path": app["path"],
                    "tag": release["tag"],
                    "commit": release["commit"],
                    "previousCommit": previous_commit,
                    "requiresReview": needs_review,
                    "note": "protected source path changed between consecutive published releases",
                }
                app_events.append(event)
                events.append(event)
                if needs_review and app["appId"] not in unresolved:
                    unresolved.append(app["appId"])
            previous_commit = release["commit"]
            previous_present = True

        app_summaries.append(
            {
                **app,
                "firstPresentRelease": next(
                    (release["tag"] for release in releases if path_exists(community_repo, release["commit"], app["path"])),
                    None,
                ),
                "eventCount": len(app_events),
                "unreviewedEventCount": sum(1 for event in app_events if event["requiresReview"]),
            }
        )
    return {
        "schema": SCHEMA,
        "generatedAt": generated_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "community": {
            "head": head,
            "firstRelease": releases[0],
            "latestRelease": releases[-1],
            "releaseCount": len(releases),
        },
        "apps": app_summaries,
        "events": events,
        "unresolved": sorted(unresolved),
        "overallStatus": "needsReview" if unresolved else "verified",
    }


def markdown(report: dict[str, Any]) -> str:
    community = report["community"]
    lines = [
        "# Protected Community Pack source history",
        "",
        "<!-- protected-source-history -->",
        "",
        f"- status: **{report['overallStatus']}**",
        f"- Community Pack releases checked: `{community['releaseCount']}`",
        f"- first release: `{community['firstRelease']['tag']}` at `{community['firstRelease']['commit']}`",
        f"- latest release: `{community['latestRelease']['tag']}` at `{community['latestRelease']['commit']}`",
        f"- checkout HEAD: `{community['head']}`",
        f"- historical transitions: `{len(report['events'])}`",
        f"- transitions after the reviewed boundary: `{sum(1 for event in report['events'] if event['requiresReview'])}`",
        "",
        "The report checks protected source directories across every published release. It does not replace the exact binary/archive audit.",
        "",
        "| App | Pack source path | Reviewed boundary | Transitions | After boundary | First present |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for app in report["apps"]:
        lines.append(
            f"| `{app['appId']}` | `{app['path']}` | `{app['reviewedCommit']}` | "
            f"{app['eventCount']} | {app['unreviewedEventCount']} | `{app['firstPresentRelease'] or 'none'}` |"
        )
    if report["events"]:
        lines.extend(["", "## Historical transitions", ""])
        for event in report["events"]:
            state = "needs review" if event["requiresReview"] else "covered by reviewed boundary"
            previous = f" from `{event['previousCommit']}`" if event.get("previousCommit") else ""
            lines.append(
                f"- `{event['tag']}` `{event['appId']}` `{event['kind']}`{previous}: **{state}** — {event['note']}."
            )
    if report["unresolved"]:
        lines.extend(["", f"Unresolved protected apps: `{', '.join(report['unresolved'])}`"])
    return "\n".join(lines) + "\n"


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--releases", type=Path, required=True)
    parser.add_argument("--community-repo", type=Path, required=True)
    parser.add_argument("--community-head")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--generated-at")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = scan(
            registry_path=args.registry,
            releases_path=args.releases,
            community_repo=args.community_repo,
            community_head=args.community_head,
            generated_at=args.generated_at,
        )
    except HistoryError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    write_json(args.output, report)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"overallStatus": report["overallStatus"], "unresolved": report["unresolved"]}))
    return 1 if report["unresolved"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
