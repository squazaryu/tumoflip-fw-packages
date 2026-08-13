#!/usr/bin/env python3
"""Build and independently verify a native Tumoflip FW Packages release.

The firmware repository remains the owner of application source, fbt, and its
export mapping. This control-plane tool composes only a checked-in selection of
those exports over an immutable predecessor catalog, then emits independently
verified provenance. It never reads, downloads, or updates firmware binaries.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from .catalog_contract import (
        ContractError,
        load_json,
        manifest_release_id,
        sha256,
        verify_archive,
        verify_release_directory,
    )
except ImportError:  # Direct script execution.
    from catalog_contract import (
        ContractError,
        load_json,
        manifest_release_id,
        sha256,
        verify_archive,
        verify_release_directory,
    )


Runner = Callable[..., subprocess.CompletedProcess[str]]
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CANONICAL_ASSETS = (
    "tumoflip-packages.json",
    "tumoflip-packages.zip",
)
PROVENANCE_NAME = "catalog-provenance.json"
PACKAGE_GROUPS = {"base", "arf", "module_one", "protocol_packs"}


def default_runner(
    command: Sequence[str], *, cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _exact_commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX_40.fullmatch(value) is None:
        raise ContractError(f"{label} must be a full lowercase commit SHA")
    return value


def _repository(value: Any, label: str) -> str:
    if not isinstance(value, str) or REPOSITORY.fullmatch(value) is None:
        raise ContractError(f"{label} is invalid")
    return value


def _run(runner: Runner, command: Sequence[str], *, cwd: Path) -> str:
    result = runner(command, cwd=cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise ContractError(f"command failed ({' '.join(command)}): {detail}")
    return result.stdout.strip()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def load_native_plan(
    control_root: Path,
    channel: str,
    revision: int,
    source_commit: str,
    publisher_commit: str,
) -> dict[str, Any]:
    """Resolve the next immutable channel revision from repository contracts."""

    source_commit = _exact_commit(source_commit, "source commit")
    publisher_commit = _exact_commit(publisher_commit, "publisher commit")
    if channel not in {"stable", "dev"}:
        raise ContractError("channel must be stable or dev")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ContractError("revision must be a positive integer")

    source_contract = load_json(control_root / "contracts/source-checkouts.json")
    lineage = load_json(control_root / "contracts/catalog-lineage.json")
    baselines = load_json(control_root / "contracts/catalog-baselines.json")
    legacy = load_json(control_root / "contracts/legacy-sources.json")
    policy = load_json(control_root / "contracts/native-build-policy.json")
    source_repository = _repository(
        source_contract.get("firmwareRepository"), "firmware repository"
    )
    publisher_repository = _repository(
        source_contract.get("publisherRepository"), "publisher repository"
    )
    if source_contract.get("buildParallelism") != 2:
        raise ContractError("build parallelism contract must remain exactly 2")
    allowed_overlays = policy.get("allowedOverlays")
    release_plans = policy.get("releasePlans")
    if (
        policy.get("schema") != 1
        or not isinstance(allowed_overlays, dict)
        or not allowed_overlays
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(target, str)
            or not target
            for name, target in allowed_overlays.items()
        )
        or len(set(allowed_overlays.values())) != len(allowed_overlays)
        or not isinstance(release_plans, dict)
        or set(release_plans) - {
            str(value.get("nextNativeTag"))
            for value in lineage.get("channels", {}).values()
            if isinstance(value, dict)
        }
    ):
        raise ContractError("native overlay policy is invalid")

    try:
        channel_lineage = lineage["channels"][channel]
        baseline = baselines["channels"][channel]
        base_release = legacy["channels"][channel]
    except (KeyError, TypeError) as error:
        raise ContractError(f"missing {channel} release contract") from error
    expected_revision = channel_lineage.get("nextNativeRevision")
    expected_tag = channel_lineage.get("nextNativeTag")
    tag = f"fw-packages-{channel}-{revision:03d}"
    if revision != expected_revision or tag != expected_tag:
        raise ContractError(
            f"requested {tag} is not the next contracted release {expected_tag}"
        )
    release_policy = release_plans.get(tag)
    selected_names = (
        release_policy.get("selectedOverlays")
        if isinstance(release_policy, dict)
        else None
    )
    if (
        not isinstance(selected_names, list)
        or not selected_names
        or any(
            not isinstance(name, str) or name not in allowed_overlays
            for name in selected_names
        )
        or len(set(selected_names)) != len(selected_names)
    ):
        raise ContractError(f"native release {tag} has no exact non-empty overlay plan")
    authorized_source_commit = _exact_commit(
        release_policy.get("sourceCommit"), f"native release {tag} source commit"
    )
    if source_commit != authorized_source_commit:
        raise ContractError(
            f"source commit is not authorized for native release {tag}"
        )
    selected_overlays = {
        name: allowed_overlays[name] for name in sorted(selected_names)
    }
    if (
        base_release.get("tag") != channel_lineage.get("currentTag")
        or base_release.get("revision") != channel_lineage.get("currentRevision")
    ):
        raise ContractError("native base release differs from current channel lineage")

    firmware_commit = _exact_commit(
        baseline.get("firmwareCommit"), "target firmware commit"
    )
    firmware_release_id = baseline.get("firmwareReleaseId")
    if (
        not isinstance(firmware_release_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", firmware_release_id) is None
    ):
        raise ContractError("target firmware release ID contract is invalid")
    firmware_tag = baseline.get("firmwareTag")
    firmware_version = baseline.get("firmwareVersion")
    api = baseline.get("api")
    target = baseline.get("target")
    if not all(isinstance(value, str) and value for value in (firmware_tag, firmware_version)):
        raise ContractError("target firmware tag/version contract is invalid")
    if not isinstance(api, str) or re.fullmatch(r"[0-9]+\.[0-9]+", api) is None:
        raise ContractError("target firmware API contract is invalid")
    if not isinstance(target, int) or isinstance(target, bool) or target < 1:
        raise ContractError("target firmware hardware target is invalid")

    return {
        "channel": channel,
        "revision": revision,
        "tag": tag,
        "prerelease": channel == "dev",
        "sourceRepository": source_repository,
        "sourceCommit": source_commit,
        "publisherRepository": publisher_repository,
        "publisherCommit": publisher_commit,
        "baseRelease": base_release,
        "selectedOverlays": selected_overlays,
        "overlayTargets": sorted(selected_overlays.values()),
        "maxChangedTargets": len(selected_overlays),
        "targetFirmware": {
            "repository": source_repository,
            "tag": firmware_tag,
            "commit": firmware_commit,
            "releaseId": firmware_release_id,
            "version": firmware_version,
            "api": api,
            "target": target,
        },
    }


def prove_source_checkout(
    source_root: Path, expected_commit: str, runner: Runner = default_runner
) -> None:
    actual = _run(runner, ("git", "rev-parse", "HEAD"), cwd=source_root)
    if actual != expected_commit:
        raise ContractError(f"source checkout differs: {actual} != {expected_commit}")
    tracked = _run(
        runner,
        ("git", "status", "--porcelain", "--untracked-files=no"),
        cwd=source_root,
    )
    if tracked:
        raise ContractError("source checkout has tracked modifications")
    command = ("git", "submodule", "status", "--recursive")
    result = runner(command, cwd=source_root)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise ContractError(f"command failed ({' '.join(command)}): {detail}")
    # The leading status byte is part of git's machine-readable contract:
    # space = exact, '-' = missing, '+' = different commit, 'U' = conflict.
    # Do not pass this output through _run(), which strips that first space.
    submodules = result.stdout
    invalid = [
        line
        for line in submodules.splitlines()
        if not line or line[0] != " "
    ]
    if invalid:
        raise ContractError("source checkout has missing or changed submodule gitlinks")


def _validate_source_manifest(manifest: dict[str, Any], plan: dict[str, Any]) -> None:
    if manifest.get("schema") != 2:
        raise ContractError("source manifest schema must be 2")
    release_id = manifest.get("release_id")
    if release_id != manifest_release_id(manifest):
        raise ContractError("source manifest release_id differs")
    firmware = manifest.get("firmware")
    if not isinstance(firmware, dict):
        raise ContractError("source manifest firmware is invalid")
    expected_firmware = plan["targetFirmware"]
    expected = {
        "version": expected_firmware["version"],
        "api": expected_firmware["api"],
        "target": expected_firmware["target"],
    }
    for field, value in expected.items():
        if firmware.get(field) != value:
            raise ContractError(f"source manifest firmware.{field} differs")
    package_release = manifest.get("package_release")
    if not isinstance(package_release, dict):
        raise ContractError("source manifest package_release is missing")
    required = {
        "type": "package-only",
        "source_commit": plan["sourceCommit"],
        "source_dirty": False,
        "target_release_tag": expected_firmware["tag"],
        "firmware_flash_unchanged": True,
    }
    for field, value in required.items():
        if package_release.get(field) != value:
            raise ContractError(f"source manifest package_release.{field} differs")
    if package_release.get("target_release_id") != plan["targetFirmware"]["releaseId"]:
        raise ContractError("source manifest package_release.target_release_id differs")
    overlay_targets = package_release.get("overlay_targets")
    if not isinstance(overlay_targets, list) or sorted(overlay_targets) != plan["overlayTargets"]:
        raise ContractError("source manifest overlay target policy differs")
    synced = package_release.get("synced_extapps")
    if (
        not isinstance(synced, list)
        or len(synced) != len(plan["overlayTargets"])
        or any(not isinstance(entry, dict) for entry in synced)
        or any(not isinstance(entry.get("target"), str) for entry in synced)
        or sorted(entry.get("target") for entry in synced) != plan["overlayTargets"]
    ):
        raise ContractError("source manifest synced overlay set differs")
    if not isinstance(manifest.get("artifacts"), dict):
        raise ContractError("source manifest firmware artifact evidence is invalid")
    packages = manifest.get("packages")
    if not isinstance(packages, dict) or set(packages) != PACKAGE_GROUPS:
        raise ContractError("source manifest package groups differ from client contract")


def _checksums(directory: Path, tag: str) -> Path:
    path = directory / f"{tag}-SHA256SUMS"
    content = "".join(
        f"{sha256(directory / name)}  {name}\n" for name in CANONICAL_ASSETS
    )
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
    return path


def _canonicalize_archive(
    manifest: dict[str, Any], archive_path: Path
) -> None:
    """Normalize container metadata while preserving every verified payload byte."""

    verify_archive(manifest, archive_path)
    sources = sorted(
        entry["source"]
        for entries in manifest["packages"].values()
        for entry in entries
    )
    with zipfile.ZipFile(archive_path) as archive:
        payloads = {source: archive.read(source) for source in sources}
    temporary = archive_path.with_name(f".{archive_path.name}.tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for source in sources:
                info = zipfile.ZipInfo(source, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, payloads[source], compresslevel=9)
        os.replace(temporary, archive_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    verify_archive(manifest, archive_path)


def _asset_evidence(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": sha256(path)}


def _entries_by_source(manifest: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        entry["source"]: (group, entry)
        for group, entries in manifest["packages"].items()
        for entry in entries
    }


def _source_exports(source_root: Path) -> dict[str, str]:
    try:
        from .source_build_targets import source_overlay_exports
    except ImportError:
        from source_build_targets import source_overlay_exports
    return source_overlay_exports(source_root)


def _compose_selected_release(
    source_root: Path,
    build_directory: Path,
    base_directory: Path,
    output_directory: Path,
    plan: dict[str, Any],
) -> None:
    """Compose a minimal catalog delta from selected source-owned build exports."""

    base = load_json(base_directory / "tumoflip-packages.json")
    manifest = copy.deepcopy(base)
    manifest.pop("release_id", None)
    base_release = base.get("package_release")
    if not isinstance(base_release, dict):
        raise ContractError("immutable base package release is missing")
    exports = _source_exports(source_root)
    selected_paths = set(plan["overlayTargets"])
    if not selected_paths.issubset(exports):
        raise ContractError("selected overlays differ from source-owned exports")
    packages = manifest.get("packages")
    if not isinstance(packages, dict):
        raise ContractError("immutable base package topology is invalid")
    base_entries = _entries_by_source(base)
    synced: list[dict[str, Any]] = []
    for source in sorted(selected_paths):
        if source not in base_entries:
            raise ContractError(f"selected overlay is absent from immutable base: {source}")
        group, old_entry = base_entries[source]
        filename = exports[source]
        artifact = build_directory / ".extapps" / filename
        if not artifact.is_file():
            raise ContractError(f"selected source build artifact is missing: {filename}")
        data = artifact.read_bytes()
        if not data:
            raise ContractError(f"selected source build artifact is empty: {filename}")
        replacement = {
            "source": source,
            "target": old_entry["target"],
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": hashlib.md5(data).hexdigest(),
        }
        if all(
            replacement[key] == old_entry.get(key)
            for key in ("bytes", "sha256", "md5")
        ):
            raise ContractError(f"selected overlay is unchanged: {source}")
        matches = [
            index
            for index, entry in enumerate(packages[group])
            if isinstance(entry, dict) and entry.get("source") == source
        ]
        if len(matches) != 1:
            raise ContractError(f"selected overlay route is ambiguous: {source}")
        packages[group][matches[0]] = replacement
        synced.append(
            {
                "source": f".extapps/{filename}",
                "target": source,
                "bytes": replacement["bytes"],
                "sha256": replacement["sha256"],
                "md5": replacement["md5"],
            }
        )

    remaining_compatible_ids = {
        str(alias["release_id"])
        for entries in packages.values()
        for entry in entries
        for alias in entry.get("compatible_builds", [])
    }
    compatible_releases = [
        copy.deepcopy(item)
        for item in base_release.get("compatible_releases", [])
        if item.get("release_id") in remaining_compatible_ids
    ]
    if {str(item.get("release_id")) for item in compatible_releases} != remaining_compatible_ids:
        raise ContractError("selected overlay compatible lineage cannot be resolved")
    modified_targets = set(base_release.get("catalog_modified_targets", []))
    if not all(isinstance(item, str) for item in modified_targets):
        raise ContractError("immutable base modified-target evidence is invalid")
    manifest["package_release"] = {
        "type": "package-only",
        "id": plan["tag"],
        "source_commit": plan["sourceCommit"],
        "source_dirty": False,
        "source_firmware_version": plan["targetFirmware"]["version"],
        "target_release_tag": plan["targetFirmware"]["tag"],
        "target_release_id": base_release.get("target_release_id"),
        "target_source_commit": plan["targetFirmware"]["commit"],
        "firmware_flash_unchanged": True,
        "overlay_targets": sorted(selected_paths),
        "catalog_modified_targets": sorted(modified_targets | selected_paths),
        "synced_extapps": synced,
        "catalog_channel": plan["channel"],
        "catalog_revision": plan["revision"],
        "catalog_release_tag": plan["tag"],
        "base_catalog": {
            "release_tag": plan["baseRelease"]["tag"],
            "release_id": base["release_id"],
            "manifest_sha256": sha256(base_directory / "tumoflip-packages.json"),
            "package_zip_sha256": sha256(base_directory / "tumoflip-packages.zip"),
            "source_commit": plan["baseRelease"]["sourceCommit"],
        },
        "compatible_releases": compatible_releases,
    }
    if manifest["package_release"]["target_release_id"] != plan["targetFirmware"][
        "releaseId"
    ]:
        raise ContractError("immutable base target firmware release ID differs")
    manifest["release_id"] = manifest_release_id(manifest)
    _write_json(output_directory / "tumoflip-packages.json", manifest)
    with zipfile.ZipFile(base_directory / "tumoflip-packages.zip") as base_zip:
        payloads = {name: base_zip.read(name) for name in base_zip.namelist()}
    for source in selected_paths:
        payloads[source] = (build_directory / ".extapps" / exports[source]).read_bytes()
    archive_path = output_directory / "tumoflip-packages.zip"
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in sorted(payloads):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payloads[name], compresslevel=9)
    verify_archive(manifest, archive_path)


def verify_bounded_delta(
    base_directory: Path, directory: Path, plan: dict[str, Any]
) -> list[str]:
    """Prove the source builder changed only the control-owned overlay set."""

    verify_release_directory(base_directory, plan["baseRelease"])
    base = load_json(base_directory / "tumoflip-packages.json")
    candidate = load_json(directory / "tumoflip-packages.json")
    base_entries = _entries_by_source(base)
    candidate_entries = _entries_by_source(candidate)
    if set(base_entries) != set(candidate_entries):
        raise ContractError("native package topology differs from immutable base")
    allowed = set(plan["overlayTargets"])
    changed: set[str] = set()
    for source in sorted(base_entries):
        old_group, old_entry = base_entries[source]
        new_group, new_entry = candidate_entries[source]
        if old_group != new_group or old_entry.get("target") != new_entry.get("target"):
            raise ContractError(f"native package route differs: {source}")
        if old_entry != new_entry:
            changed.add(source)
    if changed - allowed or len(changed) > plan["maxChangedTargets"]:
        raise ContractError("native package delta exceeds control-owned overlay policy")
    if candidate.get("cleanup") != base.get("cleanup"):
        raise ContractError("native cleanup policy differs from immutable base")
    if candidate.get("artifacts") != base.get("artifacts"):
        raise ContractError("native firmware artifact evidence differs from immutable base")
    base_package_release = base.get("package_release")
    candidate_package_release = candidate.get("package_release")
    if (
        not isinstance(base_package_release, dict)
        or not isinstance(candidate_package_release, dict)
        or candidate_package_release.get("target_release_id")
        != base_package_release.get("target_release_id")
    ):
        raise ContractError("native target firmware release lineage differs")
    with zipfile.ZipFile(base_directory / "tumoflip-packages.zip") as old_zip:
        with zipfile.ZipFile(directory / "tumoflip-packages.zip") as new_zip:
            if set(old_zip.namelist()) != set(new_zip.namelist()):
                raise ContractError("native ZIP topology differs from immutable base")
            for source in sorted(set(base_entries) - allowed):
                if old_zip.read(source) != new_zip.read(source):
                    raise ContractError(f"native ZIP changed non-overlay payload: {source}")
    return sorted(changed)


def finalize_native_release(
    directory: Path, plan: dict[str, Any], base_directory: Path | None = None
) -> None:
    """Add independent identity without changing any packaged file bytes."""

    manifest_path = directory / "tumoflip-packages.json"
    archive_path = directory / "tumoflip-packages.zip"
    if not manifest_path.is_file() or not archive_path.is_file():
        raise ContractError("source builder did not produce both package assets")
    manifest = load_json(manifest_path)
    _validate_source_manifest(manifest, plan)
    package_release = dict(manifest["package_release"])
    package_release.update(
        {
            "catalog_channel": plan["channel"],
            "catalog_revision": plan["revision"],
            "catalog_release_tag": plan["tag"],
            "source_repository": plan["sourceRepository"],
            "source_firmware_version": manifest["firmware"]["version"],
            "target_firmware_commit": plan["targetFirmware"]["commit"],
        }
    )
    manifest["package_release"] = package_release
    manifest["release_id"] = manifest_release_id(manifest)
    _write_json(manifest_path, manifest)
    _canonicalize_archive(manifest, archive_path)
    changed_targets = (
        verify_bounded_delta(base_directory, directory, plan)
        if base_directory is not None
        else []
    )
    checksum_path = _checksums(directory, plan["tag"])

    assets = {
        name: _asset_evidence(directory / name)
        for name in (*CANONICAL_ASSETS, checksum_path.name)
    }
    provenance = {
        "schema": 1,
        "kind": "nativePackageRelease",
        "channel": plan["channel"],
        "revision": plan["revision"],
        "tag": plan["tag"],
        "publisher": {
            "repository": plan["publisherRepository"],
            "commit": plan["publisherCommit"],
        },
        "firmwareSource": {
            "repository": plan["sourceRepository"],
            "commit": plan["sourceCommit"],
        },
        "targetFirmware": plan["targetFirmware"],
        "baseCatalog": {
            "tag": plan["baseRelease"]["tag"],
            "releaseId": plan["baseRelease"]["releaseId"],
            "assets": plan["baseRelease"]["assets"],
        },
        "overlayPolicy": {
            "targets": plan["overlayTargets"],
            "maxChangedTargets": plan["maxChangedTargets"],
        },
        "changedTargets": changed_targets,
        "manifestReleaseId": manifest["release_id"],
        "assets": assets,
    }
    _write_json(directory / PROVENANCE_NAME, provenance)
    verify_native_release(directory, plan, base_directory)


def verify_native_release(
    directory: Path, plan: dict[str, Any], base_directory: Path | None = None
) -> None:
    verify_release_directory(directory)
    provenance = load_json(directory / PROVENANCE_NAME)
    exact = {
        "schema": 1,
        "kind": "nativePackageRelease",
        "channel": plan["channel"],
        "revision": plan["revision"],
        "tag": plan["tag"],
        "publisher": {
            "repository": plan["publisherRepository"],
            "commit": plan["publisherCommit"],
        },
        "firmwareSource": {
            "repository": plan["sourceRepository"],
            "commit": plan["sourceCommit"],
        },
        "targetFirmware": plan["targetFirmware"],
        "baseCatalog": {
            "tag": plan["baseRelease"]["tag"],
            "releaseId": plan["baseRelease"]["releaseId"],
            "assets": plan["baseRelease"]["assets"],
        },
        "overlayPolicy": {
            "targets": plan["overlayTargets"],
            "maxChangedTargets": plan["maxChangedTargets"],
        },
    }
    for key, value in exact.items():
        if provenance.get(key) != value:
            raise ContractError(f"native provenance {key} differs")
    changed_targets = provenance.get("changedTargets")
    if (
        not isinstance(changed_targets, list)
        or any(not isinstance(item, str) for item in changed_targets)
        or changed_targets != sorted(set(changed_targets))
        or not set(changed_targets).issubset(plan["overlayTargets"])
        or len(changed_targets) > plan["maxChangedTargets"]
    ):
        raise ContractError("native provenance changedTargets differs")
    if base_directory is not None:
        actual_changed = verify_bounded_delta(base_directory, directory, plan)
        if changed_targets != actual_changed:
            raise ContractError("native provenance changedTargets evidence differs")
    manifest = load_json(directory / "tumoflip-packages.json")
    if provenance.get("manifestReleaseId") != manifest.get("release_id"):
        raise ContractError("native provenance manifest release ID differs")
    checksum_name = f"{plan['tag']}-SHA256SUMS"
    expected_names = {*CANONICAL_ASSETS, checksum_name}
    assets = provenance.get("assets")
    if not isinstance(assets, dict) or set(assets) != expected_names:
        raise ContractError("native provenance asset set differs")
    for name in sorted(expected_names):
        evidence = assets.get(name)
        path = directory / name
        if (
            not isinstance(evidence, dict)
            or evidence.get("bytes") != path.stat().st_size
            or evidence.get("sha256") != sha256(path)
        ):
            raise ContractError(f"native provenance asset differs: {name}")


def build_native_release(
    source_root: Path,
    base_directory: Path,
    output: Path,
    plan: dict[str, Any],
    build_dir: str = "build/f7-firmware-C",
    runner: Runner = default_runner,
) -> None:
    """Compose selected source-owned exports, then atomically expose verified assets."""

    prove_source_checkout(source_root, plan["sourceCommit"], runner)
    verify_release_directory(base_directory, plan["baseRelease"])
    if output.exists():
        raise ContractError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=str(output.parent))
    )
    try:
        _compose_selected_release(
            source_root,
            source_root / build_dir,
            base_directory,
            staging,
            plan,
        )
        prove_source_checkout(source_root, plan["sourceCommit"], runner)
        finalize_native_release(staging, plan, base_directory)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--control-root", type=Path, default=Path("."))
    value.add_argument("--source-root", type=Path, required=True)
    value.add_argument("--base-directory", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    value.add_argument("--channel", choices=("stable", "dev"), required=True)
    value.add_argument("--revision", type=int, required=True)
    value.add_argument("--source-commit", required=True)
    value.add_argument("--publisher-commit", required=True)
    value.add_argument("--build-dir", default="build/f7-firmware-C")
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        plan = load_native_plan(
            args.control_root.resolve(),
            args.channel,
            args.revision,
            args.source_commit,
            args.publisher_commit,
        )
        build_native_release(
            args.source_root.resolve(),
            args.base_directory.resolve(),
            args.output.resolve(),
            plan,
            args.build_dir,
        )
    except (ContractError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"verified native release: {plan['tag']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
