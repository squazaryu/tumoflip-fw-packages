#!/usr/bin/env python3
"""Build and validate the immutable FW Packages catalog index.

The index is intentionally independent from firmware release labels. A catalog
revision is selected by channel, target and API major; firmware versions are not
part of the identity. Every published revision remains in the index so a client
can install a compatible older revision without deleting or replacing a GitHub
release.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .catalog_contract import ContractError, HEX_64, load_json
except ImportError:
    from catalog_contract import ContractError, HEX_64, load_json


REPOSITORY = "squazaryu/tumoflip-fw-packages"
TAG = re.compile(r"^fw-packages-(stable|dev)-([0-9]{3})$")
STATES = {"active", "legacy", "withdrawn"}


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value


def _digest(value: Any, label: str) -> str:
    value = _string(value, label)
    if HEX_64.fullmatch(value) is None:
        raise ContractError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError(f"{label} must be a positive integer")
    return value


def validate_index(index: dict[str, Any]) -> dict[str, Any]:
    if index.get("schema") != 1:
        raise ContractError("catalog index schema must be 1")
    if index.get("repository") != REPOSITORY:
        raise ContractError("catalog index repository differs from publisher")
    _string(index.get("generated_at"), "generated_at")
    policy = index.get("selection_policy")
    expected_policy = {
        "auto": "highest compatible active revision",
        "manual": "any compatible active or legacy revision",
        "withdrawal": "immutable release retained; index state becomes withdrawn",
    }
    if policy != expected_policy:
        raise ContractError("catalog selection policy is not fail-closed")
    channels = index.get("channels")
    if not isinstance(channels, dict) or set(channels) != {"stable", "dev"}:
        raise ContractError("catalog index must contain stable and dev channels")
    for channel, document in channels.items():
        if not isinstance(document, dict):
            raise ContractError(f"{channel} channel must be an object")
        current = _positive_int(document.get("current_revision"), f"{channel}.current_revision")
        releases = document.get("releases")
        if not isinstance(releases, list) or not releases:
            raise ContractError(f"{channel}.releases must be non-empty")
        revisions: set[int] = set()
        active_revisions: set[int] = set()
        for number, release in enumerate(releases):
            label = f"{channel}.releases[{number}]"
            if not isinstance(release, dict):
                raise ContractError(f"{label} must be an object")
            revision = _positive_int(release.get("revision"), f"{label}.revision")
            if revision in revisions:
                raise ContractError(f"duplicate {channel} revision: {revision}")
            revisions.add(revision)
            tag = _string(release.get("tag"), f"{label}.tag")
            match = TAG.fullmatch(tag)
            if match is None or match.group(1) != channel or int(match.group(2)) != revision:
                raise ContractError(f"{label}.tag does not match channel/revision")
            _string(release.get("repository"), f"{label}.repository")
            _digest(release.get("release_id"), f"{label}.release_id")
            _digest(release.get("manifest_sha256"), f"{label}.manifest_sha256")
            _digest(release.get("archive_sha256"), f"{label}.archive_sha256")
            state = release.get("state")
            if state not in STATES:
                raise ContractError(f"{label}.state is invalid")
            compatibility = release.get("compatibility")
            if not isinstance(compatibility, dict):
                raise ContractError(f"{label}.compatibility must be an object")
            targets = compatibility.get("targets")
            api_majors = compatibility.get("api_majors")
            if not isinstance(targets, list) or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in targets
            ):
                raise ContractError(f"{label}.compatibility.targets is invalid")
            if not isinstance(api_majors, list) or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in api_majors
            ):
                raise ContractError(f"{label}.compatibility.api_majors is invalid")
            if state != "withdrawn":
                active_revisions.add(revision)
        if current not in active_revisions:
            raise ContractError(f"{channel}.current_revision must be active")
    return index


def _release_from_current(channel: str, data: dict[str, Any]) -> dict[str, Any]:
    tag = _string(data.get("tag"), f"current {channel}.tag")
    revision = _positive_int(data.get("revision"), f"current {channel}.revision")
    assets = data.get("assets")
    if not isinstance(assets, dict):
        raise ContractError(f"current {channel}.assets is invalid")
    manifest_sha = _digest(assets.get("tumoflip-packages.json"), f"current {channel}.manifest")
    archive_sha = _digest(assets.get("tumoflip-packages.zip"), f"current {channel}.archive")
    api = str(data.get("api", "88.0"))
    try:
        api_major = int(api.split(".", 1)[0])
    except (ValueError, IndexError) as error:
        raise ContractError(f"current {channel}.api is invalid") from error
    return {
        "revision": revision,
        "tag": tag,
        "repository": REPOSITORY,
        "release_id": _digest(data.get("releaseId"), f"current {channel}.releaseId"),
        "manifest_sha256": manifest_sha,
        "archive_sha256": archive_sha,
        "state": "active",
        "compatibility": {"targets": [int(data.get("target", 7))], "api_majors": [api_major]},
    }


def build_from_current(
    path: Path,
    generated_at: str | None = None,
    existing: Path | None = None,
) -> dict[str, Any]:
    current = load_json(path)
    previous: dict[str, Any] | None = None
    if existing is not None and existing.is_file():
        previous = validate_index(load_json(existing))
    channels: dict[str, Any] = {}
    for channel in ("stable", "dev"):
        item = current.get("channels", {}).get(channel)
        if not isinstance(item, dict):
            raise ContractError(f"current release contract lacks {channel}")
        release = _release_from_current(channel, item)
        releases = []
        if previous is not None:
            releases.extend(previous["channels"][channel]["releases"])
        releases = [
            item for item in releases
            if not (
                item.get("repository") == REPOSITORY
                and item.get("revision") == release["revision"]
            )
        ]
        releases.append(release)
        releases.sort(key=lambda item: item["revision"])
        channels[channel] = {
            "current_revision": release["revision"],
            "releases": releases,
        }
    if previous is not None and generated_at is None:
        previous_channels = previous.get("channels")
        generated_at = previous["generated_at"] if previous_channels == channels else None
    result = {
        "schema": 1,
        "repository": REPOSITORY,
        "generated_at": generated_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "selection_policy": {
            "auto": "highest compatible active revision",
            "manual": "any compatible active or legacy revision",
            "withdrawal": "immutable release retained; index state becomes withdrawn",
        },
        "channels": channels,
    }
    return validate_index(result)


def write_index(path: Path, document: dict[str, Any]) -> None:
    """Write an index without creating formatting-only automation churn.

    Existing indexes may have been produced by an older serializer. When the
    validated JSON document is semantically unchanged, preserve those bytes so
    the scheduled reconciler does not open a PR merely to reformat history.
    New or changed indexes use the canonical two-space representation.
    """

    encoded = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if existing == document:
            return
    path.write_text(encoded, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "build"))
    parser.add_argument("--index", type=Path, default=Path("catalog-index.json"))
    parser.add_argument("--current", type=Path, default=Path("contracts/current-releases.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--existing", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            validate_index(load_json(args.index))
            print(f"verified: {args.index}")
        else:
            output = args.output or args.index
            document = build_from_current(args.current, existing=args.existing)
            write_index(output, document)
            print(f"generated: {output}")
    except (ContractError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
