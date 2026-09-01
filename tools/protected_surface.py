#!/usr/bin/env python3
"""Detect drift in the Tumoflip protected application surface.

The protected-app audit proves exact package bytes. This tool proves that the
reviewed Tumoflip implementation and its application surface are still the
ones covered by the audit contracts. It never builds, publishes, or changes a
repository; a non-zero result is a review signal only.
"""

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


class SurfaceError(ValueError):
    """Raised when an inventory or contract is incomplete."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SurfaceError(f"unable to read JSON: {path}") from error
    if not isinstance(value, dict):
        raise SurfaceError(f"JSON root must be an object: {path}")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SurfaceError(f"{label} must be a non-empty string")
    return value


def require_commit(value: Any, label: str) -> str:
    value = require_string(value, label)
    if not HEX_40.fullmatch(value):
        raise SurfaceError(f"{label} must be a full commit SHA")
    return value


def require_repo_path(value: Any, label: str) -> str:
    path = require_string(value, label)
    parts = path.split("/")
    if path.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise SurfaceError(f"{label} must be a normalized repository-relative path")
    return path


def validate_contract(document: dict[str, Any]) -> dict[str, Any]:
    schema = document.get("schema")
    if schema not in {1, 2} or document.get("kind") != "protectedSurface":
        raise SurfaceError("protected surface contract has an unsupported schema or kind")
    require_string(document.get("firmwareRepository"), "firmwareRepository")
    implementations = document.get("reviewedImplementations")
    if not isinstance(implementations, dict) or not implementations:
        raise SurfaceError("reviewedImplementations must be a non-empty object")
    for name, item in implementations.items():
        if not isinstance(item, dict):
            raise SurfaceError(f"reviewedImplementations.{name} must be an object")
        require_string(item.get("ref"), f"reviewedImplementations.{name}.ref")
        require_commit(item.get("commit"), f"reviewedImplementations.{name}.commit")
    owned = document.get("ownedSourcePaths")
    if not isinstance(owned, list) or not all(isinstance(item, str) and item for item in owned):
        raise SurfaceError("ownedSourcePaths must be a list of non-empty strings")
    owned = [require_repo_path(item, "ownedSourcePaths entry") for item in owned]
    if not all(item.startswith("applications_user/") for item in owned):
        raise SurfaceError("ownedSourcePaths entries must be under applications_user/")
    if len(set(owned)) != len(owned):
        raise SurfaceError("ownedSourcePaths contains duplicates")
    owned_by_implementation = document.get("ownedSourcePathsByImplementation", {})
    if schema == 2:
        if not isinstance(owned_by_implementation, dict):
            raise SurfaceError("ownedSourcePathsByImplementation must be an object")
        if set(owned_by_implementation) != set(implementations):
            raise SurfaceError(
                "ownedSourcePathsByImplementation must match reviewed implementations"
            )
    elif "ownedSourcePathsByImplementation" in document:
        raise SurfaceError("schema 1 cannot contain ownedSourcePathsByImplementation")
    branch_owned: list[str] = []
    for name, values in owned_by_implementation.items():
        if not isinstance(values, list) or not all(
            isinstance(item, str) and item for item in values
        ):
            raise SurfaceError(
                f"ownedSourcePathsByImplementation.{name} must be a string array"
            )
        normalized = [
            require_repo_path(item, f"ownedSourcePathsByImplementation.{name} entry")
            for item in values
        ]
        if not all(item.startswith("applications_user/") for item in normalized):
            raise SurfaceError(
                f"ownedSourcePathsByImplementation.{name} entries must be under applications_user/"
            )
        if len(set(normalized)) != len(normalized):
            raise SurfaceError(
                f"ownedSourcePathsByImplementation.{name} contains duplicates"
            )
        branch_owned.extend(normalized)
    if len(set(branch_owned)) != len(branch_owned):
        raise SurfaceError("branch-specific owned source paths must be unique")
    if set(owned) & set(branch_owned):
        raise SurfaceError("common and branch-specific owned source paths overlap")
    prefixes = document.get("reviewPrefixes")
    if not isinstance(prefixes, list) or not all(isinstance(item, str) and item for item in prefixes):
        raise SurfaceError("reviewPrefixes must be a list of non-empty strings")
    if len(set(prefixes)) != len(prefixes):
        raise SurfaceError("reviewPrefixes contains duplicates")
    for item in prefixes:
        if "//" in item:
            raise SurfaceError("reviewPrefixes entries must not contain repeated separators")
        require_repo_path(item.rstrip("/"), "reviewPrefixes entry")
    return document


def validate_registry(document: dict[str, Any]) -> list[dict[str, Any]]:
    apps = document.get("apps")
    if not isinstance(apps, list) or not apps:
        raise SurfaceError("protected registry apps must be a non-empty list")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, app in enumerate(apps):
        if not isinstance(app, dict):
            raise SurfaceError(f"registry apps[{index}] must be an object")
        app_id = require_string(app.get("id"), f"registry apps[{index}].id")
        path = require_repo_path(app.get("localSourcePath"), f"registry {app_id}.localSourcePath")
        if not path.startswith("applications_user/"):
            raise SurfaceError(f"registry {app_id}.localSourcePath must be under applications_user/")
        if app_id in seen_ids:
            raise SurfaceError(f"duplicate protected app id: {app_id}")
        if path in seen_paths:
            raise SurfaceError(f"duplicate protected source path: {path}")
        seen_ids.add(app_id)
        seen_paths.add(path)
        raw_surfaces = app.get("coverageSurfaces", [])
        if not isinstance(raw_surfaces, list):
            raise SurfaceError(f"registry {app_id}.coverageSurfaces must be an array")
        surfaces: list[dict[str, Any]] = []
        surface_ids: set[str] = set()
        for surface_index, surface in enumerate(raw_surfaces):
            if not isinstance(surface, dict):
                raise SurfaceError(
                    f"registry {app_id}.coverageSurfaces[{surface_index}] must be an object"
                )
            surface_id = require_string(
                surface.get("id"),
                f"registry {app_id}.coverageSurfaces[{surface_index}].id",
            )
            if surface_id in surface_ids:
                raise SurfaceError(
                    f"duplicate coverage surface id for {app_id}: {surface_id}"
                )
            surface_ids.add(surface_id)
            raw_paths = surface.get("sourcePaths")
            if not isinstance(raw_paths, list) or not raw_paths:
                raise SurfaceError(
                    f"registry {app_id}.{surface_id}.sourcePaths must be a non-empty array"
                )
            source_paths = [
                require_repo_path(
                    value, f"registry {app_id}.{surface_id}.sourcePaths entry"
                )
                for value in raw_paths
            ]
            if len(source_paths) != len(set(source_paths)):
                raise SurfaceError(
                    f"duplicate coverage source path for {app_id}: {surface_id}"
                )
            raw_capabilities = surface.get("capabilities")
            if not isinstance(raw_capabilities, list) or not raw_capabilities:
                raise SurfaceError(
                    f"registry {app_id}.{surface_id}.capabilities must be a non-empty array"
                )
            capabilities = [
                require_string(
                    value, f"registry {app_id}.{surface_id}.capabilities entry"
                )
                for value in raw_capabilities
            ]
            if len(capabilities) != len(set(capabilities)):
                raise SurfaceError(
                    f"duplicate coverage capability for {app_id}: {surface_id}"
                )
            surfaces.append(
                {
                    "id": surface_id,
                    "sourcePaths": source_paths,
                    "capabilities": capabilities,
                }
            )
        result.append(
            {"id": app_id, "localSourcePath": path, "coverageSurfaces": surfaces}
        )
    return result


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SurfaceError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", *args], cwd=repo, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


def commit_relation(repo: Path, pin: str, current: str) -> str:
    """Classify an audit pin against the reviewed branch, fail-closed."""
    if not git_ok(repo, "cat-file", "-e", f"{pin}^{{commit}}"):
        return "unavailable"
    if pin == current:
        return "current"
    if git_ok(repo, "merge-base", "--is-ancestor", pin, current):
        return "behind"
    if git_ok(repo, "merge-base", "--is-ancestor", current, pin):
        return "ahead"
    return "diverged"


def discover_roots(repo: Path, ref: str) -> dict[str, list[str]]:
    paths = git(repo, "ls-tree", "-r", "--name-only", ref, "applications_user/").splitlines()
    roots: dict[str, list[str]] = {}
    for path in paths:
        if not path.endswith("/application.fam"):
            continue
        root = str(Path(path).parent)
        text = git(repo, "show", f"{ref}:{path}")
        app_ids = sorted(set(re.findall(r"\bappid\s*=\s*[\"']([^\"']+)", text)))
        roots[root] = app_ids
    return roots


def changed_paths(repo: Path, baseline: str, current: str) -> list[str]:
    return git(repo, "diff", "--name-only", f"{baseline}..{current}").splitlines()


def is_under(path: str, prefix: str) -> bool:
    return path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")


def classify_coverage_changes(
    paths: Iterable[str], registry: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Map changed implementation paths to explicit protected feature surfaces."""

    changes: list[dict[str, Any]] = []
    for app in registry:
        for surface in app["coverageSurfaces"]:
            matched = sorted(
                {
                    path
                    for path in paths
                    if any(
                        is_under(path, source_path)
                        for source_path in surface["sourcePaths"]
                    )
                }
            )
            if matched:
                changes.append(
                    {
                        "appId": app["id"],
                        "surfaceId": surface["id"],
                        "capabilities": surface["capabilities"],
                        "changedPaths": matched,
                    }
                )
    return sorted(changes, key=lambda item: (item["appId"], item["surfaceId"]))


