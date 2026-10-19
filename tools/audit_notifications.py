#!/usr/bin/env python3
"""Reconcile review-only monitors; never change an audit result or authorization."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


REPOSITORY = "squazaryu/tumoflip-fw-packages"
BOT = "github-actions[bot]"
CONFIGS = {
    "abi": ("Review Community Pack ABI compatibility", "community-pack-abi-audit"),
    "source": ("Watch ARF and ProtoPirate source changes", "source-matrix-watch"),
    "drift": ("Review Tumoflip implementation drift", "tumoflip-implementation-drift"),
    "parity": ("Review protected app source imports", "protected-source-parity"),
    "history": ("Review protected Community Pack source history", "protected-source-history"),
    "esp": ("", "esp-installer-audit"),
}
RECEIPT = re.compile(r"\n<!-- audit-notification:v1 ([0-9a-f]{64}) ([0-9a-f]{64}) -->\n?\Z")


class NotificationError(Exception):
    pass


def fingerprint(report: dict[str, Any]) -> str:
    # Ignore only the report-generation clock. Evidence timestamps, source refs,
    # asset identities, findings and policy decisions remain significant.
    content = {key: value for key, value in report.items() if key != "generatedAt"}
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def report_status(report: dict[str, Any]) -> str:
    values = [report[key] for key in ("status", "overallStatus") if key in report]
    if not values or any(value != values[0] for value in values):
        raise NotificationError("Audit status is missing or conflicting")
    if values[0] not in {"verified", "baselineStale", "needsReview", "rejected"}:
        raise NotificationError("Audit status is invalid")
    return values[0]


def has_error(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("error")) or value.get("status") in {"unavailable", "rejected"} or any(
            has_error(child) for child in value.values()
        )
    return isinstance(value, list) and any(has_error(child) for child in value)


def must_fail(report: dict[str, Any], *, unchanged: bool) -> bool:
    status = report_status(report)
    if has_error(report):
        return True
    return status == "needsReview" and not unchanged


def with_receipt(body: str, report: dict[str, Any]) -> str:
    body = body.rstrip() + "\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    return f"{body}\n<!-- audit-notification:v1 {fingerprint(report)} {digest} -->\n"


def bounded_body(mode: str, body: str) -> str:
    if len(body) <= 60000:
        return body
    workflows = {"abi": "community-abi-audit", "source": "source-matrix-watcher",
                 "drift": "implementation-drift", "parity": "protected-source-parity",
                 "history": "protected-source-history", "esp": "esp-installer-audit"}
    # Full reports are uploaded before reconciliation, including on quiet runs.
    # The workflow link also reaches fresh reports after an older artifact expires.
    link = f"https://github.com/{REPOSITORY}/actions/workflows/{workflows[mode]}.yml"
    excerpt = "\n".join(body.splitlines()[:160])[:40000]
    return (f"<!-- {CONFIGS[mode][1]} -->\n# Audit review report (excerpt)\n\n"
            f"Full JSON/Markdown exceeds the GitHub issue limit. See [full audit reports]({link}).\n"
            "Findings remain unresolved; this excerpt does not authorize publication.\n\n"
            f"{excerpt}\n\nExcerpt ends; complete evidence remains in the workflow artifacts.\n")


def same_report(body: str, report: dict[str, Any]) -> bool:
    match = RECEIPT.search(body)
    return bool(match and match[1] == fingerprint(report)
                and match[2] == hashlib.sha256(body[:match.start()].encode()).hexdigest())


def marked(body: Any, marker: str) -> bool:
    if not isinstance(body, str):
        return False
    prefix = f"<!-- {marker} -->"
    return body.startswith(prefix) or (
        marker == "protected-source-history"
        and body.startswith("# Protected Community Pack source history\n\n" + prefix + "\n")
    )


def matches(issue: dict[str, Any], config: tuple[str, str]) -> bool:
    title, marker = config
    return ("pull_request" not in issue and issue.get("user", {}).get("login") == BOT
            and issue.get("title") == title and marked(issue.get("body"), marker))


def positive_id(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise NotificationError("GitHub object identity is invalid")
    return value


def validate_report(mode: str, report: dict[str, Any], scan_exit: int) -> None:
    if not isinstance(report, dict) or type(report.get("schema")) is not int:
        raise NotificationError("Audit report schema is invalid")
    status = report_status(report)
    allowed = {"verified", "needsReview"}
    allowed |= {"baselineStale"} if mode == "drift" else {"rejected"} if mode == "esp" else set()
    if report["schema"] not in ({1, 2} if mode == "source" else {1}) or status not in allowed:
        raise NotificationError("Audit schema/status differs from monitor")
    if scan_exit not in {0, 1, 2} or (scan_exit == 0) != (status in {"verified", "baselineStale"}):
        raise NotificationError("Scanner exit status differs from audit result")
    if scan_exit == 2 and (mode != "esp" or status != "rejected"):
        raise NotificationError("Unexpected scanner error status")
    kinds = {"abi": "communityPackAbiAudit", "source": "sourceWatchReport",
             "drift": "protectedSurfaceReport", "esp": "espInstallerAuditReport"}
    if mode in kinds and report.get("kind") != kinds[mode]:
        raise NotificationError("Audit report kind differs from monitor")
    if mode in {"parity", "history"} and not isinstance(report.get("apps"), list):
        raise NotificationError("Source report apps are missing")
    if mode == "esp" and report.get("automaticFlashPackageAuthorization") is not (status == "verified"):
        raise NotificationError("ESP authorization differs from audit result")


def github(path: str, *, payload: dict[str, Any] | None = None, method: str = "GET", pages: bool = False):
    args = ["gh", "api", f"repos/{REPOSITORY}/{path}", "--method", method]
    if pages:
        args += ["--paginate", "--slurp"]
    if payload is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=120, check=False)
    if result.returncode:
        raise NotificationError(f"GitHub {method} {path} failed: {result.stderr.strip()}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise NotificationError("GitHub response is not JSON") from error
    if pages:
        if not isinstance(value, list) or not all(isinstance(page, list) and all(isinstance(item, dict) for item in page) for page in value):
            raise NotificationError("GitHub pagination shape is invalid")
        return [item for page in value for item in page]
    if not isinstance(value, dict):
        raise NotificationError("GitHub object response is invalid")
    return value


def reconcile(mode: str, report: dict[str, Any], body: str, *, scan_exit: int,
              call=github, dry_run: bool = False, tracking_issue: int | None = None) -> dict[str, Any]:
    validate_report(mode, report, scan_exit)
    title, marker = CONFIGS[mode]
    if not marked(body, marker):
        raise NotificationError("Rendered report marker differs from monitor")
    status = report_status(report)
    review = status in {"needsReview", "rejected"}
    if mode == "esp":
        number = positive_id(tracking_issue)
        issue = call(f"issues/{number}")
        if issue.get("number") != number or "pull_request" in issue:
            raise NotificationError("ESP tracking issue identity differs")
        comments = call(f"issues/{number}/comments?per_page=100", pages=True)
        marked_comments = [item for item in comments if item.get("user", {}).get("login") == BOT
                           and marked(item.get("body"), marker)]
        if len(marked_comments) > 1:
            raise NotificationError("Multiple canonical ESP comments; manual reconciliation required")
        previous = marked_comments[0] if marked_comments else None
    else:
        issues = call("issues?state=all&per_page=100", pages=True)
        candidates = [item for item in issues if matches(item, CONFIGS[mode])]
        # Reuse one existing tracker, including the legacy history heading.
        # Do not delete/close other historical issues or accept their findings.
        candidates.sort(key=lambda item: (item.get("state") != "open", positive_id(item.get("number"))))
        issue = candidates[0] if candidates else None
        if issue:
            number = positive_id(issue.get("number"))
            issue = call(f"issues/{number}")
            if not matches(issue, CONFIGS[mode]) or issue.get("number") != number:
                raise NotificationError("Canonical issue identity changed")
        previous = issue
    if issue and issue.get("state") not in {"open", "closed"}:
        raise NotificationError("Canonical issue state is invalid")
    unchanged = bool(previous and issue and issue["state"] == ("open" if review else "closed")
                     and same_report(previous.get("body", ""), report))
    desired = with_receipt(bounded_body(mode, body), report)
    if len(desired) > 65000:
        raise NotificationError("Report exceeds safe GitHub body size")
    if not unchanged and not dry_run and (review or issue):
        if mode == "esp":
            if previous:
                identifier = positive_id(previous.get("id"))
                updated = call(f"issues/comments/{identifier}", method="PATCH", payload={"body": desired})
                if updated.get("id") != identifier:
                    raise NotificationError("ESP comment identity changed")
            else:
                updated = call(f"issues/{number}/comments", method="POST", payload={"body": desired})
            if updated.get("user", {}).get("login") != BOT or updated.get("body") != desired:
                raise NotificationError("ESP comment write verification failed")
        else:
            payload = {"body": desired, "state": "open" if review else "closed"}
            if issue:
                updated = call(f"issues/{number}", method="PATCH", payload=payload)
            else:
                updated = call("issues", method="POST", payload={"title": title, "body": desired})
                number = positive_id(updated.get("number"))
            if not matches(updated, CONFIGS[mode]) or updated.get("number") != number or updated.get("body") != desired:
                raise NotificationError("Issue write verification failed")
            issue = updated
        desired_state = "open" if review else "closed"
        if issue["state"] != desired_state:
            issue = call(f"issues/{number}", method="PATCH", payload={"state": desired_state})
        if issue.get("number") != number or issue.get("state") != desired_state:
            raise NotificationError("Issue state write verification failed")
    return {"status": status, "unchanged": unchanged,
            "failure": must_fail(report, unchanged=unchanged),
            "issue": issue.get("number") if issue else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=CONFIGS, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--scan-exit", type=int, required=True)
    parser.add_argument("--tracking-contract", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if not args.dry_run and (os.environ.get("GITHUB_ACTIONS") != "true"
                                or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY):
            raise NotificationError("Issue writes require the canonical GitHub Actions repository")
        report = json.loads(args.report.read_text())
        body = args.markdown.read_text() if args.markdown else (
            f"<!-- {CONFIGS[args.mode][1]} -->\n" + json.dumps(report, indent=2, sort_keys=True) + "\n")
        if args.mode == "parity":
            body = f"<!-- {CONFIGS[args.mode][1]} -->\n" + body
        tracking_issue = None
        if args.mode == "esp":
            if args.tracking_contract is None:
                raise NotificationError("ESP tracking contract is required")
            contract = json.loads(args.tracking_contract.read_text())
            if contract["trackingIssue"]["repository"] != REPOSITORY:
                raise NotificationError("ESP tracking repository differs")
            tracking_issue = positive_id(contract["trackingIssue"]["number"])
        result = reconcile(args.mode, report, body, scan_exit=args.scan_exit,
                           dry_run=args.dry_run, tracking_issue=tracking_issue)
        text = (f"Audit {args.mode}: {result['status']}; "
                f"{'unchanged findings, no repeated alert' if result['unchanged'] else 'new/changed result'}. "
                "Notification status does not authorize publication or clear findings.")
        print(f"::{'error' if result['failure'] else 'notice'}::{text}")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a") as output:
                output.write(f"\n### {args.mode} audit monitor\n\n{text}\n")
                if result["issue"]:
                    output.write(f"\n[Canonical review issue](https://github.com/{REPOSITORY}/issues/{result['issue']})\n")
        return int(result["failure"])
    except (NotificationError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        detail = str(error).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::Audit notification reconciliation failed: {detail}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
