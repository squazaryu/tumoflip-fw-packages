#!/usr/bin/env python3
"""Validate repository contracts, a catalog directory, or a legacy seed."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from .catalog_contract import (
        ContractError,
        PACKAGE_TAG,
        load_json,
        verify_migration_provenance,
        verify_release_directory,
    )
except ImportError:  # Direct script execution.
    from catalog_contract import (
        ContractError,
        PACKAGE_TAG,
        load_json,
        verify_migration_provenance,
        verify_release_directory,
    )


def verify_contract(root: Path) -> None:
    try:
        from .mirror_history import load_contract as load_history_contract
    except ImportError:
        from mirror_history import load_contract as load_history_contract

    load_history_contract(root)
    legacy = load_json(root / "contracts/legacy-sources.json")
    lineage = load_json(root / "contracts/catalog-lineage.json")
    baselines = load_json(root / "contracts/catalog-baselines.json")
    checkouts = load_json(root / "contracts/source-checkouts.json")
    clients = load_json(root / "contracts/client-sources.json")
    policy = load_json(root / "contracts/native-build-policy.json")
    for name, document in (
        ("legacy", legacy),
        ("lineage", lineage),
        ("baselines", baselines),
        ("checkouts", checkouts),
        ("clients", clients),
        ("native policy", policy),
    ):
        if document.get("schema") != 1:
            raise ContractError(f"{name} contract schema must be 1")
    if legacy.get("repository") != baselines.get("firmwareRepository"):
        raise ContractError("legacy and baseline firmware repositories differ")
    if checkouts.get("firmwareRepository") != baselines.get("firmwareRepository"):
        raise ContractError("checkout and baseline firmware repositories differ")
    if lineage.get("publisherRepository") != checkouts.get("publisherRepository"):
        raise ContractError("publisher repositories differ")
    if checkouts.get("refPolicy") != "full-commit-sha-only":
        raise ContractError("source checkout policy must require exact commits")
    package_sources = clients.get("packages")
    audit_sources = clients.get("audit")
    if not isinstance(package_sources, dict) or not isinstance(audit_sources, dict):
        raise ContractError("client source contract is incomplete")
    if package_sources.get("primaryRepository") != lineage.get("publisherRepository"):
        raise ContractError("client primary repository differs from publisher")
    if package_sources.get("legacyRepository") != legacy.get("repository"):
        raise ContractError("client legacy repository differs from pinned fallback")
    if package_sources.get("identityFields") != [
        "catalog_channel",
        "catalog_revision",
        "release_id",
    ]:
        raise ContractError("client package identity fields differ")
    for label, source in (("packages", package_sources), ("audit", audit_sources)):
        fallback = set(source.get("fallbackOn", []))
        terminal = set(source.get("terminalFailures", []))
        if not fallback or not terminal or fallback & terminal:
            raise ContractError(f"client {label} fallback is not fail-closed")
        if "malformedPrimary" not in terminal or "digestMismatch" not in terminal:
            raise ContractError(f"client {label} must reject malformed primary data")
    commit_pattern = re.compile(checkouts.get("commitPattern", ""))
    for channel in ("stable", "dev"):
        legacy_channel = legacy["channels"][channel]
        lineage_channel = lineage["channels"][channel]
        baseline = baselines["channels"][channel]
        tag_match = PACKAGE_TAG.fullmatch(legacy_channel["tag"])
        if tag_match is None or tag_match.group(1) != channel:
            raise ContractError(f"invalid legacy {channel} tag")
        if int(tag_match.group(2)) != legacy_channel["revision"]:
            raise ContractError(f"legacy {channel} revision differs")
        if legacy_channel.get("prerelease") is not (channel == "dev"):
            raise ContractError(f"legacy {channel} prerelease state differs")
        if lineage_channel["currentTag"] != legacy_channel["tag"]:
            raise ContractError(f"lineage {channel} head differs from seed")
        if lineage_channel["currentRevision"] != legacy_channel["revision"]:
            raise ContractError(f"lineage {channel} revision differs from seed")
        if lineage_channel["nextNativeRevision"] != legacy_channel["revision"] + 1:
            raise ContractError(f"lineage {channel} next revision is not monotonic")
        next_match = PACKAGE_TAG.fullmatch(lineage_channel["nextNativeTag"])
        if next_match is None or int(next_match.group(2)) != lineage_channel["nextNativeRevision"]:
            raise ContractError(f"lineage {channel} next tag differs")
        for label, commit in (
            ("legacy source", legacy_channel["sourceCommit"]),
            ("legacy tag", legacy_channel["tagCommit"]),
            ("legacy target", legacy_channel["targetFirmwareCommit"]),
            ("baseline", baseline["firmwareCommit"]),
        ):
            if commit_pattern.fullmatch(commit) is None:
                raise ContractError(f"{channel} {label} commit is not an exact SHA")
        baseline_advanced = (
            legacy_channel["targetFirmwareTag"] != baseline["firmwareTag"]
            or legacy_channel["targetFirmwareCommit"] != baseline["firmwareCommit"]
        )
        if baseline_advanced:
            plan = policy.get("releasePlans", {}).get(lineage_channel["nextNativeTag"])
            if (
                channel != "stable"
                or not isinstance(plan, dict)
                or plan.get("mode") != "firmwareSnapshot"
                or plan.get("sourceCommit") != baseline["firmwareCommit"]
                or plan.get("selectedOverlays") != []
                or not isinstance(baseline.get("packageManifestSHA256"), str)
                or not isinstance(baseline.get("packageZipSHA256"), str)
            ):
                raise ContractError(f"{channel} target firmware differs without snapshot plan")


def verify_seed(root: Path, contract_root: Path) -> None:
    legacy = load_json(contract_root / "contracts/legacy-sources.json")
    for channel in ("stable", "dev"):
        verify_release_directory(root / channel, legacy["channels"][channel])
    index = load_json(root / "seed-index.json")
    if index.get("schema") != 1 or index.get("sourceRepository") != legacy.get("repository"):
        raise ContractError("seed index source differs")


def verify_migration(
    root: Path,
    contract_root: Path,
    publisher_repository: str,
    publisher_commit: str,
) -> None:
    legacy = load_json(contract_root / "contracts/legacy-sources.json")
    verify_migration_provenance(root, legacy, publisher_repository, publisher_commit)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    sub = value.add_subparsers(dest="command", required=True)
    contract = sub.add_parser("contract")
    contract.add_argument("--root", type=Path, default=Path("."))
    release = sub.add_parser("release")
    release.add_argument("directory", type=Path)
    seed = sub.add_parser("seed")
    seed.add_argument("--root", type=Path, required=True)
    seed.add_argument("--contract-root", type=Path, default=Path("."))
    migration = sub.add_parser("migration")
    migration.add_argument("--root", type=Path, required=True)
    migration.add_argument("--contract-root", type=Path, default=Path("."))
    migration.add_argument("--publisher-repository", required=True)
    migration.add_argument("--publisher-commit", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "contract":
            verify_contract(args.root)
        elif args.command == "release":
            verify_release_directory(args.directory)
        elif args.command == "seed":
            verify_seed(args.root, args.contract_root)
        else:
            verify_migration(
                args.root,
                args.contract_root,
                args.publisher_repository,
                args.publisher_commit,
            )
    except (ContractError, KeyError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"verified: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
