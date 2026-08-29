#!/usr/bin/env python3
"""Collect fail-closed lifecycle evidence for GitHub upstream candidates.

The collector is deliberately read-only.  It separates upstream state from a
local implementation decision: only a merged pull request on the configured
branch, with the configured checks passing, is eligible for a Tumoflip review
task.  Open, draft, and closed-without-merge candidates are recorded but
suppressed.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode


HEX_40 = re.compile(r"^[0-9a-f]{40}$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CHECK_KINDS = {"checkRun", "statusContext"}
CHECK_STATES = {"passed", "pending", "failed", "missing", "notRequired"}
ISSUE_RESOLUTIONS = {"pending", "completed", "notPlanned", "unknown"}


class LifecycleError(ValueError):
    """Raised when lifecycle evidence is malformed, incomplete, or ambiguous."""


JsonFetcher = Callable[..., Any]


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise LifecycleError(f"{label} must be a non-empty string")
    return value


def _require_sha(value: Any, label: str) -> str:
    value = _require_string(value, label)
    if HEX_40.fullmatch(value) is None:
        raise LifecycleError(f"{label} must be a full lowercase commit SHA")
    return value


def _require_number(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise LifecycleError(f"{label} must be a positive integer")
    return value


def _parse_timestamp(value: Any, label: str) -> datetime:
    value = _require_string(value, label)
    if TIMESTAMP.fullmatch(value) is None:
        raise LifecycleError(f"{label} must be a UTC RFC3339 timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise LifecycleError(f"{label} is not a valid timestamp") from error


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_text(value: Any, label: str) -> str:
    value = _require_string(value, label)
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def gh_json(endpoint: str, *, paginate: bool = False) -> Any:
    """Read one GitHub API endpoint through the authenticated gh CLI."""

    command = ["gh", "api", endpoint]
    if paginate:
        command.extend(("--paginate", "--slurp"))
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired as error:
        raise LifecycleError(f"GitHub API request timed out for {endpoint}") from error
    except OSError as error:
        raise LifecycleError(f"GitHub API command failed for {endpoint}: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise LifecycleError(f"GitHub API request failed for {endpoint}: {detail}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise LifecycleError(f"GitHub API response is not JSON for {endpoint}") from error


def validate_policy(value: Any, *, label: str = "lifecycle") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LifecycleError(f"{label} must be an object")
    if value.get("provider") != "github":
        raise LifecycleError(f"{label}.provider must be github")
    if value.get("taskPolicy") != "accepted-only":
        raise LifecycleError(f"{label}.taskPolicy must be accepted-only")
    track_pulls = value.get("trackPullRequests")
    track_issues = value.get("trackIssues")
    track_releases = value.get("trackReleases")
    if (
        not isinstance(track_pulls, bool)
        or not isinstance(track_issues, bool)
        or not isinstance(track_releases, bool)
    ):
        raise LifecycleError(f"{label} tracking flags must be booleans")
    if not track_pulls and not track_issues:
        raise LifecycleError(f"{label} must track pull requests or issues")

    raw_checks = value.get("requiredChecks")
    if not isinstance(raw_checks, list):
        raise LifecycleError(f"{label}.requiredChecks must be an array")
    checks: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw_checks):
        item_label = f"{label}.requiredChecks[{index}]"
        if not isinstance(item, dict):
            raise LifecycleError(f"{item_label} must be an object")
        kind = _require_string(item.get("kind"), f"{item_label}.kind")
        name = _normalise_text(item.get("name"), f"{item_label}.name")
        if kind not in CHECK_KINDS:
            raise LifecycleError(f"{item_label}.kind is unsupported")
        identity = (kind, name)
        if identity in seen:
            raise LifecycleError(f"{label}.requiredChecks contains a duplicate")
        seen.add(identity)
        checks.append({"kind": kind, "name": name})
    checks.sort(key=lambda item: (item["kind"], item["name"]))
    result: dict[str, Any] = {
        "provider": "github",
        "taskPolicy": "accepted-only",
        "trackPullRequests": track_pulls,
        "trackIssues": track_issues,
        "trackReleases": track_releases,
        "requiredChecks": checks,
    }
    raw_milestone = value.get("milestone")
    if raw_milestone is not None:
        if not isinstance(raw_milestone, dict):
            raise LifecycleError(f"{label}.milestone must be an object")
        if not track_pulls and not track_issues:
            raise LifecycleError(
                f"{label}.milestone requires pull-request or issue tracking"
            )
        result["milestone"] = {
            "number": _require_number(
                raw_milestone.get("number"), f"{label}.milestone.number"
            ),
            "title": _normalise_text(
                raw_milestone.get("title"), f"{label}.milestone.title"
            ),
        }
    return result


def _flatten_pages(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise LifecycleError(f"{label} paginated response is not an array")
    if not value:
        return []
    if all(isinstance(item, list) for item in value):
        return [entry for page in value for entry in page]
    if all(isinstance(item, dict) for item in value):
        return value
    raise LifecycleError(f"{label} paginated response is malformed")


def _search_items(fetch: JsonFetcher, query: str, label: str) -> list[dict[str, Any]]:
    endpoint = f"search/issues?{urlencode({'q': query, 'per_page': '100'})}"
    response = fetch(endpoint, paginate=True)
    if response == []:
        return []
    pages = response if isinstance(response, list) else [response]
    if not pages or not all(isinstance(page, dict) for page in pages):
        raise LifecycleError(f"{label} search response is invalid")

    expected_total: int | None = None
    result: list[dict[str, Any]] = []
    numbers: set[int] = set()
    for page in pages:
        total = page.get("total_count")
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or total > 1000
            or page.get("incomplete_results") is not False
        ):
            raise LifecycleError(f"{label} search is incomplete")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise LifecycleError(f"{label} search total changed between pages")
        items = page.get("items")
        if not isinstance(items, list):
            raise LifecycleError(f"{label} search items are invalid")
        for item in items:
            if not isinstance(item, dict):
                raise LifecycleError(f"{label} search item is invalid")
            number = _require_number(item.get("number"), f"{label} number")
            if number in numbers:
                raise LifecycleError(f"{label} search contains duplicate #{number}")
            numbers.add(number)
            result.append(item)
    if expected_total != len(result):
        raise LifecycleError(f"{label} search did not return every result")
    return result


def _milestone_evidence(
    fetch: JsonFetcher,
    repository: str,
    expected: dict[str, Any],
) -> tuple[dict[str, Any], set[int], set[int]]:
    """Return the complete current item set for one exact milestone."""

    number = _require_number(expected.get("number"), "milestone policy number")
    title = _normalise_text(expected.get("title"), "milestone policy title")
    detail = fetch(f"repos/{repository}/milestones/{number}")
    if not isinstance(detail, dict) or detail.get("number") != number:
        raise LifecycleError("tracked milestone identity differs")
    actual_title = _normalise_text(detail.get("title"), "tracked milestone title")
    if actual_title != title:
        raise LifecycleError("tracked milestone title differs")
    state = detail.get("state")
    if state not in {"open", "closed"}:
        raise LifecycleError("tracked milestone state is invalid")
    open_items = detail.get("open_issues")
    closed_items = detail.get("closed_issues")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (open_items, closed_items)
    ):
        raise LifecycleError("tracked milestone item counts are invalid")

    endpoint = f"repos/{repository}/issues?milestone={number}&state=all&per_page=100"
    items = _flatten_pages(fetch(endpoint, paginate=True), "tracked milestone items")
    if len(items) != open_items + closed_items:
        raise LifecycleError("tracked milestone item listing is incomplete")
    pull_numbers: set[int] = set()
    issue_numbers: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            raise LifecycleError("tracked milestone contains an invalid item")
        item_number = _require_number(item.get("number"), "tracked milestone item number")
        milestone = item.get("milestone")
        if (
            not isinstance(milestone, dict)
            or milestone.get("number") != number
            or _normalise_text(
                milestone.get("title"), "tracked milestone item title"
            )
            != title
        ):
            raise LifecycleError("tracked milestone item membership differs")
        target = pull_numbers if item.get("pull_request") is not None else issue_numbers
        if item_number in pull_numbers or item_number in issue_numbers:
            raise LifecycleError("tracked milestone contains a duplicate item")
        target.add(item_number)

    def optional_timestamp(value: Any, label: str) -> str | None:
        if value is None:
            return None
        return _timestamp(_parse_timestamp(value, label))

    evidence = {
        "number": number,
        "title": title,
        "url": f"https://github.com/{repository}/milestone/{number}",
        "state": state,
        "updatedAt": _timestamp(
            _parse_timestamp(detail.get("updated_at"), "tracked milestone updatedAt")
        ),
        "closedAt": optional_timestamp(
            detail.get("closed_at"), "tracked milestone closedAt"
        ),
        "dueAt": optional_timestamp(detail.get("due_on"), "tracked milestone dueAt"),
        "openItems": open_items,
        "closedItems": closed_items,
        "pullRequests": sorted(pull_numbers),
        "issues": sorted(issue_numbers),
    }
    return evidence, pull_numbers, issue_numbers


def _commit_included(
    fetch: JsonFetcher,
    repository: str,
    commit: str,
    target: str,
    label: str,
) -> bool:
    commit = _require_sha(commit, f"{label} commit")
    target = _require_sha(target, f"{label} target")
    if commit == target:
        return True
    response = fetch(f"repos/{repository}/compare/{commit}...{target}")
    if not isinstance(response, dict):
        raise LifecycleError(f"{label} comparison is invalid")
    status = response.get("status")
    ahead = response.get("ahead_by")
    behind = response.get("behind_by")
    if status not in {"identical", "ahead", "behind", "diverged"}:
        raise LifecycleError(f"{label} comparison status is invalid")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (ahead, behind)
    ):
        raise LifecycleError(f"{label} comparison counts are invalid")
    base = response.get("base_commit")
    if not isinstance(base, dict) or _require_sha(
        base.get("sha"), f"{label} comparison base"
    ) != commit:
        raise LifecycleError(f"{label} comparison base differs")
    head = response.get("head_commit")
    if head is not None and (
        not isinstance(head, dict)
        or _require_sha(head.get("sha"), f"{label} comparison head") != target
    ):
        raise LifecycleError(f"{label} comparison head differs")
    return status in {"identical", "ahead"} and behind == 0


def _latest_by_name(
    values: list[dict[str, Any]],
    *,
    name_key: str,
    timestamp_keys: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, tuple[tuple[str, int], dict[str, Any]]] = {}
    for item in values:
        name = _normalise_text(item.get(name_key), f"check {name_key}")
        timestamp = ""
        for key in timestamp_keys:
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate:
                timestamp = candidate
                break
        item_id = item.get("id")
        if not isinstance(item_id, int) or isinstance(item_id, bool) or item_id < 1:
            raise LifecycleError(f"check {name} id is invalid")
        identity = (timestamp, item_id)
        if name not in latest or identity > latest[name][0]:
            latest[name] = (identity, item)
    return {name: value for name, (_, value) in latest.items()}


def _check_evidence(
    fetch: JsonFetcher,
    repository: str,
    commit: str,
    required: list[dict[str, str]],
) -> dict[str, Any]:
    commit = _require_sha(commit, "checked commit")
    if not required:
        return {"commit": commit, "status": "notRequired", "required": []}

    check_payload = fetch(
        f"repos/{repository}/commits/{commit}/check-runs?per_page=100&filter=latest"
    )
    if not isinstance(check_payload, dict):
        raise LifecycleError("check-runs response is invalid")
    check_runs = check_payload.get("check_runs")
    total = check_payload.get("total_count")
    if (
        not isinstance(check_runs, list)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total != len(check_runs)
        or total > 100
    ):
        raise LifecycleError("check-runs response is incomplete")
    if not all(isinstance(item, dict) for item in check_runs):
        raise LifecycleError("check-runs response contains an invalid item")
    latest_runs = _latest_by_name(
        check_runs,
        name_key="name",
        timestamp_keys=("completed_at", "started_at"),
    )

    statuses = _flatten_pages(
        fetch(
            f"repos/{repository}/commits/{commit}/statuses?per_page=100",
            paginate=True,
        ),
        "commit statuses",
    )
    if not all(isinstance(item, dict) for item in statuses):
        raise LifecycleError("commit statuses response contains an invalid item")
    latest_statuses = _latest_by_name(
        statuses,
        name_key="context",
        timestamp_keys=("updated_at", "created_at"),
    )

    evidence: list[dict[str, Any]] = []
    states: list[str] = []
    for requirement in required:
        kind = requirement["kind"]
        name = requirement["name"]
        if kind == "checkRun":
            item = latest_runs.get(name)
            if item is None:
                entry = {"kind": kind, "name": name, "state": "missing"}
            else:
                status = item.get("status")
                conclusion = item.get("conclusion")
                if status in {"queued", "in_progress", "pending", "waiting", "requested"}:
                    state = "pending"
                elif status == "completed" and conclusion == "success":
                    state = "passed"
                else:
                    state = "failed"
                entry = {
                    "kind": kind,
                    "name": name,
                    "state": state,
                    "id": _require_number(item.get("id"), f"check run {name} id"),
                    "status": _require_string(status, f"check run {name} status"),
                    "conclusion": conclusion,
                    "url": item.get("details_url"),
                }
        else:
            item = latest_statuses.get(name)
            if item is None:
                entry = {"kind": kind, "name": name, "state": "missing"}
            else:
                raw_state = item.get("state")
                state = (
                    "passed"
                    if raw_state == "success"
                    else "pending"
                    if raw_state == "pending"
                    else "failed"
                )
                entry = {
                    "kind": kind,
                    "name": name,
                    "state": state,
                    "id": _require_number(item.get("id"), f"status context {name} id"),
                    "status": _require_string(raw_state, f"status context {name} state"),
                    "url": item.get("target_url"),
                }
        states.append(entry["state"])
        evidence.append(entry)

    if "failed" in states:
        overall = "failed"
    elif "missing" in states:
        overall = "missing"
    elif "pending" in states:
        overall = "pending"
    else:
        overall = "passed"
    return {"commit": commit, "status": overall, "required": evidence}


def _review_evidence(fetch: JsonFetcher, repository: str, number: int) -> str:
    values = _flatten_pages(
        fetch(f"repos/{repository}/pulls/{number}/reviews?per_page=100", paginate=True),
        f"pull request {number} reviews",
    )
    latest: dict[str, tuple[tuple[str, int], str]] = {}
    for item in values:
        if not isinstance(item, dict):
            raise LifecycleError(f"pull request {number} review is invalid")
        user = item.get("user")
        if not isinstance(user, dict):
            raise LifecycleError(f"pull request {number} review user is invalid")
        login = _require_string(user.get("login"), f"pull request {number} reviewer")
        state = _require_string(item.get("state"), f"pull request {number} review state")
        submitted = item.get("submitted_at") or ""
        if submitted:
            _parse_timestamp(submitted, f"pull request {number} review submittedAt")
        review_id = _require_number(item.get("id"), f"pull request {number} review id")
        identity = (submitted, review_id)
        if login not in latest or identity > latest[login][0]:
            latest[login] = (identity, state)
    states = {state for _, state in latest.values() if state != "DISMISSED"}
    if "CHANGES_REQUESTED" in states:
        return "changesRequested"
    if "APPROVED" in states:
        return "approved"
    return "none"


def _pull_requests(
    *,
    fetch: JsonFetcher,
    repository: str,
    branch: str,
    since: datetime,
    reviewed_commit: str,
    branch_head: str,
    branch_check_status: str,
    release_commit: str | None,
    required_checks: list[dict[str, str]],
    milestone: dict[str, Any] | None = None,
    milestone_numbers: set[int] | None = None,
) -> list[dict[str, Any]]:
    query = (
        f"repo:{repository} is:pr base:{branch} "
        f"updated:>={since:%Y-%m-%d}"
    )
    summaries = _search_items(fetch, query, "pull request")
    numbers = {
        _require_number(item.get("number"), "pull request number")
        for item in summaries
    }
    numbers.update(milestone_numbers or set())
    result: list[dict[str, Any]] = []
    for number in sorted(numbers):
        milestone_item = number in (milestone_numbers or set())
        detail = fetch(f"repos/{repository}/pulls/{number}")
        if not isinstance(detail, dict) or detail.get("number") != number:
            raise LifecycleError(f"pull request identity differs: {number}")
        updated_at = _parse_timestamp(
            detail.get("updated_at"), f"pull request {number} updatedAt"
        )
        if updated_at <= since and not milestone_item:
            continue
        base = detail.get("base")
        head = detail.get("head")
        if not isinstance(base, dict) or base.get("ref") != branch:
            raise LifecycleError(f"pull request base branch differs: {number}")
        if not isinstance(head, dict):
            raise LifecycleError(f"pull request head is invalid: {number}")
        head_commit = _require_sha(head.get("sha"), f"pull request {number} head")
        state = detail.get("state")
        merged_at_value = detail.get("merged_at")
        closed_at_value = detail.get("closed_at")
        if merged_at_value is not None:
            if state != "closed":
                raise LifecycleError(f"merged pull request is not closed: {number}")
            lifecycle = "merged"
            merged_at = _timestamp(
                _parse_timestamp(merged_at_value, f"pull request {number} mergedAt")
            )
            merge_commit = _require_sha(
                detail.get("merge_commit_sha"), f"pull request {number} merge commit"
            )
        elif state == "closed":
            lifecycle = "closedUnmerged"
            merged_at = None
            merge_commit = None
        elif state == "open":
            lifecycle = "draft" if detail.get("draft") is True else "open"
            merged_at = None
            merge_commit = None
        else:
            raise LifecycleError(f"pull request state is invalid: {number}")

        if lifecycle in {"open", "draft"}:
            raw_mergeable = detail.get("mergeable")
            raw_mergeable_state = detail.get("mergeable_state")
            if not isinstance(raw_mergeable, bool) and raw_mergeable is not None:
                raise LifecycleError(f"pull request {number} mergeability is invalid")
            if raw_mergeable_state is not None:
                raw_mergeable_state = _normalise_text(
                    raw_mergeable_state, f"pull request {number} mergeableState"
                )
            if raw_mergeable is True:
                mergeability = "mergeable"
            elif raw_mergeable is False and raw_mergeable_state == "dirty":
                mergeability = "conflicting"
            elif raw_mergeable is False:
                mergeability = "blocked"
            else:
                mergeability = "unknown"
        else:
            raw_mergeable = None
            raw_mergeable_state = None
            mergeability = "notApplicable"

        if milestone is None:
            milestone_membership = "notTracked"
        else:
            raw_milestone = detail.get("milestone")
            belongs = (
                isinstance(raw_milestone, dict)
                and raw_milestone.get("number") == milestone["number"]
                and _normalise_text(
                    raw_milestone.get("title"),
                    f"pull request {number} milestone title",
                )
                == milestone["title"]
            )
            if belongs != milestone_item:
                raise LifecycleError(
                    f"pull request {number} tracked milestone membership differs"
                )
            milestone_membership = "included" if belongs else "notIncluded"
        closed_at = (
            _timestamp(
                _parse_timestamp(closed_at_value, f"pull request {number} closedAt")
            )
            if closed_at_value is not None
            else None
        )
        checks = _check_evidence(
            fetch, repository, head_commit, required_checks
        )
        review = _review_evidence(fetch, repository, number)

        reviewed_inclusion = "notIncluded"
        branch_inclusion = "notIncluded"
        release_inclusion = "notReleased"
        if lifecycle == "merged" and merge_commit is not None:
            reviewed_inclusion = (
                "included"
                if _commit_included(
                    fetch,
                    repository,
                    merge_commit,
                    reviewed_commit,
                    f"pull request {number} reviewed boundary",
                )
                else "notIncluded"
            )
            branch_inclusion = (
                "included"
                if _commit_included(
                    fetch,
                    repository,
                    merge_commit,
                    branch_head,
                    f"pull request {number} branch",
                )
                else "notIncluded"
            )
            if release_commit is None:
                release_inclusion = "unavailable"
            else:
                release_inclusion = (
                    "released"
                    if _commit_included(
                        fetch,
                        repository,
                        merge_commit,
                        release_commit,
                        f"pull request {number} release",
                    )
                    else "notReleased"
                )

        if lifecycle in {"open", "draft"}:
            disposition = "suppressed"
            if milestone is not None and milestone_membership == "notIncluded":
                outcome = "deferred"
                reason = "outsideTrackedMilestone"
            else:
                outcome = "pending"
                reason = "upstreamPending"
        elif lifecycle == "closedUnmerged":
            outcome = "declined"
            disposition = "suppressed"
            reason = "upstreamDeclined"
        elif reviewed_inclusion == "included":
            outcome = "accepted"
            disposition = "suppressed"
            reason = "alreadyReviewed"
        elif branch_inclusion != "included":
            outcome = "blocked"
            disposition = "blocked"
            reason = "mergeMissingFromBranch"
        elif checks["status"] not in {"passed", "notRequired"}:
            outcome = "blocked"
            disposition = "blocked"
            reason = f"checks{checks['status'].capitalize()}"
        elif review == "changesRequested":
            outcome = "blocked"
            disposition = "blocked"
            reason = "changesRequestedAfterMerge"
        elif branch_check_status not in {"passed", "notRequired"}:
            outcome = "blocked"
            disposition = "blocked"
            reason = f"branchChecks{branch_check_status.capitalize()}"
        else:
            outcome = "accepted"
            disposition = "eligible"
            reason = "mergedChecksPassed"

        result.append(
            {
                "number": number,
                "title": _normalise_text(
                    detail.get("title"), f"pull request {number} title"
                ),
                "url": f"https://github.com/{repository}/pull/{number}",
                "updatedAt": _timestamp(updated_at),
                "closedAt": closed_at,
                "mergedAt": merged_at,
                "headCommit": head_commit,
                "mergeCommit": merge_commit,
                "lifecycle": lifecycle,
                "mergeable": raw_mergeable,
                "mergeableState": raw_mergeable_state,
                "mergeability": mergeability,
                "milestone": milestone_membership,
                "review": review,
                "checks": checks,
                "reviewedBoundary": reviewed_inclusion,
                "branch": branch_inclusion,
                "release": release_inclusion,
                "outcome": outcome,
                "taskDisposition": disposition,
                "taskReason": reason,
            }
        )
    result.sort(key=lambda item: (item["updatedAt"], item["number"]))
    return result


def _issues(
    *,
    fetch: JsonFetcher,
    repository: str,
    since: datetime,
    milestone: dict[str, Any] | None = None,
    milestone_numbers: set[int] | None = None,
) -> list[dict[str, Any]]:
    query = f"repo:{repository} is:issue updated:>={since:%Y-%m-%d}"
    summaries = _search_items(fetch, query, "issue")
    numbers = {
        _require_number(item.get("number"), "issue number") for item in summaries
    }
    numbers.update(milestone_numbers or set())
    result: list[dict[str, Any]] = []
    for number in sorted(numbers):
        milestone_item = number in (milestone_numbers or set())
        detail = fetch(f"repos/{repository}/issues/{number}")
        if (
            not isinstance(detail, dict)
            or detail.get("number") != number
            or detail.get("pull_request") is not None
        ):
            raise LifecycleError(f"issue identity differs: {number}")
        updated_at = _parse_timestamp(detail.get("updated_at"), f"issue {number} updatedAt")
        if updated_at <= since and not milestone_item:
            continue
        if milestone is None:
            milestone_membership = "notTracked"
        else:
            raw_milestone = detail.get("milestone")
            belongs = (
                isinstance(raw_milestone, dict)
                and raw_milestone.get("number") == milestone["number"]
                and _normalise_text(
                    raw_milestone.get("title"), f"issue {number} milestone title"
                )
                == milestone["title"]
            )
            if belongs != milestone_item:
                raise LifecycleError(
                    f"issue {number} tracked milestone membership differs"
                )
            milestone_membership = "included" if belongs else "notIncluded"
        state = detail.get("state")
        state_reason = detail.get("state_reason")
        if state == "open":
            resolution = "pending"
        elif state == "closed" and state_reason == "completed":
            resolution = "completed"
        elif state == "closed" and state_reason == "not_planned":
            resolution = "notPlanned"
        elif state == "closed":
            resolution = "unknown"
        else:
            raise LifecycleError(f"issue state is invalid: {number}")
        result.append(
            {
                "number": number,
                "title": _normalise_text(detail.get("title"), f"issue {number} title"),
                "url": f"https://github.com/{repository}/issues/{number}",
                "updatedAt": _timestamp(updated_at),
                "state": state,
                "milestone": milestone_membership,
                "resolution": resolution,
                "taskDisposition": "suppressed",
                "taskReason": (
                    "upstreamDeclined"
                    if resolution == "notPlanned"
                    else "issueIsNotImplementationEvidence"
                ),
            }
        )
    result.sort(key=lambda item: (item["updatedAt"], item["number"]))
    return result


def collect(
    *,
    repository: str,
    branch: str,
    since: datetime,
    reviewed_commit: str,
    branch_head: str,
    release_commit: str | None,
    policy: dict[str, Any],
    fetch: JsonFetcher = gh_json,
) -> dict[str, Any]:
    """Collect current GitHub lifecycle state at exact immutable identities."""

    if REPOSITORY.fullmatch(repository) is None:
        raise LifecycleError("lifecycle repository is invalid")
    branch = _require_string(branch, "lifecycle branch")
    if since.tzinfo is None:
        raise LifecycleError("lifecycle boundary must have a timezone")
    reviewed_commit = _require_sha(reviewed_commit, "lifecycle reviewed commit")
    branch_head = _require_sha(branch_head, "lifecycle branch head")
    policy = validate_policy(policy)
    if policy["trackReleases"]:
        release_commit = _require_sha(release_commit, "lifecycle release commit")
    else:
        release_commit = None
    milestone: dict[str, Any] | None = None
    milestone_pull_numbers: set[int] = set()
    milestone_issue_numbers: set[int] = set()
    if "milestone" in policy:
        milestone, milestone_pull_numbers, milestone_issue_numbers = (
            _milestone_evidence(fetch, repository, policy["milestone"])
        )
    branch_checks = _check_evidence(
        fetch, repository, branch_head, policy["requiredChecks"]
    )
    pulls = (
        _pull_requests(
            fetch=fetch,
            repository=repository,
            branch=branch,
            since=since,
            reviewed_commit=reviewed_commit,
            branch_head=branch_head,
            branch_check_status=branch_checks["status"],
            release_commit=release_commit,
            required_checks=policy["requiredChecks"],
            milestone=policy.get("milestone"),
            milestone_numbers=milestone_pull_numbers,
        )
        if policy["trackPullRequests"]
        else []
    )
    issues = (
        _issues(
            fetch=fetch,
            repository=repository,
            since=since,
            milestone=policy.get("milestone"),
            milestone_numbers=milestone_issue_numbers,
        )
        if policy["trackIssues"]
        else []
    )
    summary = {
        "accepted": sum(item["outcome"] == "accepted" for item in pulls),
        "blocked": sum(item["taskDisposition"] == "blocked" for item in pulls),
        "declined": sum(item["outcome"] == "declined" for item in pulls),
        "deferred": sum(item["outcome"] == "deferred" for item in pulls),
        "eligible": sum(item["taskDisposition"] == "eligible" for item in pulls),
        "pending": sum(item["outcome"] == "pending" for item in pulls),
        "suppressed": sum(item["taskDisposition"] == "suppressed" for item in pulls)
        + len(issues),
        "issues": len(issues),
        "pullRequests": len(pulls),
    }
    review_required = summary["eligible"] > 0 or summary["blocked"] > 0
    if branch_checks["status"] not in {"passed", "notRequired"}:
        review_required = True
    result = {
        "schema": 1,
        "provider": "github",
        "policy": policy,
        "since": _timestamp(since),
        "branchChecks": branch_checks,
        "pullRequests": pulls,
        "issues": issues,
        "summary": summary,
        "reviewRequired": review_required,
    }
    if milestone is not None:
        result["milestone"] = milestone
    return result


def _validate_check_artifact(
    value: Any,
    *,
    expected_commit: str,
    expected_requirements: list[dict[str, str]],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LifecycleError(f"{label} is invalid")
    if value.get("commit") != _require_sha(expected_commit, f"{label} commit"):
        raise LifecycleError(f"{label} targets a different commit")
    required = value.get("required")
    if not isinstance(required, list) or not all(
        isinstance(item, dict) for item in required
    ):
        raise LifecycleError(f"{label} required evidence is invalid")
    identities = [
        {"kind": item.get("kind"), "name": item.get("name")} for item in required
    ]
    if identities != expected_requirements:
        raise LifecycleError(f"{label} requirements differ from policy")
    states: list[str] = []
    for item in required:
        state = item.get("state")
        if state not in CHECK_STATES - {"notRequired"}:
            raise LifecycleError(f"{label} required check state is invalid")
        states.append(state)
        kind = item["kind"]
        if state == "missing":
            if any(
                key in item for key in ("id", "status", "conclusion", "url")
            ):
                raise LifecycleError(f"{label} missing check carries raw evidence")
            continue

        _require_number(item.get("id"), f"{label} check id")
        raw_status = _require_string(item.get("status"), f"{label} check status")
        url = item.get("url")
        if url is not None and not isinstance(url, str):
            raise LifecycleError(f"{label} check URL is invalid")
        conclusion = item.get("conclusion")
        if conclusion is not None and not isinstance(conclusion, str):
            raise LifecycleError(f"{label} check conclusion is invalid")
        if kind == "checkRun":
            if raw_status in {
                "queued",
                "in_progress",
                "pending",
                "waiting",
                "requested",
            }:
                raw_state = "pending"
            elif raw_status == "completed" and conclusion == "success":
                raw_state = "passed"
            else:
                raw_state = "failed"
        else:
            if conclusion is not None:
                raise LifecycleError(
                    f"{label} status context carries a check-run conclusion"
                )
            raw_state = (
                "passed"
                if raw_status == "success"
                else "pending"
                if raw_status == "pending"
                else "failed"
            )
        if state != raw_state:
            raise LifecycleError(f"{label} check state differs from raw evidence")
    if not expected_requirements:
        expected_status = "notRequired"
    elif "failed" in states:
        expected_status = "failed"
    elif "missing" in states:
        expected_status = "missing"
    elif "pending" in states:
        expected_status = "pending"
    else:
        expected_status = "passed"
    if value.get("status") != expected_status:
        raise LifecycleError(f"{label} summary differs from required checks")
    return value


def _validate_milestone_artifact(
    value: Any,
    *,
    repository: str,
    expected: dict[str, Any],
) -> tuple[dict[str, Any], set[int], set[int]]:
    if not isinstance(value, dict):
        raise LifecycleError("tracked milestone evidence is invalid")
    number = _require_number(expected.get("number"), "milestone policy number")
    title = _normalise_text(expected.get("title"), "milestone policy title")
    if value.get("number") != number or value.get("title") != title:
        raise LifecycleError("tracked milestone evidence identity differs")
    if value.get("url") != f"https://github.com/{repository}/milestone/{number}":
        raise LifecycleError("tracked milestone evidence URL is invalid")
    state = value.get("state")
    if state not in {"open", "closed"}:
        raise LifecycleError("tracked milestone evidence state is invalid")
    _parse_timestamp(value.get("updatedAt"), "tracked milestone evidence updatedAt")
    closed_at = value.get("closedAt")
    if state == "closed":
        _parse_timestamp(closed_at, "tracked milestone evidence closedAt")
    elif closed_at is not None:
        raise LifecycleError("open tracked milestone carries close evidence")
    due_at = value.get("dueAt")
    if due_at is not None:
        _parse_timestamp(due_at, "tracked milestone evidence dueAt")

    counts: list[int] = []
    for key in ("openItems", "closedItems"):
        count = value.get(key)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise LifecycleError("tracked milestone evidence counts are invalid")
        counts.append(count)

    raw_pulls = value.get("pullRequests")
    raw_issues = value.get("issues")
    if not isinstance(raw_pulls, list) or not isinstance(raw_issues, list):
        raise LifecycleError("tracked milestone evidence item lists are invalid")
    pull_numbers = {
        _require_number(item, "tracked milestone pull request") for item in raw_pulls
    }
    issue_numbers = {
        _require_number(item, "tracked milestone issue") for item in raw_issues
    }
    if (
        len(pull_numbers) != len(raw_pulls)
        or len(issue_numbers) != len(raw_issues)
        or pull_numbers & issue_numbers
    ):
        raise LifecycleError("tracked milestone evidence contains duplicate items")
    if raw_pulls != sorted(raw_pulls) or raw_issues != sorted(raw_issues):
        raise LifecycleError("tracked milestone evidence items are not canonical")
    if sum(counts) != len(pull_numbers) + len(issue_numbers):
        raise LifecycleError("tracked milestone evidence listing is incomplete")
    return value, pull_numbers, issue_numbers


def validate_evidence(
    value: Any,
    *,
    repository: str,
    branch_head: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Validate a downloaded lifecycle artifact before any issue write."""

    if not isinstance(value, dict) or value.get("schema") != 1:
        raise LifecycleError("lifecycle evidence schema is invalid")
    if value.get("provider") != "github":
        raise LifecycleError("lifecycle evidence provider is invalid")
    expected_policy = validate_policy(policy)
    if value.get("policy") != expected_policy:
        raise LifecycleError("lifecycle evidence policy differs from contract")
    _parse_timestamp(value.get("since"), "lifecycle evidence since")
    branch_checks = value.get("branchChecks")
    _validate_check_artifact(
        branch_checks,
        expected_commit=branch_head,
        expected_requirements=expected_policy["requiredChecks"],
        label="lifecycle branch checks",
    )

    milestone_pull_numbers: set[int] = set()
    milestone_issue_numbers: set[int] = set()
    if "milestone" in expected_policy:
        _, milestone_pull_numbers, milestone_issue_numbers = (
            _validate_milestone_artifact(
                value.get("milestone"),
                repository=repository,
                expected=expected_policy["milestone"],
            )
        )
    elif value.get("milestone") is not None:
        raise LifecycleError("untracked milestone evidence is not allowed")

    pulls = value.get("pullRequests")
    issues = value.get("issues")
    if not isinstance(pulls, list) or not isinstance(issues, list):
        raise LifecycleError("lifecycle candidates are invalid")
    pull_numbers: set[int] = set()
    for item in pulls:
        if not isinstance(item, dict):
            raise LifecycleError("lifecycle pull request is invalid")
        number = _require_number(item.get("number"), "lifecycle pull request number")
        if number in pull_numbers:
            raise LifecycleError("lifecycle pull request is duplicated")
        pull_numbers.add(number)
        _normalise_text(item.get("title"), "lifecycle pull request title")
        _parse_timestamp(item.get("updatedAt"), "lifecycle pull request updatedAt")
        head_commit = _require_sha(
            item.get("headCommit"), "lifecycle pull request head"
        )
        if item.get("url") != f"https://github.com/{repository}/pull/{number}":
            raise LifecycleError("lifecycle pull request URL is invalid")
        checks = _validate_check_artifact(
            item.get("checks"),
            expected_commit=head_commit,
            expected_requirements=expected_policy["requiredChecks"],
            label=f"lifecycle pull request {number} checks",
        )
        lifecycle = item.get("lifecycle")
        raw_mergeable = item.get("mergeable")
        raw_mergeable_state = item.get("mergeableState")
        mergeability = item.get("mergeability")
        milestone_membership = item.get("milestone")
        review = item.get("review")
        reviewed_boundary = item.get("reviewedBoundary")
        branch = item.get("branch")
        release = item.get("release")
        if lifecycle not in {"open", "draft", "merged", "closedUnmerged"}:
            raise LifecycleError("lifecycle pull request state is invalid")
        expected_membership = (
            "included"
            if number in milestone_pull_numbers
            else "notIncluded"
            if "milestone" in expected_policy
            else "notTracked"
        )
        if milestone_membership != expected_membership:
            raise LifecycleError("lifecycle pull request milestone state is invalid")
        if lifecycle in {"open", "draft"}:
            if not isinstance(raw_mergeable, bool) and raw_mergeable is not None:
                raise LifecycleError("lifecycle pull request mergeability is invalid")
            if raw_mergeable_state is not None:
                _normalise_text(
                    raw_mergeable_state,
                    "lifecycle pull request mergeableState",
                )
            expected_mergeability = (
                "mergeable"
                if raw_mergeable is True
                else "conflicting"
                if raw_mergeable is False and raw_mergeable_state == "dirty"
                else "blocked"
                if raw_mergeable is False
                else "unknown"
            )
        else:
            if raw_mergeable is not None or raw_mergeable_state is not None:
                raise LifecycleError("closed lifecycle pull request carries mergeability")
            expected_mergeability = "notApplicable"
        if mergeability != expected_mergeability:
            raise LifecycleError("lifecycle pull request mergeability summary differs")
        if review not in {"none", "approved", "changesRequested"}:
            raise LifecycleError("lifecycle pull request review is invalid")
        if reviewed_boundary not in {"included", "notIncluded"}:
            raise LifecycleError("lifecycle reviewed-boundary state is invalid")
        if branch not in {"included", "notIncluded"}:
            raise LifecycleError("lifecycle branch-inclusion state is invalid")
        if release not in {"released", "notReleased", "unavailable"}:
            raise LifecycleError("lifecycle release-inclusion state is invalid")
        merge_commit = item.get("mergeCommit")
        if lifecycle == "merged":
            _require_sha(merge_commit, "lifecycle pull request merge")
            _parse_timestamp(item.get("mergedAt"), "lifecycle pull request mergedAt")
        elif merge_commit is not None or item.get("mergedAt") is not None:
            raise LifecycleError("unmerged lifecycle pull request carries merge evidence")
        if lifecycle in {"merged", "closedUnmerged"}:
            _parse_timestamp(item.get("closedAt"), "lifecycle pull request closedAt")
        elif item.get("closedAt") is not None:
            raise LifecycleError("open lifecycle pull request carries close evidence")

        if lifecycle in {"open", "draft"}:
            expected = (
                ("deferred", "suppressed", "outsideTrackedMilestone")
                if "milestone" in expected_policy
                and milestone_membership == "notIncluded"
                else ("pending", "suppressed", "upstreamPending")
            )
        elif lifecycle == "closedUnmerged":
            expected = ("declined", "suppressed", "upstreamDeclined")
        elif reviewed_boundary == "included":
            expected = ("accepted", "suppressed", "alreadyReviewed")
        elif branch != "included":
            expected = ("blocked", "blocked", "mergeMissingFromBranch")
        elif checks["status"] not in {"passed", "notRequired"}:
            expected = (
                "blocked",
                "blocked",
                f"checks{checks['status'].capitalize()}",
            )
        elif review == "changesRequested":
            expected = ("blocked", "blocked", "changesRequestedAfterMerge")
        elif branch_checks["status"] not in {"passed", "notRequired"}:
            expected = (
                "blocked",
                "blocked",
                f"branchChecks{branch_checks['status'].capitalize()}",
            )
        else:
            expected = ("accepted", "eligible", "mergedChecksPassed")
        actual = (
            item.get("outcome"),
            item.get("taskDisposition"),
            item.get("taskReason"),
        )
        if actual != expected:
            raise LifecycleError(
                "lifecycle pull request disposition differs from exact evidence"
            )

    issue_numbers: set[int] = set()
    for item in issues:
        if not isinstance(item, dict):
            raise LifecycleError("lifecycle issue is invalid")
        number = _require_number(item.get("number"), "lifecycle issue number")
        if number in issue_numbers:
            raise LifecycleError("lifecycle issue is duplicated")
        issue_numbers.add(number)
        _normalise_text(item.get("title"), "lifecycle issue title")
        _parse_timestamp(item.get("updatedAt"), "lifecycle issue updatedAt")
        if item.get("url") != f"https://github.com/{repository}/issues/{number}":
            raise LifecycleError("lifecycle issue URL is invalid")
        expected_membership = (
            "included"
            if number in milestone_issue_numbers
            else "notIncluded"
            if "milestone" in expected_policy
            else "notTracked"
        )
        if item.get("milestone") != expected_membership:
            raise LifecycleError("lifecycle issue milestone state is invalid")
        if item.get("resolution") not in ISSUE_RESOLUTIONS:
            raise LifecycleError("lifecycle issue resolution is invalid")
        if item.get("taskDisposition") != "suppressed":
            raise LifecycleError("issue-only evidence must not create a task")
        state = item.get("state")
        resolution = item["resolution"]
        if state == "open" and resolution != "pending":
            raise LifecycleError("open lifecycle issue resolution is invalid")
        if state == "closed" and resolution == "pending":
            raise LifecycleError("closed lifecycle issue resolution is invalid")
        if state not in {"open", "closed"}:
            raise LifecycleError("lifecycle issue state is invalid")
        expected_reason = (
            "upstreamDeclined"
            if resolution == "notPlanned"
            else "issueIsNotImplementationEvidence"
        )
        if item.get("taskReason") != expected_reason:
            raise LifecycleError("lifecycle issue task reason is invalid")

    if not milestone_pull_numbers.issubset(pull_numbers) or not milestone_issue_numbers.issubset(
        issue_numbers
    ):
        raise LifecycleError("tracked milestone candidate listing is incomplete")

    expected_summary = {
        "accepted": sum(item["outcome"] == "accepted" for item in pulls),
        "blocked": sum(item["taskDisposition"] == "blocked" for item in pulls),
        "declined": sum(item["outcome"] == "declined" for item in pulls),
        "deferred": sum(item["outcome"] == "deferred" for item in pulls),
        "eligible": sum(item["taskDisposition"] == "eligible" for item in pulls),
        "pending": sum(item["outcome"] == "pending" for item in pulls),
        "suppressed": sum(item["taskDisposition"] == "suppressed" for item in pulls)
        + len(issues),
        "issues": len(issues),
        "pullRequests": len(pulls),
    }
    if value.get("summary") != expected_summary:
        raise LifecycleError("lifecycle summary differs from candidates")
    expected_review = expected_summary["eligible"] > 0 or expected_summary["blocked"] > 0
    if branch_checks["status"] not in {"passed", "notRequired"}:
        expected_review = True
    if value.get("reviewRequired") is not expected_review:
        raise LifecycleError("lifecycle review state differs from evidence")
    return value
