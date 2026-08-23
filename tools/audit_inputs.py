#!/usr/bin/env python3
"""Materialize exact protected-audit inputs from immutable GitHub evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from tools.tumoflip.protected_app_audit import AuditError, load_firmware_updaters
except ModuleNotFoundError:  # Direct script execution from tools/.
    from tumoflip.protected_app_audit import AuditError, load_firmware_updaters


HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SOURCE_TAG = re.compile(r"^[0-9]{1,2}[a-z]{3}[0-9]{4}(?:p[0-9]+)?$")
STABLE_FIRMWARE_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
DEV_FIRMWARE_TAG = re.compile(r"^t-dev-[0-9]{3}-[0-9]{3}$")
RELEASE_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class InputError(RuntimeError):
    """Raised when a remote release differs from its exact audit contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"invalid JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise InputError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_value(*command: str) -> Any:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise InputError(f"command failed ({' '.join(command)}): {detail}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise InputError(f"invalid GitHub response for {command[-1]}") from error


def _run_json(*command: str) -> dict[str, Any]:
    value = _run_value(*command)
    if not isinstance(value, dict):
        raise InputError(f"GitHub response is not an object for {command[-1]}")
    return value


def _download_asset(repository: str, asset: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    asset_id = asset.get("id")
    if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id < 1:
        raise InputError("asset ID is invalid")
    with destination.open("wb") as stream:
        result = subprocess.run(
            (
                "gh",
                "api",
                "-H",
                "Accept: application/octet-stream",
                f"repos/{repository}/releases/assets/{asset_id}",
            ),
            check=False,
            stdout=stream,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise InputError(f"cannot download asset {asset_id}: {detail}")
    if destination.stat().st_size != asset.get("bytes"):
        raise InputError(f"asset size differs: {asset.get('name')}")
    if sha256(destination) != asset.get("sha256"):
        raise InputError(f"asset SHA-256 differs: {asset.get('name')}")


def _asset_map(release: dict[str, Any]) -> dict[int, dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets):
        raise InputError("GitHub release assets are invalid")
    result: dict[int, dict[str, Any]] = {}
    for item in assets:
        asset_id = item.get("id")
        if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id in result:
            raise InputError("GitHub asset IDs are invalid or duplicated")
        result[asset_id] = item
    return result


def _verify_asset_metadata(remote: dict[str, Any], expected: dict[str, Any]) -> None:
    if remote.get("name") != expected.get("name") or remote.get("size") != expected.get("bytes"):
        raise InputError(f"GitHub asset metadata differs: {expected.get('name')}")
    if remote.get("digest") != f"sha256:{expected.get('sha256')}":
        raise InputError(f"GitHub asset digest differs: {expected.get('name')}")


def _tag_commit(repository: str, tag: str) -> str:
    reference = _run_json("gh", "api", f"repos/{repository}/git/ref/tags/{tag}")
    item = reference.get("object")
    if not isinstance(item, dict):
        raise InputError(f"tag reference is invalid: {repository}/{tag}")
    commit = item.get("sha")
    if item.get("type") == "tag":
        annotated = _run_json("gh", "api", f"repos/{repository}/git/tags/{commit}")
        item = annotated.get("object")
        if not isinstance(item, dict) or item.get("type") != "commit":
            raise InputError(f"annotated tag target is invalid: {repository}/{tag}")
        commit = item.get("sha")
    elif item.get("type") != "commit":
        raise InputError(f"tag does not resolve to a commit: {repository}/{tag}")
    if not isinstance(commit, str) or HEX_40.fullmatch(commit) is None:
        raise InputError(f"tag commit is invalid: {repository}/{tag}")
    return commit


def _verify_release(expected: dict[str, Any]) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    repository = expected.get("repository")
    release_id = expected.get("githubReleaseId")
    tag = expected.get("releaseTag")
    if not isinstance(repository, str) or not repository or not isinstance(release_id, int):
        raise InputError("release contract identity is invalid")
    release = _run_json("gh", "api", f"repos/{repository}/releases/{release_id}")
    if (
        release.get("id") != release_id
        or release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not expected.get("prerelease")
    ):
        raise InputError(f"GitHub release identity differs: {repository}/{tag}")
    if _tag_commit(repository, tag) != expected.get("tagCommit"):
        raise InputError(f"release tag commit differs: {repository}/{tag}")
    remote_assets = _asset_map(release)
    for asset in expected.get("assets", {}).values():
        if not isinstance(asset, dict) or asset.get("id") not in remote_assets:
            raise InputError(f"required GitHub asset is missing: {repository}/{tag}")
        _verify_asset_metadata(remote_assets[asset["id"]], asset)
    if isinstance(expected.get("asset"), dict):
        asset = expected["asset"]
        if asset.get("id") not in remote_assets:
            raise InputError(f"required GitHub asset is missing: {repository}/{tag}")
        _verify_asset_metadata(remote_assets[asset["id"]], asset)
    return release, remote_assets


def _release_asset(target: dict[str, Any], release: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        item for item in release.get("assets", [])
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item["name"].startswith("flipper-z-f7-update-")
        and item["name"].endswith(".tgz")
    ]
    if len(candidates) != 1:
        raise InputError(
            f"rolling firmware release must have exactly one updater asset: "
            f"{target['repository']}/{target['releaseTag']}")
    asset = candidates[0]
    if (
        not isinstance(asset.get("id"), int)
        or isinstance(asset["id"], bool)
        or asset["id"] < 1
        or not isinstance(asset.get("size"), int)
        or isinstance(asset["size"], bool)
        or asset["size"] < 1
        or not isinstance(asset.get("digest"), str)
        or not asset["digest"].startswith("sha256:")
    ):
        raise InputError(
            f"rolling firmware updater metadata is invalid: "
            f"{target['repository']}/{target['releaseTag']}")
    digest = asset["digest"].removeprefix("sha256:")
    if HEX_64.fullmatch(digest) is None:
        raise InputError(
            f"rolling firmware updater digest is invalid: "
            f"{target['repository']}/{target['releaseTag']}")
    return {
        "id": asset["id"],
        "name": asset["name"],
        "bytes": asset["size"],
        "sha256": digest,
    }


def select_rolling_firmware_release(
    selector: dict[str, Any], releases: list[dict[str, Any]]
) -> dict[str, Any]:
    """Select one exact latest stable or dev updater release from trusted ownership.

    The caller records the selected numeric release ID, annotated tag commit, asset
    digest and decoded resources in immutable audit evidence. The selector has no
    user-controlled repository, channel or asset pattern surface.
    """
    repository = selector["repository"]
    channel = selector["channel"]
    prerelease = channel == "dev"
    tag_pattern = DEV_FIRMWARE_TAG if prerelease else STABLE_FIRMWARE_TAG
    candidates: list[dict[str, Any]] = []
    for release in releases:
        if not isinstance(release, dict):
            raise InputError("rolling firmware releases are invalid")
        tag = release.get("tag_name")
        if (
            release.get("draft") is not False
            or release.get("prerelease") is not prerelease
            or not isinstance(tag, str)
            or tag_pattern.fullmatch(tag) is None
        ):
            continue
        published_at = release.get("published_at")
        release_id = release.get("id")
        if (
            not isinstance(published_at, str)
            or RELEASE_TIMESTAMP.fullmatch(published_at) is None
            or not isinstance(release_id, int)
            or isinstance(release_id, bool)
            or release_id < 1
        ):
            raise InputError(f"rolling {channel} firmware release metadata is invalid: {tag}")
        candidate = {
            "repository": repository,
            "releaseTag": tag,
            "githubReleaseId": release_id,
            "prerelease": prerelease,
            "publishedAt": published_at,
        }
        candidate["release"] = release
        candidates.append(candidate)
    if not candidates:
        raise InputError(f"no eligible rolling {channel} firmware release")
    candidates.sort(key=lambda item: (item["publishedAt"], item["githubReleaseId"]))
    winner = candidates[-1]
    if len(candidates) > 1 and candidates[-2]["publishedAt"] == winner["publishedAt"]:
        raise InputError(f"rolling {channel} firmware release order is ambiguous")
    winner["asset"] = _release_asset(winner, winner.pop("release"))
    winner.pop("publishedAt")
    return winner


def resolve_rolling_firmware_targets(selectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for selector in selectors:
        pages = _run_value(
            "gh", "api", "--paginate", "--slurp",
            f"repos/{selector['repository']}/releases?per_page=100")
        if (
            not isinstance(pages, list)
            or not all(isinstance(page, list) for page in pages)
        ):
            raise InputError("rolling firmware release pages are invalid")
        target = select_rolling_firmware_release(
            selector, [release for page in pages for release in page])
        target["tagCommit"] = _tag_commit(target["repository"], target["releaseTag"])
        result.append(target)
    return result


def _materialize_firmware_target(target: dict[str, Any], output: Path) -> dict[str, Any]:
    """Download one immutable updater and derive its resource evidence.

    The release, tag and asset were resolved before this function runs.  The
    updater parser provides the second, independent check: it validates the
    updater's archive layout and hashes every resource before its FAP MD5s can
    enter a protected-app ledger.
    """
    _verify_release(target)
    directory = output / "firmware" / target["releaseTag"]
    archive = directory / target["asset"]["name"]
    _download_asset(target["repository"], target["asset"], archive)
    descriptor = {
        "schema": 1,
        "releaseTag": target["releaseTag"],
        "releaseCommit": target["tagCommit"],
        "assetFileName": target["asset"]["name"],
        "assetSHA256": target["asset"]["sha256"],
    }
    descriptor_path = directory / "firmware-updater.json"
    write_json(descriptor_path, descriptor)
    try:
        updaters = load_firmware_updaters([descriptor_path])
    except AuditError as error:
        raise InputError(f"firmware updater is invalid: {target['releaseTag']}: {error}") from error
    if len(updaters) != 1:
        raise InputError(f"firmware updater count is invalid: {target['releaseTag']}")
    updater = updaters[0]
    if (
        updater["releaseTag"] != target["releaseTag"]
        or updater["sourceCommit"] != target["tagCommit"]
        or updater["containerSHA256"] != target["asset"]["sha256"]
    ):
        raise InputError(f"firmware updater provenance differs: {target['releaseTag']}")
    for key, actual in (
        ("resourceManifestSHA256", updater["manifestSHA256"]),
        ("resourcesSHA256", updater["resourcesSHA256"]),
    ):
        expected = target.get(key)
        if expected is not None and expected != actual:
            raise InputError(f"firmware resource evidence differs: {target['releaseTag']}/{key}")
    return {
        "repository": target["repository"],
        "releaseTag": target["releaseTag"],
        "githubReleaseId": target["githubReleaseId"],
        "tagCommit": target["tagCommit"],
        "updaterSHA256": target["asset"]["sha256"],
        "resourceManifestSHA256": updater["manifestSHA256"],
        "resourcesSHA256": updater["resourcesSHA256"],
    }


def validate_targets(contract: dict[str, Any]) -> dict[str, Any]:
    if contract.get("schema") != 1 or contract.get("kind") != "protectedAuditTargets":
        raise InputError("protected audit target contract is invalid")
    implementation = contract.get("implementation")
    if not isinstance(implementation, dict) or implementation.get("repository") != "squazaryu/tumoflip":
        raise InputError("implementation contract is invalid")
    if not isinstance(implementation.get("commit"), str) or HEX_40.fullmatch(implementation["commit"]) is None:
        raise InputError("implementation commit is invalid")
    packages = contract.get("packages")
    firmware = contract.get("firmware")
    rolling_firmware = contract.get("rollingFirmware")
    if not isinstance(packages, list) or len(packages) < 2:
        raise InputError("at least stable and dev package targets are required")
    if not isinstance(firmware, list) or len(firmware) < 2:
        raise InputError("stable and dev firmware targets are required")
    if not isinstance(rolling_firmware, list) or len(rolling_firmware) != 2:
        raise InputError("exactly stable and dev rolling firmware selectors are required")
    identities: set[tuple[str, int]] = set()
    for item in [*packages, *firmware]:
        if not isinstance(item, dict):
            raise InputError("target entry is invalid")
        identity = (item.get("repository"), item.get("githubReleaseId"))
        if not isinstance(identity[0], str) or not isinstance(identity[1], int) or identity in identities:
            raise InputError("target release identity is invalid or duplicated")
        identities.add(identity)
        if not isinstance(item.get("tagCommit"), str) or HEX_40.fullmatch(item["tagCommit"]) is None:
            raise InputError("target tag commit is invalid")
    channels: set[str] = set()
    for selector in rolling_firmware:
        if (
            not isinstance(selector, dict)
            or set(selector) != {"repository", "channel"}
            or selector.get("repository") != "squazaryu/tumoflip"
            or selector.get("channel") not in {"stable", "dev"}
            or selector["channel"] in channels
        ):
            raise InputError("rolling firmware selector is invalid or duplicated")
        channels.add(selector["channel"])
    if channels != {"stable", "dev"}:
        raise InputError("stable and dev rolling firmware selectors are required")
    return contract


def validate_source_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    if fixture.get("schema") != 1 or fixture.get("kind") != "protectedAuditSourceFixture":
        raise InputError("source fixture contract is invalid")
    current = fixture.get("current")
    previous = fixture.get("previous")
    issue = fixture.get("canonicalIssue")
    if not all(isinstance(item, dict) for item in (current, previous, issue)):
        raise InputError("source fixture identity is incomplete")
    for item in (current, previous):
        if item.get("repository") != "xMasterX/all-the-plugins":
            raise InputError("source fixture repository is invalid")
        if not isinstance(item.get("releaseTag"), str) or SOURCE_TAG.fullmatch(item["releaseTag"]) is None:
            raise InputError("source fixture tag is invalid")
        if not isinstance(item.get("tagCommit"), str) or HEX_40.fullmatch(item["tagCommit"]) is None:
            raise InputError("source fixture commit is invalid")
    if not isinstance(fixture.get("sequence"), int) or fixture["sequence"] < 1:
        raise InputError("source fixture sequence is invalid")
    return fixture


def _verify_package_manifest(path: Path, target: dict[str, Any]) -> dict[str, Any]:
    manifest = load_object(path)
    package_release = manifest.get("package_release")
    if not isinstance(package_release, dict):
        raise InputError(f"package_release is missing: {target['releaseTag']}")
    if manifest.get("release_id") != target.get("manifestReleaseId"):
        raise InputError(f"manifest release ID differs: {target['releaseTag']}")
    if (
        package_release.get("catalog_release_tag") != target["releaseTag"]
        or package_release.get("source_commit") != target.get("manifestSourceCommit")
    ):
        raise InputError(f"manifest source identity differs: {target['releaseTag']}")
    return package_release


def _verify_migration(path: Path, target: dict[str, Any]) -> None:
    migration = load_object(path)
    if (
        migration.get("schema") != 1
        or migration.get("kind") != "legacyByteMirror"
        or migration.get("publisher") != {
            "repository": target["repository"],
            "commit": target["tagCommit"],
        }
        or migration.get("manifestReleaseId") != target["manifestReleaseId"]
        or migration.get("firmwareSourceCommit") != target["manifestSourceCommit"]
    ):
        raise InputError(f"migration provenance differs: {target['releaseTag']}")
    legacy = migration.get("legacy")
    if not isinstance(legacy, dict) or legacy.get("tag") != target["releaseTag"]:
        raise InputError(f"legacy migration identity differs: {target['releaseTag']}")
    for name, item in migration.get("assets", {}).items():
        if not isinstance(item, dict):
            raise InputError(f"migration asset evidence is invalid: {target['releaseTag']}")
        expected = next(
            (
                value
                for key, value in target["assets"].items()
                if key != "migrationProvenance" and value["name"] == name
            ),
            None,
        )
        if expected is None or item.get("sha256") != expected["sha256"] or item.get("bytes") != expected["bytes"]:
            raise InputError(f"migration asset evidence differs: {target['releaseTag']}/{name}")


def materialize(
    *,
    targets_path: Path,
    fixture_path: Path,
    output: Path,
    control_repository: str,
    control_commit: str,
) -> None:
    targets = validate_targets(load_object(targets_path))
    fixture = validate_source_fixture(load_object(fixture_path))
    if output.exists() and any(output.iterdir()):
        raise InputError("materialization output must be empty")
    if not control_repository or HEX_40.fullmatch(control_commit) is None:
        raise InputError("control identity is invalid")
    output.mkdir(parents=True, exist_ok=True)

    packages_evidence: list[dict[str, Any]] = []
    for target in targets["packages"]:
        _verify_release(target)
        directory = output / "packages" / target["releaseTag"]
        downloaded: dict[str, Path] = {}
        for key, asset in target["assets"].items():
            destination = directory / asset["name"]
            _download_asset(target["repository"], asset, destination)
            downloaded[key] = destination
        package_release = _verify_package_manifest(downloaded["manifest"], target)
        if "migrationProvenance" in downloaded:
            _verify_migration(downloaded["migrationProvenance"], target)
        channel = "stable" if "-stable-" in target["releaseTag"] else "dev"
        scanner_manifest = output / "targets" / f"{channel}:{target['releaseTag']}=manifest.json"
        scanner_archive = output / "targets" / f"{channel}:{target['releaseTag']}=targets.zip"
        scanner_manifest.parent.mkdir(parents=True, exist_ok=True)
        scanner_manifest.write_bytes(downloaded["manifest"].read_bytes())
        scanner_archive.write_bytes(downloaded["archive"].read_bytes())
        packages_evidence.append(
            {
                "repository": target["repository"],
                "releaseTag": target["releaseTag"],
                "githubReleaseId": target["githubReleaseId"],
                "tagCommit": target["tagCommit"],
                "sourceCommit": target["manifestSourceCommit"],
                "manifestReleaseId": target["manifestReleaseId"],
                "manifestSHA256": target["assets"]["manifest"]["sha256"],
                "archiveSHA256": target["assets"]["archive"]["sha256"],
                "packageRelease": package_release,
            }
        )

    firmware_targets = list(targets["firmware"])
    fixed_identities = {
        (target["repository"], target["githubReleaseId"]) for target in firmware_targets
    }
    for target in resolve_rolling_firmware_targets(targets["rollingFirmware"]):
        identity = (target["repository"], target["githubReleaseId"])
        if identity not in fixed_identities:
            firmware_targets.append(target)
            fixed_identities.add(identity)

    firmware_evidence = [
        _materialize_firmware_target(target, output) for target in firmware_targets
    ]

    current = fixture["current"]
    release, _ = _verify_release(
        {
            **current,
            "prerelease": False,
        }
    )
    community_dir = output / "community"
    for pack, asset in current["assets"].items():
        _download_asset(current["repository"], asset, community_dir / asset["name"])
    previous = fixture["previous"]
    if _tag_commit(previous["repository"], previous["releaseTag"]) != previous["tagCommit"]:
        raise InputError("previous Community Pack tag commit differs")
    if release.get("published_at") != current["publishedAt"]:
        raise InputError("Community Pack publication time differs")
    evidence = {
        "schema": 1,
        "kind": "protectedAppAuditEvidence",
        "control": {"repository": control_repository, "commit": control_commit},
        "implementation": targets["implementation"],
        "community": {
            "repository": current["repository"],
            "tag": current["releaseTag"],
            "commit": current["tagCommit"],
            "previousTag": previous["releaseTag"],
            "previousCommit": previous["tagCommit"],
            "githubReleaseId": current["githubReleaseId"],
            "publishedAt": current["publishedAt"],
            "api": current["api"],
            "archives": {
                pack: {"fileName": asset["name"], "sha256": asset["sha256"]}
                for pack, asset in current["assets"].items()
            },
        },
        "issue": fixture["canonicalIssue"],
        "packages": packages_evidence,
        "firmware": firmware_evidence,
    }
    write_json(output / "exact-evidence.json", evidence)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--source-fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--control-repository", required=True)
    parser.add_argument("--control-commit", required=True)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        materialize(
            targets_path=args.targets,
            fixture_path=args.source_fixture,
            output=args.output,
            control_repository=args.control_repository,
            control_commit=args.control_commit,
        )
    except (InputError, OSError, ValueError) as error:
        print(f"protected audit input validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
