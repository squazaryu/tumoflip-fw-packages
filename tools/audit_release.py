#!/usr/bin/env python3
"""Build and verify immutable protected-app audit release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from tools.tumoflip import protected_app_audit as audit_tool
except ModuleNotFoundError:  # Direct script execution.
    from tumoflip import protected_app_audit as audit_tool


AUDIT_TAG = re.compile(r"^audit-ledger-[0-9]{8}-[0-9]{3}$")
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
PACKAGE_TAG = re.compile(r"^fw-packages-(stable|dev)-[0-9]{3}$")
FIRMWARE_TAG = re.compile(r"^(?:v[0-9]+\.[0-9]+\.[0-9]+|t-dev-[0-9]{3}-[0-9]{3})$")
LEDGER_ASSET = "protected-app-audit-ledger.json"
PROVENANCE_ASSET = "audit-provenance.json"
CHECKSUM_ASSET = "protected-app-audit-ledger-SHA256SUMS"
ASSET_NAMES = (LEDGER_ASSET, PROVENANCE_ASSET, CHECKSUM_ASSET)


class AuditReleaseError(RuntimeError):
    """Raised when release evidence is incomplete, ambiguous, or changed."""


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AuditReleaseError(f"invalid JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise AuditReleaseError(f"JSON root must be an object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuditReleaseError(f"{label} must be a non-empty string")
    return value


def require_digest(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise AuditReleaseError(f"{label} is invalid")
    return value


def validate_evidence(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise AuditReleaseError("audit evidence must be an object")
    if document.get("schema") != 1 or document.get("kind") != "protectedAppAuditEvidence":
        raise AuditReleaseError("audit evidence contract is invalid")
    control = document.get("control")
    implementation = document.get("implementation")
    community = document.get("community")
    issue = document.get("issue")
    if not all(isinstance(value, dict) for value in (control, implementation, community, issue)):
        raise AuditReleaseError("audit evidence identity is incomplete")
    require_string(control.get("repository"), "control repository")
    require_digest(control.get("commit"), HEX_40, "control commit")
    if implementation.get("repository") != "squazaryu/tumoflip":
        raise AuditReleaseError("implementation repository is invalid")
    require_digest(implementation.get("commit"), HEX_40, "implementation commit")
    implementations = document.get("implementations")
    if implementations is not None:
        if not isinstance(implementations, dict) or set(implementations) != {"stable", "dev"}:
            raise AuditReleaseError("channel implementation pins are incomplete")
        for channel in ("stable", "dev"):
            pin = implementations[channel]
            if not isinstance(pin, dict) or pin.get("repository") != "squazaryu/tumoflip":
                raise AuditReleaseError(f"{channel} implementation repository is invalid")
            require_digest(pin.get("commit"), HEX_40, f"{channel} implementation commit")
        if implementation != implementations["dev"]:
            raise AuditReleaseError("legacy implementation alias differs from dev pin")
    if community.get("repository") != audit_tool.SOURCE_REPOSITORY:
        raise AuditReleaseError("Community Pack repository is invalid")
    require_string(community.get("tag"), "Community Pack tag")
    require_digest(community.get("commit"), HEX_40, "Community Pack commit")
    archives = community.get("archives")
    if not isinstance(archives, dict) or set(archives) != {"base", "extra"}:
        raise AuditReleaseError("Community Pack archives are incomplete")
    for pack, item in archives.items():
        if not isinstance(item, dict):
            raise AuditReleaseError(f"Community Pack {pack} evidence is invalid")
        require_string(item.get("fileName"), f"Community Pack {pack} filename")
        require_digest(item.get("sha256"), HEX_64, f"Community Pack {pack} SHA-256")
    issue_number = issue.get("number")
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number < 1:
        raise AuditReleaseError("canonical issue number is invalid")
    require_string(issue.get("url"), "canonical issue URL")

    packages = document.get("packages")
    firmware = document.get("firmware")
    if not isinstance(packages, list) or not packages:
        raise AuditReleaseError("package target evidence is required")
    if not isinstance(firmware, list) or not firmware:
        raise AuditReleaseError("firmware target evidence is required")
    package_keys: set[tuple[str, str]] = set()
    for item in packages:
        if not isinstance(item, dict):
            raise AuditReleaseError("package evidence entry is invalid")
        repository = require_string(item.get("repository"), "package repository")
        tag = require_string(item.get("releaseTag"), "package release tag")
        if PACKAGE_TAG.fullmatch(tag) is None or (repository, tag) in package_keys:
            raise AuditReleaseError("package release identity is invalid or duplicated")
        package_keys.add((repository, tag))
        require_digest(item.get("tagCommit"), HEX_40, "package tag commit")
        require_digest(item.get("sourceCommit"), HEX_40, "package source commit")
        require_digest(item.get("manifestSHA256"), HEX_64, "package manifest SHA-256")
        require_digest(item.get("archiveSHA256"), HEX_64, "package archive SHA-256")
        require_digest(item.get("manifestReleaseId"), HEX_64, "package manifest release ID")
        package_pin = item.get("implementation")
        if package_pin is not None:
            channel = tag.split("-")[2]
            if not isinstance(package_pin, dict) or package_pin != (
                implementations or {}
            ).get(channel):
                raise AuditReleaseError(f"package {channel} implementation pin differs")
        package_release = item.get("packageRelease")
        if not isinstance(package_release, dict):
            raise AuditReleaseError("package release provenance is required")
        if (
            package_release.get("catalog_release_tag") != tag
            or package_release.get("source_commit") != item["sourceCommit"]
        ):
            raise AuditReleaseError("package release provenance differs")
        release_id = item.get("githubReleaseId")
        if not isinstance(release_id, int) or isinstance(release_id, bool) or release_id < 1:
            raise AuditReleaseError("package GitHub release id is invalid")
    firmware_keys: set[tuple[str, str]] = set()
    for item in firmware:
        if not isinstance(item, dict):
            raise AuditReleaseError("firmware evidence entry is invalid")
        repository = require_string(item.get("repository"), "firmware repository")
        tag = require_string(item.get("releaseTag"), "firmware release tag")
        if FIRMWARE_TAG.fullmatch(tag) is None or (repository, tag) in firmware_keys:
            raise AuditReleaseError("firmware release identity is invalid or duplicated")
        firmware_keys.add((repository, tag))
        require_digest(item.get("tagCommit"), HEX_40, "firmware tag commit")
        require_digest(item.get("updaterSHA256"), HEX_64, "firmware updater SHA-256")
        require_digest(
            item.get("resourceManifestSHA256"), HEX_64, "firmware resource manifest SHA-256"
        )
        require_digest(item.get("resourcesSHA256"), HEX_64, "firmware resources SHA-256")
        release_id = item.get("githubReleaseId")
        if not isinstance(release_id, int) or isinstance(release_id, bool) or release_id < 1:
            raise AuditReleaseError("firmware GitHub release id is invalid")
    return document


def _audit_identity(value: dict[str, Any]) -> tuple[str, str, str]:
    archives = {item["pack"]: item["sha256"] for item in value["archives"]}
    return value["sourceTag"], archives["base"], archives["extra"]


def validate_predecessor(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema") != 1:
        raise AuditReleaseError("audit predecessor contract is invalid")
    kind = document.get("kind")
    if kind == "bootstrap":
        if set(document) != {"schema", "kind", "indexSHA256", "ledgerSHA256"}:
            raise AuditReleaseError("bootstrap predecessor fields differ")
        require_digest(document.get("indexSHA256"), HEX_64, "bootstrap index SHA-256")
        require_digest(document.get("ledgerSHA256"), HEX_64, "bootstrap ledger SHA-256")
    elif kind == "auditRelease":
        if set(document) != {
            "schema",
            "kind",
            "tag",
            "githubReleaseId",
            "tagCommit",
            "ledgerSHA256",
            "provenanceSHA256",
        }:
            raise AuditReleaseError("release predecessor fields differ")
        tag = require_string(document.get("tag"), "predecessor release tag")
        if AUDIT_TAG.fullmatch(tag) is None:
            raise AuditReleaseError("predecessor release tag is invalid")
        release_id = document.get("githubReleaseId")
        if not isinstance(release_id, int) or isinstance(release_id, bool) or release_id < 1:
            raise AuditReleaseError("predecessor release ID is invalid")
        require_digest(document.get("tagCommit"), HEX_40, "predecessor tag commit")
        require_digest(document.get("ledgerSHA256"), HEX_64, "predecessor ledger SHA-256")
        require_digest(
            document.get("provenanceSHA256"), HEX_64, "predecessor provenance SHA-256"
        )
    else:
        raise AuditReleaseError("audit predecessor kind is invalid")
    return document


def _audit_bound_to_evidence(
    ledger: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    community = evidence["community"]
    issue = evidence["issue"]
    expected_identity = (
        community["tag"],
        community["archives"]["base"]["sha256"],
        community["archives"]["extra"]["sha256"],
    )
    matches = [
        audit
        for audit in ledger["audits"]
        if _audit_identity(audit) == expected_identity
        and audit["sourceCommit"] == community["commit"]
        and audit["auditIssue"] == issue["url"]
    ]
    if len(matches) != 1:
        raise AuditReleaseError(
            "cumulative ledger does not contain exactly one audit bound to release evidence"
        )
    return matches[0]


def _assert_evidence_covers_audit(
    ledger: dict[str, Any], audit: dict[str, Any], evidence: dict[str, Any]
) -> None:
    community = evidence["community"]
    if audit["sourceTag"] != community["tag"] or audit["sourceCommit"] != community["commit"]:
        raise AuditReleaseError("audit and Community Pack identity differ")
    archive_map = {item["pack"]: item["sha256"] for item in audit["archives"]}
    if any(archive_map[pack] != community["archives"][pack]["sha256"] for pack in ("base", "extra")):
        raise AuditReleaseError("audit and Community Pack archive evidence differ")
    if audit["auditIssue"] != evidence["issue"]["url"]:
        raise AuditReleaseError("audit and canonical issue differ")
    matches = [item for item in ledger["audits"] if _audit_identity(item) == _audit_identity(audit)]
    if len(matches) != 1 or audit_tool.semantic_audit_payload(matches[0]) != audit_tool.semantic_audit_payload(audit):
        raise AuditReleaseError("cumulative ledger does not contain the exact audit payload")

    packages = {(item["releaseTag"], item["manifestSHA256"]): item for item in evidence["packages"]}
    package_catalogs = {item["releaseTag"]: item for item in evidence["packages"]}
    firmware = {(item["releaseTag"], item["resourceManifestSHA256"]): item for item in evidence["firmware"]}
    for entry in audit["entries"]:
        for provenance in entry.get("targetProvenance", []):
            kind = provenance["containerKind"]
            key = (provenance["releaseTag"], provenance["manifestSHA256"])
            if kind == "fwPackagesZip":
                item = packages.get(key)
                if item is None:
                    raise AuditReleaseError(f"missing exact package evidence for {key[0]}")
                expected_container = item["archiveSHA256"]
                if provenance.get("targetSourceCommit") != item["sourceCommit"]:
                    raise AuditReleaseError(f"package source commit differs for {key[0]}")
            elif kind == "fwPackagesCompatibleBuild":
                catalog_tag = provenance.get("compatibilityCatalogTag")
                catalog = package_catalogs.get(catalog_tag)
                if catalog is None:
                    raise AuditReleaseError(f"missing compatibility catalog evidence for {key[0]}")
                expected_container = catalog["manifestSHA256"]
                package_release = catalog["packageRelease"]
                sources: list[dict[str, Any]] = []
                if isinstance(package_release.get("base_catalog"), dict):
                    sources.append(package_release["base_catalog"])
                compatible = package_release.get("compatible_releases", [])
                if isinstance(compatible, list):
                    sources.extend(item for item in compatible if isinstance(item, dict))
                source_matches = [
                    source
                    for source in sources
                    if source.get("release_tag") == provenance["releaseTag"]
                    and source.get("manifest_sha256") == provenance["manifestSHA256"]
                    and source.get("source_commit") == provenance.get("targetSourceCommit")
                ]
                if len(source_matches) != 1:
                    raise AuditReleaseError(f"compatible source evidence differs for {key[0]}")
            elif kind == "firmwareUpdaterBundle":
                item = firmware.get(key)
                if item is None:
                    raise AuditReleaseError(f"missing exact firmware evidence for {key[0]}")
                expected_container = item["updaterSHA256"]
                if (
                    provenance.get("targetSourceCommit") != item["tagCommit"]
                    or provenance.get("resourcesSHA256") != item["resourcesSHA256"]
                ):
                    raise AuditReleaseError(f"firmware source evidence differs for {key[0]}")
            else:
                raise AuditReleaseError(f"unknown target container kind: {kind}")
            if provenance["containerSHA256"] != expected_container:
                raise AuditReleaseError(f"target container evidence differs for {key[0]}")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def prepare_release(
    *,
    ledger_path: Path,
    audit_path: Path,
    evidence_path: Path,
    predecessor_path: Path,
    output: Path,
    tag: str,
    publisher_repository: str,
    publisher_commit: str,
) -> None:
    if AUDIT_TAG.fullmatch(tag) is None:
        raise AuditReleaseError("audit release tag is invalid")
    require_string(publisher_repository, "publisher repository")
    require_digest(publisher_commit, HEX_40, "publisher commit")
    ledger = load_object(ledger_path)
    audit = load_object(audit_path)
    evidence = validate_evidence(load_object(evidence_path))
    predecessor = validate_predecessor(load_object(predecessor_path))
    try:
        audit_tool.validate_ledger(ledger)
        audit_tool.validate_audit(audit)
    except audit_tool.AuditError as error:
        raise AuditReleaseError(str(error)) from error
    if evidence["control"] != {
        "repository": publisher_repository,
        "commit": publisher_commit,
    }:
        raise AuditReleaseError("publisher identity differs from exact control evidence")
    _assert_evidence_covers_audit(ledger, audit, evidence)

    output.mkdir(parents=True, exist_ok=True)
    ledger_asset = output / LEDGER_ASSET
    shutil.copyfile(ledger_path, ledger_asset)
    ledger_sha = sha256(ledger_asset)
    provenance = {
        "schema": 1,
        "kind": "protectedAppAuditRelease",
        "auditReleaseTag": tag,
        "publisher": {
            "repository": publisher_repository,
            "commit": publisher_commit,
        },
        "auditSemanticSHA256": audit_tool.semantic_audit_sha256(audit),
        "ledgerSHA256": ledger_sha,
        "evidenceSHA256": canonical_sha256(evidence),
        "evidence": evidence,
        "predecessor": predecessor,
    }
    provenance_asset = output / PROVENANCE_ASSET
    _write_json(provenance_asset, provenance)
    checksum_asset = output / CHECKSUM_ASSET
    checksum_asset.write_text(
        f"{ledger_sha}  {LEDGER_ASSET}\n"
        f"{sha256(provenance_asset)}  {PROVENANCE_ASSET}\n",
        encoding="utf-8",
    )
    verify_release(
        root=output,
        tag=tag,
        publisher_repository=publisher_repository,
        publisher_commit=publisher_commit,
    )


def _parse_checksums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise AuditReleaseError(f"cannot read audit checksums: {error}") from error
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})\s+\*?([^\s]+)", line)
        if match is None or match.group(2) in result:
            raise AuditReleaseError("audit checksum file is invalid")
        result[match.group(2)] = match.group(1)
    if set(result) != {LEDGER_ASSET, PROVENANCE_ASSET}:
        raise AuditReleaseError("audit checksums must cover exactly ledger and provenance")
    return result


def verify_release(
    *, root: Path, tag: str, publisher_repository: str, publisher_commit: str
) -> dict[str, Any]:
    if AUDIT_TAG.fullmatch(tag) is None:
        raise AuditReleaseError("audit release tag is invalid")
    if {path.name for path in root.iterdir() if path.is_file()} != set(ASSET_NAMES):
        raise AuditReleaseError("audit release asset set differs")
    checksums = _parse_checksums(root / CHECKSUM_ASSET)
    for name, expected in checksums.items():
        if sha256(root / name) != expected:
            raise AuditReleaseError(f"audit release asset digest differs: {name}")
    ledger = load_object(root / LEDGER_ASSET)
    provenance = load_object(root / PROVENANCE_ASSET)
    try:
        audit_tool.validate_ledger(ledger)
    except audit_tool.AuditError as error:
        raise AuditReleaseError(str(error)) from error
    if provenance.get("schema") != 1 or provenance.get("kind") != "protectedAppAuditRelease":
        raise AuditReleaseError("audit provenance contract is invalid")
    if provenance.get("auditReleaseTag") != tag:
        raise AuditReleaseError("audit provenance tag differs")
    if provenance.get("publisher") != {
        "repository": publisher_repository,
        "commit": publisher_commit,
    }:
        raise AuditReleaseError("audit provenance publisher differs")
    if provenance.get("ledgerSHA256") != sha256(root / LEDGER_ASSET):
        raise AuditReleaseError("audit provenance ledger digest differs")
    evidence = validate_evidence(provenance.get("evidence"))
    if provenance.get("evidenceSHA256") != canonical_sha256(evidence):
        raise AuditReleaseError("audit provenance evidence digest differs")
    audit_semantic_sha256 = require_digest(
        provenance.get("auditSemanticSHA256"),
        HEX_64,
        "audit provenance semantic SHA-256",
    )
    audit = _audit_bound_to_evidence(ledger, evidence)
    if audit_semantic_sha256 != audit_tool.semantic_audit_sha256(audit):
        raise AuditReleaseError("audit provenance semantic SHA-256 differs")
    _assert_evidence_covers_audit(ledger, audit, evidence)
    predecessor = validate_predecessor(provenance.get("predecessor"))
    return {
        "schema": 1,
        "kind": "verifiedProtectedAppAuditRelease",
        "auditReleaseTag": tag,
        "publisher": provenance["publisher"],
        "predecessor": predecessor,
        "audit": audit,
        "ledgerSHA256": sha256(root / LEDGER_ASSET),
        "provenanceSHA256": sha256(root / PROVENANCE_ASSET),
    }


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--ledger", type=Path, required=True)
    prepare.add_argument("--audit", type=Path, required=True)
    prepare.add_argument("--evidence", type=Path, required=True)
    prepare.add_argument("--predecessor", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--tag", required=True)
    prepare.add_argument("--publisher-repository", required=True)
    prepare.add_argument("--publisher-commit", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--tag", required=True)
    verify.add_argument("--publisher-repository", required=True)
    verify.add_argument("--publisher-commit", required=True)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "prepare":
            prepare_release(
                ledger_path=args.ledger,
                audit_path=args.audit,
                evidence_path=args.evidence,
                predecessor_path=args.predecessor,
                output=args.output,
                tag=args.tag,
                publisher_repository=args.publisher_repository,
                publisher_commit=args.publisher_commit,
            )
        else:
            verified = verify_release(
                root=args.root,
                tag=args.tag,
                publisher_repository=args.publisher_repository,
                publisher_commit=args.publisher_commit,
            )
            print(json.dumps(verified, separators=(",", ":"), ensure_ascii=False))
    except (AuditReleaseError, OSError) as error:
        print(f"audit release validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
