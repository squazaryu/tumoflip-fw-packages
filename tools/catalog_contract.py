#!/usr/bin/env python3
"""Fail-closed validation primitives for Tumoflip package catalogs."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


HEX_32 = re.compile(r"^[0-9a-f]{32}$")
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
PACKAGE_TAG = re.compile(r"^fw-packages-(stable|dev)-([0-9]{3})$")
AUDIT_TAG = re.compile(r"^audit-ledger-[0-9]{8}-[0-9]{3}$")
CANONICAL_PACKAGE_ASSETS = (
    "tumoflip-packages.json",
    "tumoflip-packages.zip",
)
MAX_ARCHIVE_MEMBERS = 2_000
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


class ContractError(RuntimeError):
    """Raised when release evidence is incomplete, ambiguous, or unsafe."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ContractError(f"invalid JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_release_id(manifest: dict[str, Any]) -> str:
    unsigned = dict(manifest)
    unsigned.pop("release_id", None)
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value


def _safe_relative_archive_path(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ContractError(f"unsafe {label}: {value!r}")
    return path


def _canonical_absolute_target_path(value: str, label: str) -> str:
    path = PurePosixPath(value)
    has_control = any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value
    )
    if (
        "\\" in value
        or has_control
        or not path.is_absolute()
        or value != str(path)
        or len(path.parts) < 3
        or path.parts[0] != "/"
        or path.parts[1] != "ext"
        or any(part in {"", ".", ".."} for part in path.parts[2:])
    ):
        raise ContractError(f"unsafe {label}: {value!r}")
    return str(path)