def branch_report(
    *,
    repo: Path,
    name: str,
    ref: str,
    baseline: str,
    known_roots: set[str],
    review_prefixes: list[str],
    registry: list[dict[str, Any]],
) -> dict[str, Any]:
    current = require_commit(git(repo, "rev-parse", ref), f"{name}.currentCommit")
    if not git_ok(repo, "cat-file", "-e", f"{baseline}^{{commit}}"):
        raise SurfaceError(f"{name} baseline commit is unavailable: {baseline}")
    if not git_ok(repo, "merge-base", "--is-ancestor", baseline, current):
        raise SurfaceError(f"{name} baseline is not an ancestor of current ref")
    paths = changed_paths(repo, baseline, current)
    roots = discover_roots(repo, ref)
    current_roots = set(roots)
    unclassified = sorted(current_roots - known_roots)
    removed = sorted(known_roots - current_roots)
    protected_changes = sorted(
        path for path in paths if any(is_under(path, root) for root in known_roots)
    )
    review_changes = sorted(
        path for path in paths if any(is_under(path, prefix) for prefix in review_prefixes)
    )
    coverage_surface_changes = classify_coverage_changes(paths, registry)
    ahead = int(git(repo, "rev-list", "--count", f"{baseline}..{current}"))
    status = "verified"
    if unclassified or removed or protected_changes or review_changes:
        status = "needsReview"
    elif current != baseline:
        status = "baselineStale"
    return {
        "name": name,
        "ref": ref,
        "baselineCommit": baseline,
        "currentCommit": current,
        "aheadBy": ahead,
        "status": status,
        "roots": [
            {"path": path, "appIds": roots[path], "classification": "tracked" if path in known_roots else "unclassified"}
            for path in sorted(roots)
        ],
        "unclassifiedRoots": unclassified,
        "removedRoots": removed,
        "protectedChanges": protected_changes,
        "reviewChanges": review_changes,
        "coverageSurfaceChanges": coverage_surface_changes,
    }


