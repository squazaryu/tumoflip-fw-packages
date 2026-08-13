#!/usr/bin/env python3
"""Resolve the latest exact Community Pack source without executing its contents."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from .audit_inputs import HEX_64, InputError, SOURCE_TAG, _tag_commit, write_json
except ImportError:  # Direct script execution.
    from audit_inputs import HEX_64, InputError, SOURCE_TAG, _tag_commit, write_json


REPOSITORY = "xMasterX/all-the-plugins"
REQUIRED_ASSETS = {"base": "all-the-apps-base.zip", "extra": "all-the-apps-extra.zip"}


def _releases() -> list[dict[str, Any]]:
    result = subprocess.run(
        ("gh", "api", f"repos/{REPOSITORY}/releases?per_page=100", "--paginate", "--slurp"),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise InputError(result.stderr.strip() or "cannot resolve Community Pack releases")
    try:
        pages = json.loads(result.stdout)
    except ValueError as error:
        raise InputError("invalid Community Pack release response") from error
    if not isinstance(pages, list):
        raise InputError("Community Pack release response is not an array")
    values: list[Any] = []
    if all(isinstance(item, dict) for item in pages):
        values = pages
    else:
        for page in pages:
            if not isinstance(page, list):
                raise InputError("Community Pack paginated response is invalid")
            values.extend(page)
    candidates = [
        item
        for item in values
        if isinstance(item, dict)
        and item.get("draft") is False
        and item.get("prerelease") is False
        and isinstance(item.get("tag_name"), str)
        and SOURCE_TAG.fullmatch(item["tag_name"]) is not None
        and isinstance(item.get("published_at"), str)
    ]
    candidates.sort(key=lambda item: item["published_at"], reverse=True)
    return candidates


def _asset_contract(release: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [
        item
        for item in release.get("assets", [])
        if isinstance(item, dict) and item.get("name") == name
    ]
    if len(matches) != 1:
        raise InputError(f"Community Pack release requires exactly one {name}")
    asset = matches[0]
    digest = asset.get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or HEX_64.fullmatch(digest[7:]) is None:
        raise InputError(f"Community Pack asset digest is invalid: {name}")
    asset_id = asset.get("id")
    size = asset.get("size")
    if not isinstance(asset_id, int) or not isinstance(size, int) or size < 1:
        raise InputError(f"Community Pack asset metadata is invalid: {name}")
    return {"id": asset_id, "name": name, "bytes": size, "sha256": digest[7:]}


def _api(body: Any) -> str:
    if not isinstance(body, str):
        raise InputError("Community Pack release body is missing")
    match = re.search(r"API version:\s*([0-9]+\.[0-9]+)", body)
    if match is None:
        raise InputError("Community Pack API version is missing")
    return match.group(1)


def _release_by_tag(releases: list[dict[str, Any]], tag: str) -> dict[str, Any]:
    matches = [item for item in releases if item.get("tag_name") == tag]
    if len(matches) != 1:
        raise InputError(f"Community Pack release tag is missing or duplicated: {tag}")
    return matches[0]


def resolve(*, current_tag: str | None, previous_tag: str | None) -> dict[str, Any]:
    releases = _releases()
    if current_tag is None:
        if len(releases) < 2:
            raise InputError("two Community Pack releases are required")
        current, previous = releases[:2]
    else:
        if SOURCE_TAG.fullmatch(current_tag) is None or previous_tag is None or SOURCE_TAG.fullmatch(previous_tag) is None:
            raise InputError("explicit Community Pack tags are invalid")
        current = _release_by_tag(releases, current_tag)
        previous = _release_by_tag(releases, previous_tag)
    published = current["published_at"]
    try:
        sequence = int(
            datetime.fromisoformat(published.replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .strftime("%Y%m%d%H%M%S")
        )
    except (TypeError, ValueError) as error:
        raise InputError("Community Pack publication time is invalid") from error
    current_tag_value = current["tag_name"]
    previous_tag_value = previous["tag_name"]
    return {
        "schema": 1,
        "kind": "protectedAuditResolvedSource",
        "current": {
            "repository": REPOSITORY,
            "releaseTag": current_tag_value,
            "githubReleaseId": current["id"],
            "tagCommit": _tag_commit(REPOSITORY, current_tag_value),
            "publishedAt": published,
            "api": _api(current.get("body")),
            "assets": {
                pack: _asset_contract(current, name)
                for pack, name in REQUIRED_ASSETS.items()
            },
        },
        "previous": {
            "repository": REPOSITORY,
            "releaseTag": previous_tag_value,
            "githubReleaseId": previous["id"],
            "tagCommit": _tag_commit(REPOSITORY, previous_tag_value),
        },
        "sequence": sequence,
    }


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-tag")
    parser.add_argument("--previous-tag")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        value = resolve(current_tag=args.current_tag, previous_tag=args.previous_tag)
        write_json(args.output, value)
    except (InputError, OSError, ValueError) as error:
        print(f"Community Pack resolution failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