def validate_manifest(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if manifest.get("schema") != 2:
        raise ContractError("manifest schema must be 2")
    release_id = manifest.get("release_id")
    if not isinstance(release_id, str) or not HEX_64.fullmatch(release_id):
        raise ContractError("manifest release_id is invalid")
    computed_release_id = manifest_release_id(manifest)
    if computed_release_id != release_id:
        raise ContractError(
            f"manifest release_id mismatch: expected {computed_release_id}, got {release_id}"
        )

    firmware = manifest.get("firmware")
    if not isinstance(firmware, dict):
        raise ContractError("manifest firmware must be an object")
    _require_string(firmware.get("version"), "firmware.version")
    api = _require_string(firmware.get("api"), "firmware.api")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", api):
        raise ContractError("firmware.api must be major.minor")
    target = firmware.get("target")
    if not isinstance(target, int) or isinstance(target, bool) or target < 1:
        raise ContractError("firmware.target must be a positive integer")

    package_release = manifest.get("package_release")
    if package_release is not None:
        if not isinstance(package_release, dict):
            raise ContractError("package_release must be an object")
        channel = package_release.get("catalog_channel")
        revision = package_release.get("catalog_revision")
        tag = _require_string(
            package_release.get("catalog_release_tag"),
            "package_release.catalog_release_tag",
        )
        match = PACKAGE_TAG.fullmatch(tag)
        if channel not in {"stable", "dev"} or match is None:
            raise ContractError("package release tag/channel is invalid")
        if match.group(1) != channel:
            raise ContractError("package release tag channel differs")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ContractError("package release revision is invalid")
        if int(match.group(2)) != revision:
            raise ContractError("package release tag revision differs")
        source_commit = package_release.get("source_commit")
        if not isinstance(source_commit, str) or not HEX_40.fullmatch(source_commit):
            raise ContractError("package release source_commit is invalid")
        if package_release.get("source_dirty") is not False:
            raise ContractError("package release source must be clean")
        scope = package_release.get("catalog_install_scope")
        if scope is not None and scope not in {"baseline", "delta", "firmwareSnapshot"}:
            raise ContractError("package release catalog_install_scope is invalid")

    packages = manifest.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise ContractError("manifest packages must be a non-empty object")
    by_source: dict[str, dict[str, Any]] = {}
    targets: set[str] = set()
    for group, entries in packages.items():
        if not isinstance(group, str) or not group or not isinstance(entries, list):
            raise ContractError("package groups must map names to arrays")
        for index, entry in enumerate(entries):
            label = f"packages.{group}[{index}]"
            if not isinstance(entry, dict):
                raise ContractError(f"{label} must be an object")
            source = _require_string(entry.get("source"), f"{label}.source")
            _safe_relative_archive_path(source, f"{label}.source")
            target_path = _require_string(entry.get("target"), f"{label}.target")
            canonical_target = _canonical_absolute_target_path(
                target_path, f"{label}.target"
            )
            digest = entry.get("sha256")
            md5 = entry.get("md5")
            size = entry.get("bytes")
            if not isinstance(digest, str) or not HEX_64.fullmatch(digest):
                raise ContractError(f"{label}.sha256 is invalid")
            if md5 is not None and (not isinstance(md5, str) or not HEX_32.fullmatch(md5)):
                raise ContractError(f"{label}.md5 is invalid")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ContractError(f"{label}.bytes is invalid")
            if source in by_source:
                raise ContractError(f"duplicate package source: {source}")
            if canonical_target in targets:
                raise ContractError(f"duplicate package target: {canonical_target}")
            by_source[source] = entry
            targets.add(canonical_target)
    return by_source


def verify_archive(manifest: dict[str, Any], archive_path: Path) -> None:
    expected = validate_manifest(manifest)
    seen: set[str] = set()
    declared_total = 0
    actual_total = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise ContractError("package archive has too many members")
            for info in infos:
                if (
                    not isinstance(info.file_size, int)
                    or isinstance(info.file_size, bool)
                    or info.file_size < 0
                ):
                    raise ContractError("ZIP member has invalid declared size")
                declared_total += info.file_size
                if declared_total > MAX_ARCHIVE_BYTES:
                    raise ContractError("package archive exceeds declared size limit")
                if info.is_dir():
                    if info.file_size != 0:
                        raise ContractError("ZIP directory has non-zero declared size")
                    continue
                source = str(_safe_relative_archive_path(info.filename, "ZIP member"))
                mode = (info.external_attr >> 16) & 0xFFFF
                if mode and stat.S_ISLNK(mode):
                    raise ContractError(f"ZIP symlink is forbidden: {source}")
                if source in seen:
                    raise ContractError(f"duplicate ZIP member: {source}")
                entry = expected.get(source)
                if entry is None:
                    raise ContractError(f"unexpected ZIP member: {source}")
                if info.file_size != entry["bytes"]:
                    raise ContractError(f"ZIP member declared size differs: {source}")
                data = archive.read(info)
                if len(data) != info.file_size:
                    raise ContractError(f"ZIP member actual size differs: {source}")
                actual_total += len(data)
                if actual_total > MAX_ARCHIVE_BYTES:
                    raise ContractError("package archive exceeds size limit")
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise ContractError(f"ZIP member SHA-256 differs: {source}")
                if entry.get("md5") and hashlib.md5(data).hexdigest() != entry["md5"]:
                    raise ContractError(f"ZIP member MD5 differs: {source}")
                seen.add(source)
    except (OSError, zipfile.BadZipFile) as error:
        raise ContractError(f"invalid package archive {archive_path}: {error}") from error
    missing = sorted(set(expected) - seen)
    if missing:
        raise ContractError(f"package archive is missing {len(missing)} member(s): {missing[0]}")


def parse_checksums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ContractError(f"cannot read checksum file {path}: {error}") from error
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})\s+\*?([^\s]+)", line)
        if match is None:
            raise ContractError(f"invalid checksum line {number}")
        name = match.group(2)
        if name in result:
            raise ContractError(f"duplicate checksum entry: {name}")
        result[name] = match.group(1)
    required = set(CANONICAL_PACKAGE_ASSETS)
    if set(result) != required:
        raise ContractError("checksum file must cover exactly manifest and ZIP")
    return result