def audit_pin_entry(
    *,
    repo: Path,
    source: str,
    branch: str,
    pin: str,
    current: str,
    known_roots: set[str],
    review_prefixes: list[str],
    registry: list[dict[str, Any]],
) -> dict[str, Any]:
    relation = commit_relation(repo, pin, current)
    entry: dict[str, Any] = {
        "source": source,
        "branch": branch,
        "commit": pin,
        "currentCommit": current,
        "relation": relation,
        "status": relation,
        "stale": relation != "current",
        "requiresReview": relation != "current",
        "changedPaths": [],
        "protectedChanges": [],
        "reviewChanges": [],
        "coverageSurfaceChanges": [],
        "addedRoots": [],
        "removedRoots": [],
    }
    if relation != "behind":
        return entry

    paths = changed_paths(repo, pin, current)
    roots_at_pin = set(discover_roots(repo, pin))
    roots_at_current = set(discover_roots(repo, current))
    protected_changes = sorted(
        path for path in paths if any(is_under(path, root) for root in known_roots)
    )
    review_changes = sorted(
        path for path in paths if any(is_under(path, prefix) for prefix in review_prefixes)
    )
    coverage_surface_changes = classify_coverage_changes(paths, registry)
    added_roots = sorted(roots_at_current - roots_at_pin)
    removed_roots = sorted(roots_at_pin - roots_at_current)
    relevant = bool(protected_changes or review_changes or added_roots or removed_roots)
    entry.update(
        {
            "status": "behindRelevant" if relevant else "behindUnrelated",
            "requiresReview": relevant,
            "changedPaths": paths,
            "protectedChanges": protected_changes,
            "reviewChanges": review_changes,
            "coverageSurfaceChanges": coverage_surface_changes,
            "addedRoots": added_roots,
            "removedRoots": removed_roots,
        }
    )
    return entry


