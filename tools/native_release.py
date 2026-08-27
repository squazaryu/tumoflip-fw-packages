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
import struct
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
    current = load_json(control_root / "contracts/current-releases.json")
    policy = load_json(control_root / "contracts/native-build-policy.json")
    source_repository = _repository(
        source_contract.get("firmwareRepository"), "firmware repository"
    )
    publisher_repository = _repository(
        source_contract.get("publisherRepository"), "publisher repository"
    )
    if source_contract.get("buildParallelism") != 2:
        raise ContractError("build parallelism contract must remain exactly 2")
    build_runner = source_contract.get("buildRunner")
    toolchain_version = source_contract.get("toolchainVersion")
    if build_runner != "ubuntu-24.04" or toolchain_version != "39":
        raise ContractError("native build environment contract differs")
    allowed_overlays = policy.get("allowedOverlays")
    overlay_groups = policy.get("overlayGroups")
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
        or not isinstance(overlay_groups, dict)
        or set(overlay_groups) != set(allowed_overlays)
        or any(group not in PACKAGE_GROUPS for group in overlay_groups.values())
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
        base_release = current["channels"][channel]
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
    if not isinstance(release_policy, dict):
        raise ContractError(f"native release {tag} has no exact non-empty overlay plan")
    mode = release_policy.get("mode", "overlay")
    selected_names = release_policy.get("selectedOverlays")
    if mode not in {"overlay", "baseline", "firmwareSnapshot"}:
        raise ContractError(f"native release {tag} mode is invalid")
    if not isinstance(selected_names, list) or any(
        not isinstance(name, str) or name not in allowed_overlays
        for name in selected_names
    ) or len(set(selected_names)) != len(selected_names):
        raise ContractError(f"native release {tag} overlay plan is invalid")
    if mode == "overlay" and not selected_names:
        raise ContractError(f"native release {tag} has no exact non-empty overlay plan")
    if mode in {"baseline", "firmwareSnapshot"} and selected_names:
        raise ContractError(f"{mode} release must not contain overlays")
    if mode == "firmwareSnapshot" and channel != "stable":
        raise ContractError("firmware snapshot must be a stable release")
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
    selected_overlay_groups = {
        selected_overlays[name]: overlay_groups[name] for name in selected_overlays
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
    snapshot_manifest_sha = baseline.get("packageManifestSHA256")
    snapshot_zip_sha = baseline.get("packageZipSHA256")
    if mode in {"baseline", "firmwareSnapshot"}:
        if source_commit != firmware_commit:
            raise ContractError(f"{mode} source differs from target firmware")
    if mode == "firmwareSnapshot":
        for value, label in (
            (snapshot_manifest_sha, "snapshot manifest SHA-256"),
            (snapshot_zip_sha, "snapshot ZIP SHA-256"),
        ):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ContractError(f"{label} contract is invalid")

    return {
        "mode": mode,
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
        "overlayGroups": selected_overlay_groups,
        "maxChangedTargets": len(selected_overlays),
        "buildEnvironment": {
            "runner": build_runner,
            "toolchainVersion": toolchain_version,
        },
        "targetFirmware": {
            "repository": source_repository,
            "tag": firmware_tag,
            "commit": firmware_commit,
            "releaseId": firmware_release_id,
            "version": firmware_version,
            "api": api,
            "target": target,
            "packageManifestSHA256": snapshot_manifest_sha,
            "packageZipSHA256": snapshot_zip_sha,
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
        "target_source_commit": expected_firmware["commit"],
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
        or any(
            not isinstance(entry.get("source"), str)
            or not isinstance(entry.get("target"), str)
            or not isinstance(entry.get("bytes"), int)
            or isinstance(entry.get("bytes"), bool)
            or entry.get("bytes", 0) < 1
            or re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256"))) is None
            or re.fullmatch(r"[0-9a-f]{32}", str(entry.get("md5"))) is None
            for entry in synced
        )
        or sorted(entry.get("target") for entry in synced) != plan["overlayTargets"]
    ):
        raise ContractError("source manifest synced overlay set differs")
    if not isinstance(manifest.get("artifacts"), dict):
        raise ContractError("source manifest firmware artifact evidence is invalid")
    packages = manifest.get("packages")
    if not isinstance(packages, dict) or set(packages) != PACKAGE_GROUPS:
        raise ContractError("source manifest package groups differ from client contract")


def _validate_final_manifest_identity(
    manifest: dict[str, Any], plan: dict[str, Any]
) -> None:
    package_release = manifest.get("package_release")
    if not isinstance(package_release, dict):
        raise ContractError("native package release identity is missing")
    expected = {
        "id": plan["tag"],
        "source_repository": plan["sourceRepository"],
        "source_firmware_version": plan["targetFirmware"]["version"],
        "target_firmware_commit": plan["targetFirmware"]["commit"],
        "catalog_install_scope": {
            "baseline": "baseline",
            "firmwareSnapshot": "firmwareSnapshot",
            "overlay": "delta",
        }[plan["mode"]],
        "catalog_channel": plan["channel"],
        "catalog_revision": plan["revision"],
        "catalog_release_tag": plan["tag"],
    }
    for field, value in expected.items():
        if package_release.get(field) != value:
            raise ContractError(f"native package release {field} differs")


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


def _gnu_debuglink_crc_range(data: bytes) -> tuple[int, int] | None:
    """Locate the non-runtime CRC field in a 32-bit little-endian ELF file."""

    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
        return None
    try:
        section_offset = struct.unpack_from("<I", data, 32)[0]
        section_size, section_count, names_index = struct.unpack_from(
            "<HHH", data, 46
        )
    except struct.error:
        return None
    if (
        section_size != 40
        or section_count < 2
        or names_index == 0
        or names_index >= section_count
        or section_offset > len(data)
        or section_count > (len(data) - section_offset) // section_size
    ):
        return None

    def section(index: int) -> tuple[int, int, int] | None:
        offset = section_offset + index * section_size
        try:
            name_offset, file_offset, file_size = struct.unpack_from(
                "<I12xII", data, offset
            )
        except struct.error:
            return None
        if file_offset > len(data) or file_size > len(data) - file_offset:
            return None
        return name_offset, file_offset, file_size

    names_section = section(names_index)
    if names_section is None:
        return None
    _, names_offset, names_size = names_section
    names = data[names_offset : names_offset + names_size]
    matches: list[tuple[int, int]] = []
    for index in range(1, section_count):
        item = section(index)
        if item is None:
            return None
        name_offset, file_offset, file_size = item
        if name_offset >= len(names):
            return None
        name_end = names.find(b"\0", name_offset)
        if name_end < 0:
            return None
        if names[name_offset:name_end] != b".gnu_debuglink":
            continue
        contents = data[file_offset : file_offset + file_size]
        filename_end = contents.find(b"\0")
        if filename_end < 0:
            return None
        crc_offset = (filename_end + 4) & ~3
        if crc_offset + 4 != len(contents):
            return None
        matches.append((file_offset + crc_offset, file_offset + crc_offset + 4))
    return matches[0] if len(matches) == 1 else None


def _runtime_equivalent_fap(previous: bytes, candidate: bytes) -> bool:
    """Return true when FAP bytes differ only by the debug-file CRC."""

    if previous == candidate:
        return True
    if len(previous) != len(candidate):
        return False
    old_crc = _gnu_debuglink_crc_range(previous)
    new_crc = _gnu_debuglink_crc_range(candidate)
    if old_crc is None or new_crc is None or old_crc != new_crc:
        return False
    start, end = old_crc
    return previous[:start] == candidate[:start] and previous[end:] == candidate[end:]


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
    selected_groups = plan.get("overlayGroups")
    if selected_groups is None:
        selected_groups = {
            source: base_entries[source][0]
            for source in selected_paths
            if source in base_entries
        }
    if (
        not isinstance(selected_groups, dict)
        or set(selected_groups) != selected_paths
        or any(group not in PACKAGE_GROUPS for group in selected_groups.values())
    ):
        raise ContractError("selected overlay groups are invalid")
    existing_paths = selected_paths & set(base_entries)
    with zipfile.ZipFile(base_directory / "tumoflip-packages.zip") as base_zip:
        base_payloads = {source: base_zip.read(source) for source in existing_paths}
    synced: list[dict[str, Any]] = []
    for source in sorted(selected_paths):
        expected_group = selected_groups[source]
        filename = exports[source]
        artifact = build_directory / ".extapps" / filename
        if not artifact.is_file():
            raise ContractError(f"selected source build artifact is missing: {filename}")
        data = artifact.read_bytes()
        if not data:
            raise ContractError(f"selected source build artifact is empty: {filename}")
        if source in base_entries:
            group, old_entry = base_entries[source]
            if group != expected_group:
                raise ContractError(f"selected overlay group differs: {source}")
            if _runtime_equivalent_fap(base_payloads[source], data):
                raise ContractError(f"selected overlay has no runtime change: {source}")
            target = old_entry["target"]
        else:
            group = expected_group
            target = f"/ext/{source}"
            if any(
                isinstance(entry, dict) and entry.get("target") == target
                for entries in packages.values()
                for entry in entries
            ):
                raise ContractError(f"selected overlay target collides with base: {source}")
        replacement = {
            "source": source,
            "target": target,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": hashlib.md5(data).hexdigest(),
        }
        matches = [
            index
            for index, entry in enumerate(packages[group])
            if isinstance(entry, dict) and entry.get("source") == source
        ]
        if source in base_entries and len(matches) != 1:
            raise ContractError(f"selected overlay route is ambiguous: {source}")
        if source in base_entries:
            packages[group][matches[0]] = replacement
        else:
            if group not in packages:
                raise ContractError(f"selected overlay group is missing: {group}")
            packages[group].append(replacement)
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
    _validate_source_manifest(candidate, plan)
    _validate_final_manifest_identity(candidate, plan)
    base_entries = _entries_by_source(base)
    candidate_entries = _entries_by_source(candidate)
    allowed = set(plan["overlayTargets"])
    base_sources = set(base_entries)
    candidate_sources = set(candidate_entries)
    removed = base_sources - candidate_sources
    added = candidate_sources - base_sources
    if removed:
        raise ContractError("native package topology removed an immutable base entry")
    if added - allowed:
        raise ContractError("native package topology added an unapproved entry")
    selected_groups = plan.get("overlayGroups", {})
    if not isinstance(selected_groups, dict):
        raise ContractError("native overlay groups are missing")
    for source in sorted(added):
        group, entry = candidate_entries[source]
        if selected_groups.get(source) != group:
            raise ContractError(f"native package group differs: {source}")
        if entry.get("target") != f"/ext/{source}":
            raise ContractError(f"native package target differs: {source}")

    changed: set[str] = set(added)
    for source in sorted(base_sources):
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
    if candidate.get("firmware") != base.get("firmware"):
        raise ContractError("native firmware compatibility differs from immutable base")
    if candidate.get("artifacts") != base.get("artifacts"):
        raise ContractError("native firmware artifact evidence differs from immutable base")
    base_package_release = base.get("package_release")
    candidate_package_release = candidate.get("package_release")
    if (
        not isinstance(base_package_release, dict)
        or not isinstance(candidate_package_release, dict)
        or any(
            candidate_package_release.get(field) != base_package_release.get(field)
            for field in (
                "target_release_id",
                "target_release_tag",
                "target_source_commit",
            )
        )
    ):
        raise ContractError("native target firmware release lineage differs")
    with zipfile.ZipFile(base_directory / "tumoflip-packages.zip") as old_zip:
        with zipfile.ZipFile(directory / "tumoflip-packages.zip") as new_zip:
            old_names = set(old_zip.namelist())
            new_names = set(new_zip.namelist())
            if old_names - new_names:
                raise ContractError("native ZIP removed an immutable base member")
            if new_names - old_names - allowed:
                raise ContractError("native ZIP added an unapproved member")
            for source in sorted(base_sources - allowed):
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
            "id": plan["tag"],
            "catalog_channel": plan["channel"],
            "catalog_revision": plan["revision"],
            "catalog_release_tag": plan["tag"],
            "source_repository": plan["sourceRepository"],
            "source_firmware_version": manifest["firmware"]["version"],
            "target_firmware_commit": plan["targetFirmware"]["commit"],
            "catalog_install_scope": "delta",
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
        "buildEnvironment": plan["buildEnvironment"],
        "sourceBuiltOverlays": manifest["package_release"]["synced_extapps"],
        "changedTargets": changed_targets,
        "manifestReleaseId": manifest["release_id"],
        "assets": assets,
    }
    _write_json(directory / PROVENANCE_NAME, provenance)
    verify_native_release(directory, plan, base_directory)


def _snapshot_changed_sources(
    base: dict[str, Any], snapshot: dict[str, Any]
) -> list[str]:
    """Return the exact package sources added, removed, or changed by stable firmware."""

    old = _entries_by_source(base)
    new = _entries_by_source(snapshot)
    return sorted(
        source
        for source in set(old) | set(new)
        if old.get(source) != new.get(source)
    )


def _validate_snapshot_source(
    target_directory: Path, plan: dict[str, Any]
) -> dict[str, Any]:
    manifest_path = target_directory / "tumoflip-packages.json"
    archive_path = target_directory / "tumoflip-packages.zip"
    if not manifest_path.is_file() or not archive_path.is_file():
        raise ContractError("firmware snapshot package assets are missing")
    if sha256(manifest_path) != plan["targetFirmware"]["packageManifestSHA256"]:
        raise ContractError("firmware snapshot manifest SHA-256 differs")
    if sha256(archive_path) != plan["targetFirmware"]["packageZipSHA256"]:
        raise ContractError("firmware snapshot ZIP SHA-256 differs")
    manifest = load_json(manifest_path)
    verify_archive(manifest, archive_path)
    if manifest.get("release_id") != plan["targetFirmware"]["releaseId"]:
        raise ContractError("firmware snapshot release ID differs")
    if manifest.get("package_release") is not None:
        raise ContractError("firmware snapshot unexpectedly contains package-release metadata")
    firmware = manifest.get("firmware")
    expected = plan["targetFirmware"]
    if not isinstance(firmware, dict) or any(
        firmware.get(field) != expected[value]
        for field, value in (("version", "version"), ("api", "api"), ("target", "target"))
    ):
        raise ContractError("firmware snapshot compatibility identity differs")
    return manifest


def _snapshot_package_release(
    snapshot: dict[str, Any], base: dict[str, Any], base_directory: Path, plan: dict[str, Any]
) -> dict[str, Any]:
    manifest = copy.deepcopy(snapshot)
    manifest.pop("release_id", None)
    manifest["package_release"] = {
        "type": "package-only",
        "id": plan["tag"],
        "source_commit": plan["sourceCommit"],
        "source_dirty": False,
        "source_firmware_version": plan["targetFirmware"]["version"],
        "target_release_tag": plan["targetFirmware"]["tag"],
        "target_release_id": plan["targetFirmware"]["releaseId"],
        "target_source_commit": plan["targetFirmware"]["commit"],
        "firmware_flash_unchanged": True,
        "overlay_targets": [],
        "synced_extapps": [],
        "catalog_channel": plan["channel"],
        "catalog_revision": plan["revision"],
        "catalog_release_tag": plan["tag"],
        "catalog_install_scope": "firmwareSnapshot",
        "source_repository": plan["sourceRepository"],
        "target_firmware_commit": plan["targetFirmware"]["commit"],
        "base_catalog": {
            "release_tag": plan["baseRelease"]["tag"],
            "release_id": base["release_id"],
            "manifest_sha256": sha256(base_directory / "tumoflip-packages.json"),
            "package_zip_sha256": sha256(base_directory / "tumoflip-packages.zip"),
            "source_commit": plan["baseRelease"]["sourceCommit"],
        },
        "compatible_releases": [],
    }
    manifest["release_id"] = manifest_release_id(manifest)
    return manifest


def build_firmware_snapshot_release(
    target_directory: Path,
    base_directory: Path,
    output: Path,
    plan: dict[str, Any],
) -> None:
    """Atomically promote one exact stable firmware package snapshot."""

    if plan.get("mode") != "firmwareSnapshot":
        raise ContractError("release plan is not a firmware snapshot")
    verify_release_directory(base_directory, plan["baseRelease"])
    snapshot = _validate_snapshot_source(target_directory, plan)
    base = load_json(base_directory / "tumoflip-packages.json")
    if output.exists():
        raise ContractError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=str(output.parent)))
    try:
        manifest = _snapshot_package_release(snapshot, base, base_directory, plan)
        _write_json(staging / "tumoflip-packages.json", manifest)
        shutil.copyfile(
            target_directory / "tumoflip-packages.zip",
            staging / "tumoflip-packages.zip",
        )
        verify_archive(manifest, staging / "tumoflip-packages.zip")
        checksum_path = _checksums(staging, plan["tag"])
        changed_sources = _snapshot_changed_sources(base, snapshot)
        assets = {
            name: _asset_evidence(staging / name)
            for name in (*CANONICAL_ASSETS, checksum_path.name)
        }
        provenance = {
            "schema": 1,
            "kind": "firmwareSnapshotPackageRelease",
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
            "targetSnapshot": {
                "manifestSHA256": plan["targetFirmware"]["packageManifestSHA256"],
                "packageZipSHA256": plan["targetFirmware"]["packageZipSHA256"],
                "manifestReleaseId": plan["targetFirmware"]["releaseId"],
            },
            "changedSources": changed_sources,
            "manifestReleaseId": manifest["release_id"],
            "assets": assets,
        }
        _write_json(staging / PROVENANCE_NAME, provenance)
        verify_native_release(staging, plan, base_directory, target_directory)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _baseline_package_release(
    snapshot: dict[str, Any],
    base: dict[str, Any],
    base_directory: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Turn a firmware package snapshot into an independent empty baseline."""

    manifest = copy.deepcopy(snapshot)
    manifest.pop("release_id", None)
    manifest["package_release"] = {
        "type": "package-only",
        "id": plan["tag"],
        "source_commit": plan["sourceCommit"],
        "source_dirty": False,
        "source_firmware_version": plan["targetFirmware"]["version"],
        "target_release_tag": plan["targetFirmware"]["tag"],
        "target_release_id": plan["targetFirmware"]["releaseId"],
        "target_source_commit": plan["targetFirmware"]["commit"],
        "firmware_flash_unchanged": True,
        "overlay_targets": [],
        "catalog_modified_targets": [],
        "synced_extapps": [],
        "catalog_channel": plan["channel"],
        "catalog_revision": plan["revision"],
        "catalog_release_tag": plan["tag"],
        "catalog_install_scope": "baseline",
        "source_repository": plan["sourceRepository"],
        "base_catalog": {
            "release_tag": plan["baseRelease"]["tag"],
            "release_id": base["release_id"],
            "manifest_sha256": sha256(base_directory / "tumoflip-packages.json"),
            "package_zip_sha256": sha256(base_directory / "tumoflip-packages.zip"),
            "source_commit": plan["baseRelease"]["sourceCommit"],
        },
        "compatible_releases": [],
    }
    manifest["release_id"] = manifest_release_id(manifest)
    return manifest


def build_baseline_release(
    target_directory: Path,
    base_directory: Path,
    output: Path,
    plan: dict[str, Any],
) -> None:
    """Publish a complete firmware-owned surface with no managed overlays."""

    if plan.get("mode") != "baseline":
        raise ContractError("release plan is not a baseline")
    verify_release_directory(base_directory, plan["baseRelease"])
    snapshot = _validate_snapshot_source(target_directory, plan)
    base = load_json(base_directory / "tumoflip-packages.json")
    if output.exists():
        raise ContractError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=str(output.parent)))
    try:
        manifest = _baseline_package_release(snapshot, base, base_directory, plan)
        _write_json(staging / "tumoflip-packages.json", manifest)
        shutil.copyfile(target_directory / "tumoflip-packages.zip", staging / "tumoflip-packages.zip")
        verify_archive(manifest, staging / "tumoflip-packages.zip")
        checksum_path = _checksums(staging, plan["tag"])
        assets = {
            name: _asset_evidence(staging / name)
            for name in (*CANONICAL_ASSETS, checksum_path.name)
        }
        provenance = {
            "schema": 1,
            "kind": "nativeBaselinePackageRelease",
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
            "overlayPolicy": {"targets": [], "maxChangedTargets": 0},
            "changedSources": _snapshot_changed_sources(base, snapshot),
            "manifestReleaseId": manifest["release_id"],
            "assets": assets,
        }
        _write_json(staging / PROVENANCE_NAME, provenance)
        verify_native_release(staging, plan, base_directory, target_directory)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _verify_baseline_release(
    directory: Path,
    plan: dict[str, Any],
    base_directory: Path,
    target_directory: Path,
) -> None:
    verify_release_directory(base_directory, plan["baseRelease"])
    snapshot = _validate_snapshot_source(target_directory, plan)
    base = load_json(base_directory / "tumoflip-packages.json")
    manifest = load_json(directory / "tumoflip-packages.json")
    expected_manifest = _baseline_package_release(snapshot, base, base_directory, plan)
    if manifest != expected_manifest:
        raise ContractError("baseline catalog manifest differs")
    if sha256(directory / "tumoflip-packages.zip") != sha256(
        target_directory / "tumoflip-packages.zip"
    ):
        raise ContractError("baseline package ZIP differs")
    provenance = load_json(directory / PROVENANCE_NAME)
    checksum_name = f"{plan['tag']}-SHA256SUMS"
    expected_names = {*CANONICAL_ASSETS, checksum_name}
    exact = {
        "schema": 1,
        "kind": "nativeBaselinePackageRelease",
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
        "overlayPolicy": {"targets": [], "maxChangedTargets": 0},
        "changedSources": _snapshot_changed_sources(base, snapshot),
        "manifestReleaseId": manifest["release_id"],
    }
    for key, value in exact.items():
        if provenance.get(key) != value:
            raise ContractError(f"baseline provenance {key} differs")
    assets = provenance.get("assets")
    if not isinstance(assets, dict) or set(assets) != expected_names:
        raise ContractError("baseline provenance asset set differs")
    for name in sorted(expected_names):
        evidence = assets.get(name)
        path = directory / name
        if (
            not isinstance(evidence, dict)
            or evidence.get("bytes") != path.stat().st_size
            or evidence.get("sha256") != sha256(path)
        ):
            raise ContractError(f"baseline provenance asset differs: {name}")


def _verify_firmware_snapshot_release(
    directory: Path,
    plan: dict[str, Any],
    base_directory: Path,
    target_directory: Path,
) -> None:
    verify_release_directory(base_directory, plan["baseRelease"])
    snapshot = _validate_snapshot_source(target_directory, plan)
    base = load_json(base_directory / "tumoflip-packages.json")
    manifest = load_json(directory / "tumoflip-packages.json")
    expected_manifest = _snapshot_package_release(snapshot, base, base_directory, plan)
    if manifest != expected_manifest:
        raise ContractError("firmware snapshot catalog manifest differs")
    if sha256(directory / "tumoflip-packages.zip") != sha256(
        target_directory / "tumoflip-packages.zip"
    ):
        raise ContractError("firmware snapshot package ZIP differs")
    provenance = load_json(directory / PROVENANCE_NAME)
    checksum_name = f"{plan['tag']}-SHA256SUMS"
    expected_names = {*CANONICAL_ASSETS, checksum_name}
    exact = {
        "schema": 1,
        "kind": "firmwareSnapshotPackageRelease",
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
        "targetSnapshot": {
            "manifestSHA256": plan["targetFirmware"]["packageManifestSHA256"],
            "packageZipSHA256": plan["targetFirmware"]["packageZipSHA256"],
            "manifestReleaseId": plan["targetFirmware"]["releaseId"],
        },
        "changedSources": _snapshot_changed_sources(base, snapshot),
        "manifestReleaseId": manifest["release_id"],
    }
    for key, value in exact.items():
        if provenance.get(key) != value:
            raise ContractError(f"firmware snapshot provenance {key} differs")
    assets = provenance.get("assets")
    if not isinstance(assets, dict) or set(assets) != expected_names:
        raise ContractError("firmware snapshot provenance asset set differs")
    for name in sorted(expected_names):
        evidence = assets.get(name)
        path = directory / name
        if (
            not isinstance(evidence, dict)
            or evidence.get("bytes") != path.stat().st_size
            or evidence.get("sha256") != sha256(path)
        ):
            raise ContractError(f"firmware snapshot provenance asset differs: {name}")


def verify_native_release(
    directory: Path,
    plan: dict[str, Any],
    base_directory: Path | None = None,
    target_directory: Path | None = None,
) -> None:
    verify_release_directory(directory)
    if plan.get("mode") == "firmwareSnapshot":
        if base_directory is None or target_directory is None:
            raise ContractError("firmware snapshot verification requires base and target assets")
        _verify_firmware_snapshot_release(
            directory, plan, base_directory, target_directory
        )
        return
    if plan.get("mode") == "baseline":
        if base_directory is None or target_directory is None:
            raise ContractError("baseline verification requires base and target assets")
        _verify_baseline_release(
            directory, plan, base_directory, target_directory
        )
        return
    manifest = load_json(directory / "tumoflip-packages.json")
    _validate_source_manifest(manifest, plan)
    _validate_final_manifest_identity(manifest, plan)
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
        "buildEnvironment": plan["buildEnvironment"],
    }
    for key, value in exact.items():
        if provenance.get(key) != value:
            raise ContractError(f"native provenance {key} differs")
    if provenance.get("sourceBuiltOverlays") != manifest["package_release"].get(
        "synced_extapps"
    ):
        raise ContractError("native provenance source-built overlays differ")
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

    if plan.get("mode") != "overlay":
        raise ContractError("source build requires an overlay release plan")
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
    value.add_argument("--source-root", type=Path)
    value.add_argument("--target-directory", type=Path)
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
        if plan["mode"] in {"firmwareSnapshot", "baseline"}:
            if args.target_directory is None:
                raise ContractError(f"{plan['mode']} release requires --target-directory")
            builder = (
                build_firmware_snapshot_release
                if plan["mode"] == "firmwareSnapshot"
                else build_baseline_release
            )
            builder(
                args.target_directory.resolve(),
                args.base_directory.resolve(),
                args.output.resolve(),
                plan,
            )
        else:
            if args.source_root is None:
                raise ContractError("overlay release requires --source-root")
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