def verify_release_directory(directory: Path, expected: dict[str, Any] | None = None) -> None:
    manifest_path = directory / CANONICAL_PACKAGE_ASSETS[0]
    archive_path = directory / CANONICAL_PACKAGE_ASSETS[1]
    for path in (manifest_path, archive_path):
        if not path.is_file():
            raise ContractError(f"missing canonical asset: {path}")

    if expected is not None:
        release_tag = _require_string(expected.get("tag"), "legacy tag")
        if PACKAGE_TAG.fullmatch(release_tag) is None:
            raise ContractError("legacy tag is invalid")
        checksum_path = directory / f"{release_tag}-SHA256SUMS"
        required_assets = {
            *CANONICAL_PACKAGE_ASSETS,
            checksum_path.name,
        }
        pinned_assets = expected.get("assets")
        if not isinstance(pinned_assets, dict) or set(pinned_assets) != required_assets:
            raise ContractError("legacy pinned asset set differs from canonical assets")
        if not checksum_path.is_file():
            raise ContractError(f"missing canonical asset: {checksum_path}")
        for name in sorted(required_assets):
            digest = pinned_assets.get(name)
            if not isinstance(digest, str) or HEX_64.fullmatch(digest) is None:
                raise ContractError(f"legacy pinned digest is invalid: {name}")
            if sha256(directory / name) != digest:
                raise ContractError(f"legacy asset differs from pinned contract: {name}")
        manifest = load_json(manifest_path)
    else:
        manifest = load_json(manifest_path)
        package_release = manifest.get("package_release")
        if not isinstance(package_release, dict):
            raise ContractError("package_release is required for an independent catalog")
        release_tag = _require_string(
            package_release.get("catalog_release_tag"),
            "package_release.catalog_release_tag",
        )
        checksum_path = directory / f"{release_tag}-SHA256SUMS"
        if not checksum_path.is_file():
            raise ContractError(f"missing canonical asset: {checksum_path}")

    package_release = manifest.get("package_release")
    if not isinstance(package_release, dict):
        raise ContractError("package_release is required for an independent catalog")
    manifest_tag = _require_string(
        package_release.get("catalog_release_tag"),
        "package_release.catalog_release_tag",
    )
    if manifest_tag != release_tag:
        raise ContractError("manifest release tag differs from pinned contract")
    validate_manifest(manifest)
    if expected is not None and manifest.get("release_id") != expected.get("releaseId"):
        raise ContractError("legacy release ID differs from pinned contract")
    checksums = parse_checksums(checksum_path)
    for name, digest in checksums.items():
        actual = sha256(directory / name)
        if actual != digest:
            raise ContractError(f"checksum mismatch for {name}")
    verify_archive(manifest, archive_path)


def _require_exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    actual = set(value)
    if actual != keys:
        raise ContractError(
            f"{label} keys differ: missing={sorted(keys - actual)}, extra={sorted(actual - keys)}"
        )