def audit_pin_report(
    *,
    repo: Path,
    ref_by_name: dict[str, str],
    targets_path: Path | None,
    parity_path: Path | None,
    known_roots_by_branch: dict[str, set[str]],
    review_prefixes: list[str],
    registry: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    if targets_path is not None and targets_path.exists():
        targets = read_json(targets_path)
        implementations = targets.get("implementations", {})
        for channel, branch in (("dev", "dev"), ("stable", "main")):
            item = implementations.get(channel)
            if not isinstance(item, dict) or branch not in ref_by_name:
                continue
            pin = require_commit(item.get("commit"), f"audit targets {channel}.commit")
            current = require_commit(git(repo, "rev-parse", ref_by_name[branch]), f"{channel}.current")
            pins.append(
                audit_pin_entry(
                    repo=repo,
                    source=f"protected-audit-targets.{channel}",
                    branch=branch,
                    pin=pin,
                    current=current,
                    known_roots=known_roots_by_branch[branch],
                    review_prefixes=review_prefixes,
                    registry=registry,
                )
            )
    if parity_path is not None and parity_path.exists() and "dev" in ref_by_name:
        parity = read_json(parity_path)
        item = parity.get("implementation")
        if isinstance(item, dict):
            pin = require_commit(item.get("commit"), "protected-source-parity.commit")
            current = require_commit(git(repo, "rev-parse", ref_by_name["dev"]), "dev.current")
            pins.append(
                audit_pin_entry(
                    repo=repo,
                    source="protected-source-parity",
                    branch="dev",
                    pin=pin,
                    current=current,
                    known_roots=known_roots_by_branch["dev"],
                    review_prefixes=review_prefixes,
                    registry=registry,
                )
            )
    return pins


def scan(
    *,
    repo: Path,
    contract_path: Path,
    registry_path: Path,
    refs: dict[str, str],
    targets_path: Path | None = None,
    parity_path: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    contract = validate_contract(read_json(contract_path))
    registry = validate_registry(read_json(registry_path))
    upstream_roots = {item["localSourcePath"] for item in registry}
    common_owned_roots = set(contract["ownedSourcePaths"])
    branch_owned_roots = {
        name: set(contract.get("ownedSourcePathsByImplementation", {}).get(name, []))
        for name in contract["reviewedImplementations"]
    }
    owned_roots = common_owned_roots | set().union(*branch_owned_roots.values())
    overlap = upstream_roots & owned_roots
    if overlap:
        raise SurfaceError(f"owned and upstream source paths overlap: {sorted(overlap)}")
    known_roots_by_branch = {
        name: upstream_roots | common_owned_roots | branch_owned_roots[name]
        for name in contract["reviewedImplementations"]
    }
    known_roots = set().union(*known_roots_by_branch.values())
    reports: list[dict[str, Any]] = []
    for name, item in contract["reviewedImplementations"].items():
        if name not in refs:
            raise SurfaceError(f"missing current ref for reviewed implementation: {name}")
        reports.append(
            branch_report(
                repo=repo,
                name=name,
                ref=refs[name],
                baseline=require_commit(item.get("commit"), f"{name}.commit"),
                known_roots=known_roots_by_branch[name],
                review_prefixes=contract["reviewPrefixes"],
                registry=registry,
            )
        )
    pins = audit_pin_report(
        repo=repo,
        ref_by_name=refs,
        targets_path=targets_path,
        parity_path=parity_path,
        known_roots_by_branch=known_roots_by_branch,
        review_prefixes=contract["reviewPrefixes"],
        registry=registry,
    )
    status = "verified"
    if any(item["status"] == "needsReview" for item in reports) or any(
        item["requiresReview"] for item in pins
    ):
        status = "needsReview"
    elif any(item["status"] == "baselineStale" for item in reports):
        status = "baselineStale"
    return {
        "schema": SCHEMA,
        "kind": "protectedSurfaceReport",
        "generatedAt": generated_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "knownRoots": sorted(known_roots),
        "upstreamRoots": sorted(upstream_roots),
        "ownedRoots": sorted(owned_roots),
        "ownedRootsByImplementation": {
            name: sorted(values) for name, values in branch_owned_roots.items()
        },
        "branches": reports,
        "auditPins": pins,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "<!-- tumoflip-implementation-drift -->",
        "# Tumoflip implementation drift",
        "",
        f"- Status: **{report['status']}**",
        f"- Generated: `{report['generatedAt']}`",
        f"- Known application roots: `{len(report['knownRoots'])}`",
        "",
        "## Branches",
        "",
        "| Branch | Status | Baseline | Current | Ahead | Protected changes | Unclassified | Removed |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    if report.get("error"):
        lines.extend([f"- Error: `{report['error']}`", ""])
    for item in report["branches"]:
        lines.append(
            f"| `{item['name']}` | **{item['status']}** | `{item['baselineCommit']}` | "
            f"`{item['currentCommit']}` | {item['aheadBy']} | {len(item['protectedChanges']) + len(item['reviewChanges'])} | "
            f"{len(item['unclassifiedRoots'])} | {len(item['removedRoots'])} |"
        )
    lines.extend(["", "## Review details", ""])
    for item in report["branches"]:
        lines.append(f"### {item['name']}")
        for label, values in (
            ("Protected or review paths", item["reviewChanges"]),
            ("Protected application paths", item["protectedChanges"]),
            ("Unclassified application roots", item["unclassifiedRoots"]),
            ("Removed application roots", item["removedRoots"]),
        ):
            if values:
                lines.append(f"- {label}:")
                lines.extend(f"  - `{value}`" for value in values)
        for surface in item.get("coverageSurfaceChanges", []):
            capabilities = ", ".join(surface["capabilities"])
            lines.append(
                f"- Protected feature surface `{surface['appId']}/{surface['surfaceId']}` "
                f"({capabilities}):"
            )
            lines.extend(
                f"  - `{value}`" for value in surface["changedPaths"]
            )
        if item["status"] == "verified":
            lines.append("- No protected surface drift detected.")
    if report["auditPins"]:
        lines.extend(["", "## Audit pins", ""])
        for item in report["auditPins"]:
            state = item["status"]
            lines.append(
                f"- `{item['source']}` on `{item['branch']}`: **{state}**, "
                f"pin `{item['commit']}`, current `{item['currentCommit']}`."
            )
            for label, values in (
                ("Changed paths", item.get("changedPaths", [])),
                ("Protected application paths", item.get("protectedChanges", [])),
                ("Review paths", item.get("reviewChanges", [])),
                ("Added application roots", item.get("addedRoots", [])),
                ("Removed application roots", item.get("removedRoots", [])),
            ):
                if values:
                    lines.append(f"  - {label}:")
                    lines.extend(f"    - `{value}`" for value in values)
            for surface in item.get("coverageSurfaceChanges", []):
                capabilities = ", ".join(surface["capabilities"])
                lines.append(
                    f"  - Protected feature surface "
                    f"`{surface['appId']}/{surface['surfaceId']}` ({capabilities}):"
                )
                lines.extend(
                    f"    - `{value}`" for value in surface["changedPaths"]
                )
    lines.extend(
        [
            "",
            "This report is review-only. It never changes firmware, packages, releases, or audit pins automatically.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_refs(values: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SurfaceError(f"ref must use NAME=GIT_REF: {value}")
        name, ref = value.split("=", 1)
        if not name or not ref or name in result:
            raise SurfaceError(f"invalid or duplicate ref: {value}")
        result[name] = ref
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan", choices=["scan"])
    parser.add_argument("--firmware-repo", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--audit-targets", type=Path)
    parser.add_argument("--parity-contract", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = scan(
            repo=args.firmware_repo,
            contract_path=args.contract,
            registry_path=args.registry,
            refs=parse_refs(args.ref),
            targets_path=args.audit_targets,
            parity_path=args.parity_contract,
        )
    except SurfaceError as error:
        report = {
            "schema": SCHEMA,
            "kind": "protectedSurfaceReport",
            "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "status": "needsReview",
            "error": str(error),
            "knownRoots": [],
            "upstreamRoots": [],
            "ownedRoots": [],
            "branches": [],
            "auditPins": [],
        }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    return 1 if report["status"] == "needsReview" else 0


if __name__ == "__main__":
    raise SystemExit(main())
