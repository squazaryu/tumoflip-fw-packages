#!/usr/bin/env python3
"""Create a deterministic Community Pack -> FW Packages reconciliation report.

This is deliberately a control-plane operation. It does not publish bytes and it
does not mutate a Flipper. It combines the exact protected-source parity result
with the immutable audit ledger so a package release can be generated only from
one reviewed Community Pack identity. App IDs, aliases and canonical targets are
the comparison keys; a category/path move alone is not a new application.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


class ReconciliationError(RuntimeError):
    pass


HEX_40 = re.compile(r"^[0-9a-f]{40}$")


def read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReconciliationError(f"invalid JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise ReconciliationError(f"JSON root must be an object: {path}")
    return value


def validate_parity(report: dict[str, Any]) -> None:
    if report.get("schema") != 1:
        raise ReconciliationError("protected parity report schema must be 1")
    implementation = report.get("implementation")
    if not isinstance(implementation, dict) or not HEX_40.fullmatch(str(implementation.get("commit", ""))):
        raise ReconciliationError("parity implementation commit is invalid")
    if report.get("overallStatus") not in {"verified", "needsReview"}:
        raise ReconciliationError("parity overall status is invalid")
    apps = report.get("apps")
    if not isinstance(apps, list):
        raise ReconciliationError("parity apps must be an array")
    ids: set[str] = set()
    for item in apps:
        if not isinstance(item, dict) or not isinstance(item.get("appId"), str):
            raise ReconciliationError("parity app identity is invalid")
        if item["appId"] in ids:
            raise ReconciliationError(f"duplicate parity app id: {item['appId']}")
        ids.add(item["appId"])


def validate_ledger(
    ledger: dict[str, Any],
    *,
    expected_source_tag: str | None = None,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    if ledger.get("schema") != 2 or ledger.get("sourceRepository") != "xMasterX/all-the-plugins":
        raise ReconciliationError("protected audit ledger identity is invalid")
    audits = ledger.get("audits")
    if not isinstance(audits, list) or not audits:
        raise ReconciliationError("protected audit ledger has no audits")
    latest = audits[-1]
    if not isinstance(latest, dict) or latest.get("overallStatus") not in {"pending", "verified"}:
        raise ReconciliationError("latest protected audit status is invalid")
    if not HEX_40.fullmatch(str(latest.get("sourceCommit", ""))):
        raise ReconciliationError("latest protected audit commit is invalid")
    if expected_source_tag is not None and latest.get("sourceTag") != expected_source_tag:
        raise ReconciliationError(
            "latest protected audit tag does not match the current Community Pack release"
        )
    if expected_source_commit is not None and latest.get("sourceCommit") != expected_source_commit:
        raise ReconciliationError(
            "latest protected audit commit does not match the current Community Pack release"
        )
    return latest


def reconcile(
    parity: dict[str, Any],
    ledger: dict[str, Any],
    generated_at: str | None = None,
    *,
    expected_source_tag: str | None = None,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    validate_parity(parity)
    latest = validate_ledger(
        ledger,
        expected_source_tag=expected_source_tag,
        expected_source_commit=expected_source_commit,
    )
    reasons: list[str] = []
    if parity["overallStatus"] != "verified":
        reasons.append("protected source parity requires review")
    if latest["overallStatus"] != "verified":
        reasons.append("latest protected-app audit is not verified")
    return {
        "schema": 1,
        "generatedAt": generated_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "decision": "needsReview" if reasons else "readyForCatalogPR",
        "comparison": {
            "identityFields": ["app_id", "sha256", "canonical_target", "aliases"],
            "pathChangesAreRenames": True,
            "manualDeviceVerification": False,
        },
        "community": {
            "repository": ledger["sourceRepository"],
            "releaseTag": latest["sourceTag"],
            "commit": latest["sourceCommit"],
            "auditStatus": latest["overallStatus"],
        },
        "implementation": parity["implementation"],
        "protectedSourceParity": parity["overallStatus"],
        "reviewReasons": reasons,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Community Pack reconciliation",
        "",
        f"- decision: **{report['decision']}**",
        f"- Community Pack: `{report['community']['releaseTag']}` at `{report['community']['commit']}`",
        f"- protected source parity: `{report['protectedSourceParity']}`",
        f"- audit: `{report['community']['auditStatus']}`",
        "",
        "The comparison key is app ID + content hash + canonical target + aliases. A route/category move is treated as a rename and does not create a duplicate package.",
    ]
    if report["reviewReasons"]:
        lines.extend(["", "## Review required", "", *[f"- {reason}" for reason in report["reviewReasons"]]])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parity", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--expected-source-tag")
    parser.add_argument("--expected-source-commit")
    args = parser.parse_args(argv)
    try:
        report = reconcile(
            read(args.parity),
            read(args.ledger),
            expected_source_tag=args.expected_source_tag,
            expected_source_commit=args.expected_source_commit,
        )
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        args.markdown.write_text(markdown(report), encoding="utf-8")
        print(json.dumps({"decision": report["decision"]}))
        return 0 if report["decision"] == "readyForCatalogPR" else 1
    except (ReconciliationError, OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