def verify_migration_provenance(
    seed_root: Path,
    legacy_contract: dict[str, Any],
    publisher_repository: str,
    publisher_commit: str,
) -> None:
    """Revalidate mirror provenance and bytes after an artifact/job boundary."""

    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", publisher_repository) is None:
        raise ContractError("publisher repository must be owner/name")
    if HEX_40.fullmatch(publisher_commit) is None:
        raise ContractError("publisher commit must be an exact SHA")
    index = load_json(seed_root / "seed-index.json")
    _require_exact_keys(index, {"schema", "sourceRepository", "channels"}, "seed index")
    if index.get("schema") != 1 or index.get("sourceRepository") != legacy_contract.get(
        "repository"
    ):
        raise ContractError("seed index does not match legacy source contract")
    index_channels = index.get("channels")
    if not isinstance(index_channels, dict) or set(index_channels) != {"stable", "dev"}:
        raise ContractError("seed index channels must be exactly stable and dev")
    for channel in ("stable", "dev"):
        expected = legacy_contract["channels"][channel]
        source = index_channels[channel]
        if not isinstance(source, dict):
            raise ContractError(f"seed index {channel} must be an object")
        _require_exact_keys(
            source,
            {
                "tag",
                "legacyReleaseId",
                "legacyReleaseURL",
                "legacyTagCommit",
                "sourceCommit",
                "manifestReleaseId",
                "assets",
            },
            f"seed index {channel}",
        )
        provenance = load_json(seed_root / channel / "migration-provenance.json")
        _require_exact_keys(
            provenance,
            {
                "schema",
                "kind",
                "channel",
                "publisher",
                "legacy",
                "firmwareSourceCommit",
                "manifestReleaseId",
                "assets",
            },
            f"{channel} migration provenance",
        )
        if provenance.get("schema") != 1 or provenance.get("kind") != "legacyByteMirror":
            raise ContractError(f"{channel} migration provenance type is invalid")
        if provenance.get("channel") != channel:
            raise ContractError(f"{channel} migration provenance channel differs")
        publisher = provenance.get("publisher")
        legacy = provenance.get("legacy")
        if not isinstance(publisher, dict) or not isinstance(legacy, dict):
            raise ContractError(f"{channel} migration provenance identity is incomplete")
        _require_exact_keys(publisher, {"repository", "commit"}, f"{channel} publisher")
        _require_exact_keys(
            legacy,
            {"repository", "tag", "releaseId", "releaseURL", "tagCommit"},
            f"{channel} legacy source",
        )
        if publisher != {
            "repository": publisher_repository,
            "commit": publisher_commit,
        }:
            raise ContractError(f"{channel} publisher identity differs")
        expected_legacy = {
            "repository": legacy_contract["repository"],
            "tag": expected["tag"],
            "releaseId": source["legacyReleaseId"],
            "releaseURL": source["legacyReleaseURL"],
            "tagCommit": expected["tagCommit"],
        }
        if legacy != expected_legacy:
            raise ContractError(f"{channel} legacy release identity differs")
        if source["legacyTagCommit"] != expected["tagCommit"]:
            raise ContractError(f"{channel} seed tag commit differs")
        if source["sourceCommit"] != expected["sourceCommit"]:
            raise ContractError(f"{channel} seed source commit differs")
        if provenance.get("firmwareSourceCommit") != expected["sourceCommit"]:
            raise ContractError(f"{channel} firmware source commit differs")
        if source["manifestReleaseId"] != expected["releaseId"]:
            raise ContractError(f"{channel} seed manifest release ID differs")
        if provenance.get("manifestReleaseId") != expected["releaseId"]:
            raise ContractError(f"{channel} manifest release ID differs")
        assets = provenance.get("assets")
        if not isinstance(assets, dict) or assets != source.get("assets"):
            raise ContractError(f"{channel} provenance assets differ from seed index")
        if set(assets) != set(expected["assets"]):
            raise ContractError(f"{channel} provenance asset names differ")
        for name, evidence in assets.items():
            if not isinstance(evidence, dict):
                raise ContractError(f"{channel}/{name} provenance must be an object")
            _require_exact_keys(
                evidence,
                {"bytes", "sha256", "githubAssetId"},
                f"{channel}/{name} provenance",
            )
            path = seed_root / channel / name
            if not path.is_file():
                raise ContractError(f"{channel} migration asset is missing: {name}")
            if evidence.get("sha256") != expected["assets"][name]:
                raise ContractError(f"{channel}/{name} pinned SHA-256 differs")
            if evidence.get("sha256") != sha256(path):
                raise ContractError(f"{channel}/{name} bytes changed after verification")
            if evidence.get("bytes") != path.stat().st_size:
                raise ContractError(f"{channel}/{name} size differs")
            asset_id = evidence.get("githubAssetId")
            if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id < 1:
                raise ContractError(f"{channel}/{name} GitHub asset ID is invalid")
