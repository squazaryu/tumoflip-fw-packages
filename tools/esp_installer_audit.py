#!/usr/bin/env python3
"""Fail-closed audit for ESP32 Marauder installer manifests.

The audit is deliberately separate from ESP Flasher and FW Package
publication.  A release is only *observed* here until its manifest, carrier
bytes, segment recipe, and per-board hardware evidence have been reviewed.
Raw BIN files without a manifest never enter the acceptance path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
ROLES = {"bootloader", "partition-table", "ota-data", "application"}
UPSTREAM_REPOSITORY = "justcallmekoko/ESP32Marauder"
MANIFEST_NAME = "firmware-manifest.json"
ZIP_NAME = "marauder-installer-assets.zip"
ISSUE_MARKER = "<!-- esp-installer-audit -->"
ISSUE_TITLE = "Review ESP installer manifests before Flash Package acceptance"


class AuditError(RuntimeError):
    """Raised for an invalid or incomplete installer audit input."""


def _require(value: Any, label: str) -> Any:
    if value is None or value == "":
        raise AuditError(f"{label} is missing")
    return value


def _string(value: Any, label: str) -> str:
    value = _require(value, label)
    if not isinstance(value, str):
        raise AuditError(f"{label} must be a string")
    return value


def _sha(value: Any, label: str) -> str:
    value = _string(value, label)
    if HEX64.fullmatch(value) is None:
        raise AuditError(f"{label} must be lowercase SHA-256")
    return value


def _commit(value: Any, label: str) -> str:
    value = _string(value, label)
    if HEX40.fullmatch(value) is None:
        raise AuditError(f"{label} must be a full lowercase commit SHA")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AuditError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AuditError(f"{label} must be a non-negative integer")
    return value


def _safe_file(value: Any, label: str) -> str:
    value = _string(value, label)
    if SAFE_FILE.fullmatch(value) is None or value.startswith(".") or "/" in value or "\\" in value:
        raise AuditError(f"{label} is not a safe file name")
    return value


def _timestamp(value: Any, label: str) -> str:
    value = _string(value, label)
    if TIMESTAMP.fullmatch(value) is None:
        raise AuditError(f"{label} must be a UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise AuditError(f"{label} is not a valid timestamp") from error
    return value


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AuditError(f"cannot read JSON {path}: {error}") from error


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AuditError(f"{label} must be an object")
    return value


def validate_contract(value: Any) -> dict[str, Any]:
    contract = _object(value, "ESP installer audit contract")
    if contract.get("schema") != 1 or contract.get("kind") != "espInstallerAudit":
        raise AuditError("ESP installer audit contract schema is invalid")
    if contract.get("repository") != UPSTREAM_REPOSITORY:
        raise AuditError("ESP installer audit repository is not allow-listed")
    selection = _object(contract.get("selection"), "selection")
    if selection.get("releasePattern") != SAFE_TAG.pattern:
        raise AuditError("release selection pattern is not the stable release policy")
    if selection.get("includePrereleases") is not False:
        raise AuditError("pre-release assets must stay outside automatic acceptance")
    profiles = contract.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise AuditError("at least one board profile is required")
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(profiles):
        profile = _object(raw, f"profiles[{index}]")
        board = _string(profile.get("board"), f"profiles[{index}].board")
        recipe = _string(profile.get("recipe"), f"profiles[{index}].recipe")
        key = (board, recipe)
        if key in seen:
            raise AuditError(f"duplicate board profile: {board}/{recipe}")
        seen.add(key)
        status = _string(profile.get("status"), f"profiles[{index}].status")
        if status not in {"needsReview", "accepted", "rejected"}:
            raise AuditError(f"unsupported profile status: {status}")
        roles = profile.get("roles")
        offsets = profile.get("offsets")
        if not isinstance(roles, list) or not roles or any(role not in ROLES for role in roles):
            raise AuditError(f"profiles[{index}].roles is invalid")
        if not isinstance(offsets, list) or len(offsets) != len(roles):
            raise AuditError(f"profiles[{index}].offsets must match roles")
        for offset in offsets:
            _nonnegative_int(offset, f"profiles[{index}].offsets")
        sizes = profile.get("sizes")
        if not isinstance(sizes, list) or len(sizes) != len(roles):
            raise AuditError(f"profiles[{index}].sizes must match roles")
        for size in sizes:
            _nonnegative_int(size, f"profiles[{index}].sizes")
    evidence = _object(contract.get("hardwareEvidence"), "hardwareEvidence")
    checks = evidence.get("requiredChecks")
    if not isinstance(checks, list) or not checks or any(not isinstance(item, str) or not item for item in checks):
        raise AuditError("hardwareEvidence.requiredChecks is invalid")
    if evidence.get("minimumPerBoard") is not True:
        raise AuditError("hardware evidence must be required per board")
    return contract


def validate_baseline(value: Any) -> dict[str, Any]:
    """Validate the checked-in identity ledger used for mutable releases."""
    baseline = _object(value, "ESP installer audit baseline")
    if baseline.get("schema") != 1 or baseline.get("kind") != "espInstallerAuditBaseline":
        raise AuditError("ESP installer audit baseline schema is invalid")
    if baseline.get("repository") != UPSTREAM_REPOSITORY:
        raise AuditError("ESP installer audit baseline repository is not allow-listed")
    observed = baseline.get("observed")
    if not isinstance(observed, list):
        raise AuditError("ESP installer audit baseline observed list is invalid")
    seen: set[str] = set()
    for index, raw in enumerate(observed):
        item = _object(raw, f"observed[{index}]")
        tag = _string(item.get("tag"), f"observed[{index}].tag")
        if SAFE_TAG.fullmatch(tag) is None:
            raise AuditError(f"observed[{index}].tag is not a stable semver tag")
        if tag in seen:
            raise AuditError(f"duplicate observed release tag: {tag}")
        seen.add(tag)
        _nonnegative_int(item.get("releaseId"), f"observed[{index}].releaseId")
        _commit(item.get("sourceCommit"), f"observed[{index}].sourceCommit")
        _sha(item.get("carrierSha256"), f"observed[{index}].carrierSha256")
        _sha(item.get("manifestSha256"), f"observed[{index}].manifestSha256")
        _timestamp(item.get("observedAt"), f"observed[{index}].observedAt")
    return baseline


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_path(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise AuditError(f"cannot read carrier {path}: {error}") from error
    return digest.hexdigest(), size


def _zip_contents(path: Path) -> tuple[bytes, dict[str, bytes]]:
    try:
        with zipfile.ZipFile(path) as archive:
            members: dict[str, bytes] = {}
            for info in archive.infolist():
                name = info.filename
                if name.endswith("/"):
                    continue
                if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                    raise AuditError(f"unsafe ZIP member: {name}")
                if name in members:
                    raise AuditError(f"duplicate ZIP member: {name}")
                members[name] = archive.read(info)
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise AuditError(f"invalid installer carrier {path}: {error}") from error
    manifest_names = [name for name in members if Path(name).name == MANIFEST_NAME]
    if manifest_names != [MANIFEST_NAME]:
        raise AuditError("carrier must contain exactly one root firmware-manifest.json")
    return members[MANIFEST_NAME], members


def _carrier(path: Path) -> tuple[dict[str, Any], bytes, dict[str, bytes]]:
    if not path.is_file():
        raise AuditError(f"carrier does not exist: {path}")
    carrier_sha, carrier_bytes = _hash_path(path)
    if path.name == ZIP_NAME or path.suffix.lower() == ".zip":
        manifest_bytes, members = _zip_contents(path)
        kind = "marauder-installer-assets.zip"
    elif path.name == MANIFEST_NAME:
        try:
            manifest_bytes = path.read_bytes()
        except OSError as error:
            raise AuditError(f"cannot read manifest carrier: {error}") from error
        members = {MANIFEST_NAME: manifest_bytes}
        kind = "firmware-manifest.json"
    else:
        raise AuditError("carrier must be firmware-manifest.json or marauder-installer-assets.zip")
    return {
        "kind": kind,
        "assetName": path.name,
        "sha256": carrier_sha,
        "bytes": carrier_bytes,
    }, manifest_bytes, members


def _validate_segment(raw: Any, label: str) -> dict[str, Any]:
    segment = _object(raw, label)
    role = _string(segment.get("role"), f"{label}.role")
    if role not in ROLES:
        raise AuditError(f"{label}.role is not supported")
    file_name = _safe_file(segment.get("fileName"), f"{label}.fileName")
    offset = _nonnegative_int(segment.get("offset"), f"{label}.offset")
    size = _positive_int(segment.get("size"), f"{label}.size")
    digest = _sha(segment.get("sha256"), f"{label}.sha256")
    return {"role": role, "fileName": file_name, "offset": offset, "size": size, "sha256": digest}


def _segments(target: dict[str, Any], recipe: str, members: dict[str, bytes], label: str) -> tuple[list[dict[str, Any]], list[str]]:
    flash = _object(target.get("flash"), f"{label}.flash")
    selected = _object(flash.get(recipe), f"{label}.flash.{recipe}")
    raw_segments = selected.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments or len(raw_segments) > 4:
        raise AuditError(f"{label}.flash.{recipe}.segments is invalid")
    result = [_validate_segment(raw, f"{label}.segments[{i}]") for i, raw in enumerate(raw_segments)]
    roles: set[str] = set()
    files: set[str] = set()
    ordered = sorted(result, key=lambda item: item["offset"])
    for item in ordered:
        if item["role"] in roles or item["fileName"] in files:
            raise AuditError(f"{label} has duplicate segment role or file")
        roles.add(item["role"])
        files.add(item["fileName"])
    for previous, current in zip(ordered, ordered[1:]):
        if previous["offset"] + previous["size"] > current["offset"]:
            raise AuditError(f"{label} has overlapping flash segments")
    missing: list[str] = []
    for item in result:
        payload = members.get(item["fileName"])
        if payload is None:
            missing.append(item["fileName"])
            continue
        if len(payload) != item["size"]:
            raise AuditError(f"{item['fileName']} size differs from manifest")
        if _hash_bytes(payload) != item["sha256"]:
            raise AuditError(f"{item['fileName']} digest differs from manifest")
    return result, missing


def _profile(contract: dict[str, Any], board: str, recipe: str) -> dict[str, Any] | None:
    for raw in contract["profiles"]:
        if raw["board"] == board and raw["recipe"] == recipe:
            return raw
    return None


def _fingerprint(upstream: dict[str, Any], carrier: dict[str, Any], manifest_sha: str, board: str, recipe: str, segments: list[dict[str, Any]]) -> str:
    value = {
        "upstream": upstream,
        "carrier": carrier,
        "manifestSha256": manifest_sha,
        "board": board,
        "recipe": recipe,
        "segments": segments,
    }
    return _hash_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _decision(contract: dict[str, Any], board: str, recipe: str, segments: list[dict[str, Any]], evidence: list[dict[str, Any]], missing_artifacts: list[str]) -> tuple[str, list[str]]:
    profile = _profile(contract, board, recipe)
    if profile is None:
        return "needsReview", ["board/recipe is not in the reviewed policy"]
    reasons: list[str] = []
    if missing_artifacts:
        reasons.append("carrier does not include segment bytes: " + ", ".join(missing_artifacts))
    expected_sizes = profile["sizes"]
    actual_sizes = [item["size"] for item in segments]
    sizes_match = all(expected == 0 or expected == actual for expected, actual in zip(expected_sizes, actual_sizes))
    if (
        profile["roles"] != [item["role"] for item in segments]
        or profile["offsets"] != [item["offset"] for item in segments]
        or not sizes_match
    ):
        reasons.append("segment role/offset recipe differs from the reviewed policy")
    required = set(contract["hardwareEvidence"]["requiredChecks"])
    observed = {item.get("check") for item in evidence if isinstance(item, dict) and item.get("status") == "passed"}
    missing = sorted(required - observed)
    if missing:
        reasons.append("missing hardware evidence: " + ", ".join(missing))
    if profile["status"] != "accepted":
        reasons.append(f"policy status is {profile['status']}")
    if reasons:
        return "needsReview", reasons
    return "accepted", []


def build_report(contract: dict[str, Any], *, upstream: dict[str, Any], carrier: dict[str, Any], manifest_bytes: bytes, members: dict[str, bytes], evidence: list[dict[str, Any]] | None = None, generated_at: str | None = None, identity_change: str | None = None) -> dict[str, Any]:
    evidence = evidence or []
    manifest = _object(json.loads(manifest_bytes.decode("utf-8")), "firmware manifest")
    if manifest.get("schemaVersion") != 1 or manifest.get("kind") != "esp32-marauder-installer-release":
        raise AuditError("unsupported firmware manifest kind or schema")
    if manifest.get("metadataStatus") != "authoritative":
        raise AuditError("manifest is not authoritative")
    if manifest.get("sourceRepository") != UPSTREAM_REPOSITORY:
        raise AuditError("manifest source repository is not allow-listed")
    source_commit = _commit(manifest.get("sourceCommit"), "manifest.sourceCommit")
    if source_commit != upstream["sourceCommit"]:
        raise AuditError("release source commit differs from manifest source commit")
    version = _string(manifest.get("version"), "manifest.version")
    if not version.startswith("v"):
        raise AuditError("manifest.version is invalid")
    targets = manifest.get("targets")
    if not isinstance(targets, list) or not targets:
        raise AuditError("manifest.targets is empty")
    manifest_sha = _hash_bytes(manifest_bytes)
    candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(targets):
        target = _object(raw, f"targets[{index}]")
        board = _string(target.get("id"), f"targets[{index}].id")
        recipe = "factory"
        segments, missing_artifacts = _segments(target, recipe, members, f"targets[{index}]")
        decision, reasons = _decision(contract, board, recipe, segments, evidence, missing_artifacts)
        if identity_change:
            reasons.append(identity_change)
            decision = "needsReview"
        candidates.append({
            "board": board,
            "displayName": _string(target.get("displayName"), f"targets[{index}].displayName"),
            "recipe": recipe,
            "chipFamily": _string(target.get("chipFamily"), f"targets[{index}].chipFamily"),
            "segments": segments,
            "hardwareEvidence": evidence,
            "decision": decision,
            "reasons": reasons,
            "fingerprint": _fingerprint(upstream, carrier, manifest_sha, board, recipe, segments),
        })
    status = "verified" if all(item["decision"] == "accepted" for item in candidates) else "needsReview"
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _timestamp(generated_at, "generatedAt")
    return {
        "schema": 1,
        "kind": "espInstallerAuditReport",
        "generatedAt": generated_at,
        "status": status,
        "upstream": upstream,
        "carrier": carrier,
        "manifest": {"assetName": MANIFEST_NAME, "sha256": manifest_sha, "bytes": len(manifest_bytes), "version": version, "sourceCommit": source_commit},
        "identityChange": identity_change,
        "candidates": candidates,
        "automaticFlashPackageAuthorization": status == "verified",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [ISSUE_MARKER, "# ESP installer manifest audit", "", f"- Status: **{report['status']}**", f"- Automatic Flash Package authorization: **{'yes' if report['automaticFlashPackageAuthorization'] else 'no'}**", f"- Release: `{report['upstream']['tag']}` (id `{report['upstream']['releaseId']}`)", f"- Source commit: `{report['upstream']['sourceCommit']}`", f"- Carrier: `{report['carrier']['assetName']}` (`{report['carrier']['sha256']}`)", f"- Manifest: `{report['manifest']['sha256']}` ({report['manifest']['bytes']} bytes)", "", "## Board candidates", ""]
    if report.get("identityChange"):
        lines.extend([f"- **Identity change:** {report['identityChange']}", ""])
    for candidate in report["candidates"]:
        lines.append(f"- `{candidate['board']}` / `{candidate['recipe']}` — **{candidate['decision']}**; fingerprint `{candidate['fingerprint']}`")
        for reason in candidate["reasons"]:
            lines.append(f"  - {reason}")
    lines.extend(["", "Raw BIN files are never accepted without this manifest and carrier evidence. A needsReview result must not publish or expose a Flash Package.", ""])
    return "\n".join(lines)


def _github_json(url: str, token: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.HTTPError, ValueError) as error:
        raise AuditError(f"GitHub API request failed: {url}: {error}") from error


def _download(url: str, token: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                output.write(chunk)
    except OSError as error:
        raise AuditError(f"cannot download installer carrier: {error}") from error


def _identity_change(baseline: dict[str, Any] | None, upstream: dict[str, Any], carrier: dict[str, Any], manifest_sha: str) -> str | None:
    if baseline is None:
        return None
    current = {
        "releaseId": upstream["releaseId"],
        "sourceCommit": upstream["sourceCommit"],
        "carrierSha256": carrier["sha256"],
        "manifestSha256": manifest_sha,
    }
    observed = next((item for item in baseline["observed"] if item["tag"] == upstream["tag"]), None)
    if observed is None:
        return f"release identity is not present in the checked-in baseline for {upstream['tag']}"
    expected = {key: observed[key] for key in current}
    changed = [key for key in current if current[key] != expected[key]]
    if not changed:
        return None
    return f"release identity changed for {upstream['tag']}: {', '.join(changed)} differs from the checked-in baseline"


def scan_github(contract: dict[str, Any], *, token: str, release_tag: str | None, evidence: list[dict[str, Any]], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    releases = _github_json(f"https://api.github.com/repos/{UPSTREAM_REPOSITORY}/releases?per_page=100", token)
    if not isinstance(releases, list):
        raise AuditError("GitHub releases response is invalid")
    stable = [item for item in releases if isinstance(item, dict) and not item.get("draft") and not item.get("prerelease") and SAFE_TAG.fullmatch(str(item.get("tag_name", "")))]
    if release_tag:
        stable = [item for item in stable if item.get("tag_name") == release_tag]
    if not stable:
        raise AuditError("no stable Marauder release matched the requested tag")
    release = sorted(stable, key=lambda item: str(item.get("published_at", "")), reverse=True)[0]
    release_id = _nonnegative_int(release.get("id"), "release.id")
    tag = _string(release.get("tag_name"), "release.tag")
    if SAFE_TAG.fullmatch(tag) is None:
        raise AuditError("release tag is not a stable semver tag")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise AuditError("release assets are missing")
    asset = next((item for item in assets if isinstance(item, dict) and item.get("name") == ZIP_NAME), None)
    if asset is None:
        asset = next((item for item in assets if isinstance(item, dict) and item.get("name") == MANIFEST_NAME), None)
    if asset is None:
        raise AuditError("release has no supported manifest carrier")
    asset_url = _string(asset.get("browser_download_url"), "asset.browser_download_url")
    with tempfile.TemporaryDirectory(prefix="esp-installer-audit-") as directory:
        path = Path(directory) / _string(asset.get("name"), "asset.name")
        _download(asset_url, token, path)
        carrier, manifest_bytes, members = _carrier(path)
    upstream = {"repository": UPSTREAM_REPOSITORY, "releaseId": release_id, "tag": tag, "sourceCommit": ""}
    manifest = _object(json.loads(manifest_bytes.decode("utf-8")), "firmware manifest")
    source_commit = _commit(manifest.get("sourceCommit"), "manifest.sourceCommit")
    upstream["sourceCommit"] = source_commit
    expected_size = asset.get("size")
    if isinstance(expected_size, int) and expected_size != carrier["bytes"]:
        raise AuditError("downloaded carrier size differs from GitHub release metadata")
    expected_digest = asset.get("digest")
    if isinstance(expected_digest, str) and expected_digest.startswith("sha256:") and expected_digest[7:] != carrier["sha256"]:
        raise AuditError("downloaded carrier digest differs from GitHub release metadata")
    return build_report(
        contract,
        upstream=upstream,
        carrier=carrier,
        manifest_bytes=manifest_bytes,
        members=members,
        evidence=evidence,
        identity_change=_identity_change(baseline, upstream, carrier, _hash_bytes(manifest_bytes)),
    )


def _write(report: dict[str, Any], output: Path, markdown: Path) -> None:
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown.write_text(render_markdown(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("scan-github", "scan"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--contract", type=Path, required=True)
        sub.add_argument("--output", type=Path, required=True)
        sub.add_argument("--markdown", type=Path, required=True)
        sub.add_argument("--release-tag")
        sub.add_argument("--carrier", type=Path)
        sub.add_argument("--source-commit")
        sub.add_argument("--release-id", type=int)
        sub.add_argument("--baseline", type=Path)
    args = parser.parse_args(argv)
    try:
        contract = validate_contract(_json(args.contract))
        baseline = validate_baseline(_json(args.baseline)) if args.baseline else None
        evidence: list[dict[str, Any]] = []
        if args.command == "scan-github":
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            if not token:
                raise AuditError("GH_TOKEN is required for scan-github")
            report = scan_github(contract, token=token, release_tag=args.release_tag, evidence=evidence, baseline=baseline)
        else:
            if args.carrier is None or args.release_id is None or not args.release_tag or not args.source_commit:
                raise AuditError("scan requires --carrier, --release-id, --release-tag and --source-commit")
            carrier, manifest_bytes, members = _carrier(args.carrier)
            upstream = {"repository": UPSTREAM_REPOSITORY, "releaseId": args.release_id, "tag": args.release_tag, "sourceCommit": _commit(args.source_commit, "sourceCommit")}
            report = build_report(
                contract,
                upstream=upstream,
                carrier=carrier,
                manifest_bytes=manifest_bytes,
                members=members,
                evidence=evidence,
                identity_change=_identity_change(baseline, upstream, carrier, _hash_bytes(manifest_bytes)),
            )
        _write(report, args.output, args.markdown)
        return 0 if report["status"] == "verified" else 1
    except AuditError as error:
        report = {"schema": 1, "kind": "espInstallerAuditReport", "status": "rejected", "automaticFlashPackageAuthorization": False, "error": str(error)}
        _write(report, args.output, args.markdown)
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
