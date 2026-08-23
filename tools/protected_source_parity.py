#!/usr/bin/env python3
"""Fail-closed audit of protected-app upstream commits versus Tumoflip imports.

The Community Pack audit proves package bytes. This control proves the separate
source-import claim: every protected app must name the upstream commit that was
reviewed and the Tumoflip implementation commit that imported or adapted it.
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
SCHEMA = 1


class ParityError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ParityError(f"invalid JSON {path}: {error}") from error
    if not isinstance(document, dict):
        raise ParityError(f"JSON root must be an object: {path}")
    return document


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def require_commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX_40.fullmatch(value):
        raise ParityError(f"{label} must be a full 40-character lowercase commit")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ParityError(f"{label} must be a non-empty string")
    return value


def validate_inputs(registry: dict[str, Any], imports: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if registry.get("schema") != 2:
        raise ParityError("protected app registry must use schema 2")
    raw_apps = registry.get("apps")
    if not isinstance(raw_apps, list) or not raw_apps:
        raise ParityError("protected app registry apps must be a non-empty array")
    registry_apps: dict[str, Any] = {}
    for index, app in enumerate(raw_apps):
        if not isinstance(app, dict):
            raise ParityError(f"registry apps[{index}] must be an object")
        app_id = require_string(app.get("id"), f"registry apps[{index}].id")
        if app_id in registry_apps:
            raise ParityError(f"duplicate registry app id: {app_id}")
        author = app.get("author")
        if not isinstance(author, dict):
            raise ParityError(f"registry {app_id}.author must be an object")
        require_string(author.get("repository"), f"registry {app_id}.author.repository")
        require_string(author.get("ref"), f"registry {app_id}.author.ref")
        require_commit(author.get("lastReviewedCommit"), f"registry {app_id}.author.lastReviewedCommit")
        require_string(app.get("localSourcePath"), f"registry {app_id}.localSourcePath")
        registry_apps[app_id] = app

    if imports.get("schema") != SCHEMA:
        raise ParityError(f"protected source imports must use schema {SCHEMA}")
    implementation = imports.get("implementation")
    if not isinstance(implementation, dict):
        raise ParityError("imports implementation must be an object")
    require_string(implementation.get("repository"), "imports implementation.repository")
    implementation_commit = require_commit(
        implementation.get("commit"), "imports implementation.commit"
    )
    raw_imports = imports.get("imports")
    if not isinstance(raw_imports, list) or not raw_imports:
        raise ParityError("imports must be a non-empty array")
    import_map: dict[str, Any] = {}
    for index, item in enumerate(raw_imports):
        if not isinstance(item, dict):
            raise ParityError(f"imports[{index}] must be an object")
        app_id = require_string(item.get("appId"), f"imports[{index}].appId")
        if app_id in import_map:
            raise ParityError(f"duplicate import app id: {app_id}")
        require_string(item.get("localSourcePath"), f"imports {app_id}.localSourcePath")
        require_commit(item.get("implementationCommit"), f"imports {app_id}.implementationCommit")
        require_string(item.get("upstreamRepository"), f"imports {app_id}.upstreamRepository")
        require_string(item.get("upstreamRef"), f"imports {app_id}.upstreamRef")
        require_commit(item.get("upstreamCommit"), f"imports {app_id}.upstreamCommit")
        import_map[app_id] = item

    if set(import_map) != set(registry_apps):
        missing = sorted(set(registry_apps) - set(import_map))
        extra = sorted(set(import_map) - set(registry_apps))
        raise ParityError(f"import registry mismatch: missing={missing}, extra={extra}")
    return registry_apps, {"implementation": {**implementation, "commit": implementation_commit}, "imports": import_map}


def git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    ).returncode == 0


def fetch_head(repository: str, ref: str, fixtures: dict[str, str]) -> str:
    fixture_key = f"{repository} {ref}"
    if fixture_key in fixtures:
        return require_commit(fixtures[fixture_key], f"fixture {fixture_key}")
    try:
        result = subprocess.run(
            ["git", "ls-remote", repository, ref],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ParityError(f"unable to resolve {repository} {ref}: {error}") from error
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode or len(rows) != 1 or len(rows[0]) < 2:
        raise ParityError(f"missing or ambiguous upstream ref: {repository} {ref}")
    return require_commit(rows[0][0], f"upstream {repository} {ref}")


def load_fixtures(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    document = read_json(path)
    raw = document.get("heads")
    if not isinstance(raw, dict):
        raise ParityError("heads fixture must contain a heads object")
    return {require_string(k, "fixture key"): require_commit(v, f"fixture {k}") for k, v in raw.items()}


def scan(
    *,
    registry_path: Path,
    imports_path: Path,
    implementation_repo: Path,
    community_commit: str | None,
    author_heads: Path | None,
    generated_at: str | None,
) -> dict[str, Any]:
    registry_apps, contract = validate_inputs(read_json(registry_path), read_json(imports_path))
    fixtures = load_fixtures(author_heads)
    implementation_commit = contract["implementation"]["commit"]
    if not git_ok(implementation_repo, "cat-file", "-e", f"{implementation_commit}^{{commit}}"):
        raise ParityError(f"implementation commit is unavailable: {implementation_commit}")
    if not git_ok(implementation_repo, "merge-base", "--is-ancestor", implementation_commit, "HEAD"):
        raise ParityError(f"implementation commit is not an ancestor of checkout HEAD: {implementation_commit}")

    results: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for app_id in sorted(registry_apps):
        app = registry_apps[app_id]
        author = app["author"]
        item = contract["imports"][app_id]
        errors: list[str] = []
        if item["localSourcePath"] != app["localSourcePath"]:
            errors.append("local source path differs from protected registry")
        if item["upstreamRepository"] != author["repository"] or item["upstreamRef"] != author["ref"]:
            errors.append("upstream source differs from protected registry")
        implementation_item_commit = item["implementationCommit"]
        if not git_ok(implementation_repo, "cat-file", "-e", f"{implementation_item_commit}^{{commit}}"):
            errors.append(f"implementation commit unavailable: {implementation_item_commit}")
        elif not git_ok(implementation_repo, "merge-base", "--is-ancestor", implementation_item_commit, "HEAD"):
            errors.append(f"implementation commit is not reachable: {implementation_item_commit}")
        elif not git_ok(implementation_repo, "cat-file", "-e", f"{implementation_item_commit}:{item['localSourcePath']}"):
            errors.append(f"local source path missing at implementation commit: {item['localSourcePath']}")

        if author["ref"] == "release-source":
            if not community_commit:
                errors.append("release-source requires an exact Community Pack commit")
                current_commit = None
            else:
                current_commit = require_commit(community_commit, "community commit")
        else:
            try:
                current_commit = fetch_head(author["repository"], author["ref"], fixtures)
            except ParityError as error:
                current_commit = None
                errors.append(str(error))
        if current_commit and current_commit != item["upstreamCommit"]:
            errors.append(
                f"upstream changed: reviewed={item['upstreamCommit']} current={current_commit}"
            )
        status = "needsReview" if errors else "verified"
        if errors:
            unresolved.append(app_id)
        results.append(
            {
                "appId": app_id,
                "status": status,
                "localSourcePath": item["localSourcePath"],
                "implementationCommit": implementation_item_commit,
                "upstream": {
                    "repository": item["upstreamRepository"],
                    "ref": item["upstreamRef"],
                    "reviewedCommit": item["upstreamCommit"],
                    "currentCommit": current_commit,
                },
                "errors": errors,
            }
        )
    return {
        "schema": SCHEMA,
        "generatedAt": generated_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "implementation": {
            "repository": contract["implementation"]["repository"],
            "commit": implementation_commit,
        },
        "overallStatus": "needsReview" if unresolved else "verified",
        "unresolved": unresolved,
        "apps": results,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Protected source parity",
        "",
        f"- status: **{report['overallStatus']}**",
        f"- implementation: `{report['implementation']['repository']}@{report['implementation']['commit']}`",
        f"- unresolved apps: `{len(report['unresolved'])}`",
        "",
        "| App | Status | Reviewed upstream | Current upstream | Details |",
        "| --- | --- | --- | --- | --- |",
    ]
    for app in report["apps"]:
        upstream = app["upstream"]
        details = "; ".join(app["errors"]) or "source import recorded"
        lines.append(
            f"| `{app['appId']}` | `{app['status']}` | `{upstream['reviewedCommit']}` | "
            f"`{upstream['currentCommit'] or 'unresolved'}` | {details} |"
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--imports", type=Path, required=True)
    parser.add_argument("--implementation-repo", type=Path, required=True)
    parser.add_argument("--community-commit")
    parser.add_argument("--author-heads", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--generated-at")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = scan(
            registry_path=args.registry,
            imports_path=args.imports,
            implementation_repo=args.implementation_repo,
            community_commit=args.community_commit,
            author_heads=args.author_heads,
            generated_at=args.generated_at,
        )
    except ParityError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    write_json(args.output, report)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"overallStatus": report["overallStatus"], "unresolved": report["unresolved"]}))
    return 1 if report["unresolved"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
