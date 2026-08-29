#!/usr/bin/env python3
"""Watch DarkFlippers/Unleashed without changing any firmware state.

The checked-in contract is the last *human-reviewed* upstream boundary.  This
tool never advances that boundary: a reviewed control-plane pull request is
required for every baseline update.  Its only remote side effect is delegated
to the workflow's separate issue-reporting job.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

if __package__:
    from . import github_lifecycle
else:
    import github_lifecycle


HEX_40 = re.compile(r"^[0-9a-f]{40}$")
RELEASE_TAG = re.compile(r"^unlshd-[0-9]{3}$")
RELEASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
TUMOFLIP_ISSUE_URL = re.compile(
    r"^https://github\.com/squazaryu/tumoflip/issues/[1-9][0-9]*$"
)
ISSUE_MARKER = "<!-- upstream-watch:DarkFlippers/unleashed-firmware -->"
ISSUE_TITLE = "Watch DarkFlippers/unleashed-firmware upstream changes"
ISSUE_AUTHOR = "github-actions[bot]"
MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+.!|~\-])")
BARE_HTTP_SCHEME = re.compile(r"\b(https?):(?=//)", re.IGNORECASE)
URL_BREAK = "\u200b"
DECISION_CLASSIFICATIONS = {"covered", "issueOnly", "metadataOnly"}
HARDWARE_ACCEPTANCE = {"accepted", "pending", "notRequired"}


class WatchError(RuntimeError):
    """Raised when upstream evidence is incomplete or ambiguous."""


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise WatchError(f"{label} must be a non-empty string")
    return value


def _require_sha(value: Any, label: str) -> str:
    value = _require_string(value, label)
    if HEX_40.fullmatch(value) is None:
        raise WatchError(f"{label} must be a full lowercase commit SHA")
    return value


def _require_release_name(value: Any, label: str) -> str:
    value = _require_string(value, label)
    if RELEASE_NAME.fullmatch(value) is None:
        raise WatchError(f"{label} is not a safe release tag")
    return value


def _parse_timestamp(value: Any, label: str) -> datetime:
    value = _require_string(value, label)
    if TIMESTAMP.fullmatch(value) is None:
        raise WatchError(f"{label} must be a UTC RFC3339 timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise WatchError(f"{label} is not a valid timestamp") from error


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_text(value: Any, label: str) -> str:
    value = _require_string(value, label)
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def _require_tumoflip_issue(value: Any, label: str) -> str:
    value = _require_string(value, label)
    if TUMOFLIP_ISSUE_URL.fullmatch(value) is None:
        raise WatchError(f"{label} must be a canonical Tumoflip issue URL")
    return value


def _validate_decision_ledger(value: Any, reviewed_commit: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise WatchError("schema 2/3 upstream watch requires a non-empty decision ledger")

    seen_commits: set[str] = set()
    previous_through: str | None = None
    for batch_index, batch in enumerate(value):
        label = f"decisionLedger[{batch_index}]"
        if not isinstance(batch, dict):
            raise WatchError(f"{label} must be an object")
        from_commit = _require_sha(batch.get("fromExclusive"), f"{label}.fromExclusive")
        through_commit = _require_sha(
            batch.get("throughInclusive"), f"{label}.throughInclusive"
        )
        if from_commit == through_commit:
            raise WatchError(f"{label} cannot cover an empty commit range")
        if previous_through is not None and from_commit != previous_through:
            raise WatchError("decision ledger batches must form one contiguous chain")
        _parse_timestamp(batch.get("reviewedAt"), f"{label}.reviewedAt")

        entries = batch.get("entries")
        if not isinstance(entries, list) or not entries:
            raise WatchError(f"{label}.entries must be a non-empty array")
        for entry_index, entry in enumerate(entries):
            entry_label = f"{label}.entries[{entry_index}]"
            if not isinstance(entry, dict):
                raise WatchError(f"{entry_label} must be an object")
            upstream_commit = _require_sha(
                entry.get("upstreamCommit"), f"{entry_label}.upstreamCommit"
            )
            if upstream_commit in seen_commits:
                raise WatchError("decision ledger contains a duplicate upstream commit")
            seen_commits.add(upstream_commit)
            classification = _require_string(
                entry.get("classification"), f"{entry_label}.classification"
            )
            if classification not in DECISION_CLASSIFICATIONS:
                raise WatchError(f"{entry_label}.classification is invalid")
            _normalise_text(entry.get("summary"), f"{entry_label}.summary")

            issue = entry.get("issue")
            if issue is not None:
                _require_tumoflip_issue(issue, f"{entry_label}.issue")
            if classification == "issueOnly" and issue is None:
                raise WatchError(f"{entry_label} issueOnly decision requires an issue")

            implementation = entry.get("tumoflip")
            if classification == "covered":
                if not isinstance(implementation, dict):
                    raise WatchError(f"{entry_label} covered decision requires Tumoflip evidence")
                _require_sha(implementation.get("commit"), f"{entry_label}.tumoflip.commit")
                _require_release_name(
                    implementation.get("releaseTag"), f"{entry_label}.tumoflip.releaseTag"
                )
                acceptance = _require_string(
                    implementation.get("hardwareAcceptance"),
                    f"{entry_label}.tumoflip.hardwareAcceptance",
                )
                if acceptance not in HARDWARE_ACCEPTANCE:
                    raise WatchError(f"{entry_label}.tumoflip.hardwareAcceptance is invalid")
            elif implementation is not None:
                raise WatchError(f"{entry_label} non-covered decision cannot carry Tumoflip evidence")

        previous_through = through_commit

    if previous_through != reviewed_commit:
        raise WatchError("decision ledger does not end at the reviewed commit")
    return value


def _escape_markdown_text(value: Any, label: str) -> str:
    """Render upstream-controlled text as inert, non-mentioning Markdown."""

    text = _normalise_text(value, label)
    # Escaping Markdown punctuation does not stop GitHub from autolinking a
    # bare HTTP(S) URL.  In particular, a hostname such as ``evil%2eexample``
    # can become a clickable URL after URL normalization.  Break the URL
    # scheme before Markdown sees it; trusted GitHub URLs are constructed by
    # render_report and never pass through this untrusted-text renderer.
    text = BARE_HTTP_SCHEME.sub(rf"\1:{URL_BREAK}", text)
    text = text.replace("@", f"@{URL_BREAK}")
    return MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise WatchError(f"cannot load {path}: {error}") from error


def _read_object(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise WatchError(f"{path} must contain a JSON object")
    return value


def validate_contract(value: dict[str, Any]) -> dict[str, Any]:
    """Validate the immutable, human-reviewed upstream boundary."""

    schema = value.get("schema")
    if schema not in {1, 2, 3} or value.get("kind") != "upstreamWatch":
        raise WatchError("upstream watch contract schema is invalid")
    if value.get("repository") != "DarkFlippers/unleashed-firmware":
        raise WatchError("upstream watch repository is not allow-listed")
    if value.get("branch") != "dev":
        raise WatchError("upstream watch branch is not allow-listed")

    reviewed = value.get("reviewed")
    if not isinstance(reviewed, dict):
        raise WatchError("upstream watch reviewed boundary is missing")
    _require_sha(reviewed.get("commit"), "reviewed.commit")
    _parse_timestamp(reviewed.get("reviewedAt"), "reviewed.reviewedAt")

    release = reviewed.get("release")
    if not isinstance(release, dict):
        raise WatchError("reviewed release is missing")
    tag = _require_string(release.get("tag"), "reviewed.release.tag")
    if RELEASE_TAG.fullmatch(tag) is None:
        raise WatchError("reviewed release tag is invalid")
    _require_sha(release.get("commit"), "reviewed.release.commit")
    _parse_timestamp(release.get("publishedAt"), "reviewed.release.publishedAt")
    if schema in {2, 3}:
        _validate_decision_ledger(value.get("decisionLedger"), reviewed["commit"])
    elif "decisionLedger" in value:
        raise WatchError("schema 1 upstream watch cannot contain a decision ledger")
    if schema == 3:
        try:
            lifecycle = github_lifecycle.validate_policy(value.get("lifecycle"))
        except github_lifecycle.LifecycleError as error:
            raise WatchError(str(error)) from error
        if value.get("lifecycle") != lifecycle:
            raise WatchError("schema 3 lifecycle policy is not canonical")
    elif "lifecycle" in value:
        raise WatchError("only schema 3 upstream watch can contain lifecycle policy")
    return value


def load_contract(path: Path) -> dict[str, Any]:
    return validate_contract(_read_object(path))


def _gh_json(endpoint: str, *, paginate: bool = False) -> Any:
    command = ["gh", "api", endpoint]
    if paginate:
        command.extend(("--paginate", "--slurp"))
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise WatchError(f"GitHub API request failed for {endpoint}: {detail}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise WatchError(f"GitHub API response is not JSON for {endpoint}") from error


def _pages(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise WatchError(f"{label} paginated response is not an array")
    if not value:
        return []
    if all(isinstance(item, list) for item in value):
        return [entry for page in value for entry in page]
    if all(isinstance(item, dict) for item in value):
        return value
    raise WatchError(f"{label} paginated response is malformed")


def _canonical_issue_number(value: Any, *, expected_number: int | None = None) -> int:
    """Require the one issue the watcher is allowed to mutate.

    Marker text alone is deliberately not an authority signal: repository users
    can put it in an arbitrary issue.  The workflow may only touch the exact
    bot-authored, exact-title issue whose marker is the first body content.
    """

    if not isinstance(value, dict):
        raise WatchError("canonical issue response is invalid")
    number = value.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise WatchError("canonical issue number is invalid")
    if expected_number is not None:
        if (
            not isinstance(expected_number, int)
            or isinstance(expected_number, bool)
            or expected_number < 1
        ):
            raise WatchError("expected canonical issue number is invalid")
        if number != expected_number:
            raise WatchError("canonical issue number changed")
    if value.get("pull_request") is not None:
        raise WatchError("canonical issue is a pull request")
    if value.get("title") != ISSUE_TITLE:
        raise WatchError("canonical issue title differs")
    body = value.get("body")
    if not isinstance(body, str) or not body.startswith(ISSUE_MARKER):
        raise WatchError("canonical issue marker is not at the body start")
    author = value.get("user")
    if not isinstance(author, dict) or author.get("login") != ISSUE_AUTHOR:
        raise WatchError("canonical issue is not owned by github-actions[bot]")
    return number


def _normalise_canonical_issue_body(value: Any, label: str) -> str:
    """Keep only GitHub's line-ending and terminal-newline behavior flexible.

    GitHub's issue API can return a different count of terminal line feeds than
    the ``--body-file`` supplied to ``gh issue create`` or ``gh issue edit``.
    That is presentation behavior, not a change to the report. Everything
    else remains byte-significant after CRLF is represented as LF: internal
    line breaks, whitespace, and even bare carriage returns must still match.
    """

    if not isinstance(value, str):
        raise WatchError(f"{label} must be a string")
    return value.replace("\r\n", "\n").rstrip("\n")


def canonical_issue_body_matches(expected: Any, actual: Any) -> bool:
    """Compare a generated report with its GitHub issue body fail-closed."""

    return _normalise_canonical_issue_body(
        expected, "expected canonical issue body"
    ) == _normalise_canonical_issue_body(actual, "canonical issue body")


def resolve_canonical_issue_number(value: Any) -> int | None:
    """Find exactly one bot-owned canonical issue, ignoring external spoofs."""

    matches: list[int] = []
    for issue in _pages(value, "canonical issue"):
        try:
            number = _canonical_issue_number(issue)
        except WatchError:
            continue
        matches.append(number)
    if len(matches) > 1:
        raise WatchError("multiple bot-owned canonical issues were found")
    return matches[0] if matches else None


def _tag_commit(repository: str, tag: str) -> str:
    reference = _gh_json(f"repos/{repository}/git/ref/tags/{tag}")
    if not isinstance(reference, dict):
        raise WatchError(f"tag reference is invalid: {tag}")
    item = reference.get("object")
    if not isinstance(item, dict):
        raise WatchError(f"tag object is invalid: {tag}")
    object_type = item.get("type")
    commit = item.get("sha")
    if object_type == "tag":
        tag_object = _gh_json(f"repos/{repository}/git/tags/{_require_sha(commit, 'tag object SHA')}")
        if not isinstance(tag_object, dict) or not isinstance(tag_object.get("object"), dict):
            raise WatchError(f"annotated tag object is invalid: {tag}")
        item = tag_object["object"]
        object_type = item.get("type")
        commit = item.get("sha")
    if object_type != "commit":
        raise WatchError(f"tag does not resolve to a commit: {tag}")
    return _require_sha(commit, f"tag commit for {tag}")


def _published_releases(
    repository: str, reviewed_release_at: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    values = _pages(
        _gh_json(f"repos/{repository}/releases?per_page=100", paginate=True),
        "release",
    )
    releases: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    tags: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            raise WatchError("release record is invalid")
        if item.get("draft") is not False or item.get("prerelease") is not False:
            continue
        tag = _require_release_name(item.get("tag_name"), "release tag")
        if tag in tags:
            raise WatchError(f"published Unleashed release tag is duplicated: {tag}")
        tags.add(tag)
        published_at = _parse_timestamp(item.get("published_at"), f"release {tag} publishedAt")
        if RELEASE_TAG.fullmatch(tag) is None:
            # The repository has pre-unlshd historical releases.  They are out
            # of the current release stream, but a new nonstandard release is
            # never silently ignored after the reviewed boundary.
            if published_at > reviewed_release_at:
                unexpected.append(item)
            continue
        releases.append(item)
    if not releases:
        raise WatchError("no published Unleashed releases were returned")
    releases.sort(key=lambda item: _parse_timestamp(item["published_at"], "release publishedAt"), reverse=True)
    if len(releases) > 1 and releases[0]["published_at"] == releases[1]["published_at"]:
        raise WatchError("latest published Unleashed release is ambiguous")
    unexpected.sort(
        key=lambda item: _parse_timestamp(item["published_at"], "release publishedAt"),
        reverse=True,
    )
    return releases, unexpected


def _release_evidence(repository: str, release: dict[str, Any]) -> dict[str, str]:
    tag = _require_release_name(release.get("tag_name"), "release tag")
    return {
        "tag": tag,
        "commit": _tag_commit(repository, tag),
        "publishedAt": _timestamp(_parse_timestamp(release.get("published_at"), "release publishedAt")),
        "url": f"https://github.com/{repository}/releases/tag/{tag}",
    }


def _branch_evidence(repository: str, branch: str) -> str:
    repository_data = _gh_json(f"repos/{repository}")
    if not isinstance(repository_data, dict) or repository_data.get("default_branch") != branch:
        raise WatchError("upstream default branch differs from the reviewed contract")
    branch_data = _gh_json(f"repos/{repository}/branches/{branch}")
    if not isinstance(branch_data, dict) or not isinstance(branch_data.get("commit"), dict):
        raise WatchError("upstream branch response is invalid")
    return _require_sha(branch_data["commit"].get("sha"), f"upstream {branch} commit")


def _comparison(repository: str, baseline: str, branch: str, head: str) -> dict[str, Any]:
    response = _gh_json(f"repos/{repository}/compare/{baseline}...{branch}")
    if not isinstance(response, dict):
        raise WatchError("upstream comparison response is invalid")
    status = response.get("status")
    if status not in {"identical", "ahead", "behind", "diverged"}:
        raise WatchError("upstream comparison status is invalid")
    ahead = response.get("ahead_by")
    behind = response.get("behind_by")
    total = response.get("total_commits")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (ahead, behind, total)):
        raise WatchError("upstream comparison counts are invalid")
    base = response.get("base_commit")
    if not isinstance(base, dict) or _require_sha(base.get("sha"), "comparison base commit") != baseline:
        raise WatchError("upstream comparison base differs from reviewed boundary")
    response_head = response.get("head_commit")
    if response_head is not None:
        if not isinstance(response_head, dict) or _require_sha(response_head.get("sha"), "comparison head commit") != head:
            raise WatchError("upstream comparison head differs from branch evidence")
    elif status != "identical":
        # GitHub's compare endpoint can omit head_commit for a valid named
        # branch comparison (notably for diverged histories). The branch
        # endpoint above is independently resolved and is the authoritative
        # identity for the current head, so do not fail merely because the
        # duplicate compare-field is absent. Any non-null value is still
        # checked against that branch evidence above.
        pass
    if status == "identical" and (ahead != 0 or behind != 0 or head != baseline):
        raise WatchError("identical comparison is internally inconsistent")
    if status == "ahead" and (ahead < 1 or behind != 0):
        raise WatchError("forward comparison is internally inconsistent")
    if status == "behind" and (behind < 1 or ahead != 0):
        raise WatchError("backward comparison is internally inconsistent")
    if status == "diverged" and (ahead < 1 or behind < 1):
        raise WatchError("diverged comparison is internally inconsistent")

    raw_commits = response.get("commits")
    if not isinstance(raw_commits, list):
        raise WatchError("upstream comparison commit list is invalid")
    sample: list[dict[str, str]] = []
    for item in raw_commits:
        if not isinstance(item, dict) or not isinstance(item.get("commit"), dict):
            raise WatchError("upstream comparison commit entry is invalid")
        sample.append(
            {
                "sha": _require_sha(item.get("sha"), "comparison commit SHA"),
                "subject": _normalise_text(item["commit"].get("message"), "comparison commit subject").split("\n", 1)[0],
            }
        )
    if len(sample) > total:
        raise WatchError("upstream comparison returned too many commits")
    return {
        "status": status,
        "aheadBy": ahead,
        "behindBy": behind,
        "totalCommits": total,
        "commitSample": sample,
        "commitSampleTruncated": len(sample) != total,
        "url": f"https://github.com/{repository}/compare/{baseline}...{head}",
    }


def _decision_ledger_summary(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for batch in ledger:
        counts = {classification: 0 for classification in sorted(DECISION_CLASSIFICATIONS)}
        commits: list[str] = []
        for entry in batch["entries"]:
            counts[entry["classification"]] += 1
            commits.append(entry["upstreamCommit"])
        result.append(
            {
                "fromExclusive": batch["fromExclusive"],
                "throughInclusive": batch["throughInclusive"],
                "reviewedAt": batch["reviewedAt"],
                "entryCount": len(batch["entries"]),
                "classifications": counts,
                "commits": commits,
            }
        )
    return result


def _verify_decision_ledger_ranges(
    repository: str, ledger: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Prove that every advanced boundary has one explicit decision per commit."""

    for batch_index, batch in enumerate(ledger):
        base = batch["fromExclusive"]
        head = batch["throughInclusive"]
        response = _gh_json(f"repos/{repository}/compare/{base}...{head}")
        if not isinstance(response, dict):
            raise WatchError(f"decision ledger batch {batch_index} comparison is invalid")
        if response.get("status") != "ahead" or response.get("behind_by") != 0:
            raise WatchError(f"decision ledger batch {batch_index} is not a forward range")
        base_item = response.get("base_commit")
        head_item = response.get("head_commit")
        if not isinstance(base_item, dict) or _require_sha(
            base_item.get("sha"), "decision ledger comparison base"
        ) != base:
            raise WatchError(f"decision ledger batch {batch_index} range identity differs")
        if head_item is not None and (
            not isinstance(head_item, dict)
            or _require_sha(head_item.get("sha"), "decision ledger comparison head") != head
        ):
            raise WatchError(f"decision ledger batch {batch_index} range identity differs")
        raw_commits = response.get("commits")
        total = response.get("total_commits")
        if (
            not isinstance(raw_commits, list)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total != len(raw_commits)
        ):
            raise WatchError(f"decision ledger batch {batch_index} commit range is incomplete")
        exact_commits = [
            _require_sha(item.get("sha"), "decision ledger comparison commit")
            if isinstance(item, dict)
            else ""
            for item in raw_commits
        ]
        expected_commits = [entry["upstreamCommit"] for entry in batch["entries"]]
        if exact_commits != expected_commits or exact_commits[-1] != head:
            raise WatchError(f"decision ledger batch {batch_index} does not cover the exact range")
    return _decision_ledger_summary(ledger)


def _merged_pull_candidates(
    repository: str,
    branch: str,
    reviewed_at: datetime,
) -> list[dict[str, str | int]]:
    query = urlencode(
        {
            "q": f"repo:{repository} is:pr is:merged base:{branch} merged:>={reviewed_at:%Y-%m-%d}",
            "per_page": "100",
        }
    )
    pages = _gh_json(f"search/issues?{query}", paginate=True)
    if not isinstance(pages, list) or not all(isinstance(page, dict) for page in pages):
        raise WatchError("merged pull request search response is invalid")
    summaries: list[dict[str, Any]] = []
    for page in pages:
        total = page.get("total_count")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0 or total > 1000:
            raise WatchError("merged pull request search is incomplete")
        if page.get("incomplete_results") is not False:
            raise WatchError("merged pull request search is incomplete")
        items = page.get("items")
        if not isinstance(items, list):
            raise WatchError("merged pull request search items are invalid")
        for item in items:
            if not isinstance(item, dict):
                raise WatchError("merged pull request search item is invalid")
            summaries.append(item)

    result: list[dict[str, str | int]] = []
    numbers: set[int] = set()
    for summary in summaries:
        number = summary.get("number")
        pull = summary.get("pull_request")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1 or not isinstance(pull, dict):
            raise WatchError("merged pull request summary is invalid")
        merged_at = _parse_timestamp(pull.get("merged_at"), f"pull request {number} mergedAt")
        if merged_at <= reviewed_at:
            continue
        if number in numbers:
            raise WatchError(f"merged pull request is duplicated: {number}")
        numbers.add(number)
        detail = _gh_json(f"repos/{repository}/pulls/{number}")
        if not isinstance(detail, dict):
            raise WatchError(f"pull request detail is invalid: {number}")
        if detail.get("number") != number or detail.get("state") != "closed":
            raise WatchError(f"pull request identity changed: {number}")
        if _parse_timestamp(detail.get("merged_at"), f"pull request {number} mergedAt") != merged_at:
            raise WatchError(f"pull request merge time changed: {number}")
        base = detail.get("base")
        if not isinstance(base, dict) or base.get("ref") != branch:
            raise WatchError(f"pull request base branch differs: {number}")
        result.append(
            {
                "number": number,
                "title": _normalise_text(detail.get("title"), f"pull request {number} title"),
                "mergedAt": _timestamp(merged_at),
                "mergeCommit": _require_sha(
                    detail.get("merge_commit_sha"), f"pull request {number} merge commit"
                ),
                "url": f"https://github.com/{repository}/pull/{number}",
            }
        )
    result.sort(key=lambda item: (str(item["mergedAt"]), int(item["number"])))
    return result


def _merged_pulls_from_lifecycle(
    lifecycle: dict[str, Any], reviewed_at: datetime
) -> list[dict[str, str | int]]:
    """Preserve the existing merged-PR report while lifecycle adds all states."""

    result: list[dict[str, str | int]] = []
    for item in lifecycle["pullRequests"]:
        merged_at_value = item.get("mergedAt")
        if merged_at_value is None:
            continue
        merged_at = _parse_timestamp(
            merged_at_value, f"pull request {item.get('number')} mergedAt"
        )
        if merged_at <= reviewed_at:
            continue
        result.append(
            {
                "number": item["number"],
                "title": item["title"],
                "mergedAt": merged_at_value,
                "mergeCommit": item["mergeCommit"],
                "url": item["url"],
            }
        )
    result.sort(key=lambda item: (str(item["mergedAt"]), int(item["number"])))
    return result


def _lifecycle_unresolved(lifecycle: dict[str, Any] | None) -> list[str]:
    if lifecycle is None:
        return []
    unresolved: list[str] = []
    branch_status = lifecycle["branchChecks"]["status"]
    if branch_status not in {"passed", "notRequired"}:
        unresolved.append(
            f"Required checks for the exact upstream branch head are {branch_status}."
        )
    for item in lifecycle["pullRequests"]:
        if item["taskDisposition"] == "blocked":
            unresolved.append(
                f"Upstream pull request #{item['number']} is merged but blocked: "
                f"{item['taskReason']}."
            )
    return unresolved


def watch(
    *,
    contract: dict[str, Any],
    control_repository: str,
    control_commit: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect exact upstream evidence without modifying any remote source."""

    contract = validate_contract(contract)
    control_repository = _require_string(control_repository, "control repository")
    control_commit = _require_sha(control_commit, "control commit")
    repository = contract["repository"]
    branch = contract["branch"]
    reviewed = contract["reviewed"]
    baseline = reviewed["commit"]
    reviewed_release = reviewed["release"]
    reviewed_at = _parse_timestamp(reviewed["reviewedAt"], "reviewed.reviewedAt")
    decision_ledger = contract.get("decisionLedger", [])

    head = _branch_evidence(repository, branch)
    comparison = _comparison(repository, baseline, branch, head)
    decision_ledger_evidence = _verify_decision_ledger_ranges(repository, decision_ledger)
    releases, unexpected_release_records = _published_releases(
        repository,
        _parse_timestamp(reviewed_release["publishedAt"], "reviewed.release.publishedAt"),
    )
    release_by_tag = {item["tag_name"]: item for item in releases}
    historical_release = release_by_tag.get(reviewed_release["tag"])
    if historical_release is None:
        raise WatchError("reviewed upstream release no longer exists")
    if _release_evidence(repository, historical_release) != {
        **reviewed_release,
        "url": f"https://github.com/{repository}/releases/tag/{reviewed_release['tag']}",
    }:
        raise WatchError("reviewed upstream release identity changed")
    latest_release = _release_evidence(repository, releases[0])
    unexpected_releases = [
        _release_evidence(repository, release) for release in unexpected_release_records
    ]
    lifecycle: dict[str, Any] | None = None
    if contract["schema"] == 3:
        try:
            lifecycle = github_lifecycle.collect(
                repository=repository,
                branch=branch,
                since=reviewed_at,
                reviewed_commit=baseline,
                branch_head=head,
                release_commit=latest_release["commit"],
                policy=contract["lifecycle"],
                fetch=_gh_json,
            )
        except github_lifecycle.LifecycleError as error:
            raise WatchError(str(error)) from error
        pull_requests = _merged_pulls_from_lifecycle(lifecycle, reviewed_at)
    else:
        pull_requests = _merged_pull_candidates(repository, branch, reviewed_at)

    unresolved: list[str] = []
    if comparison["status"] in {"behind", "diverged"}:
        unresolved.append(
            "The reviewed commit is not a proven ancestor of the current upstream branch."
        )
    if unexpected_releases:
        unresolved.append(
            "A nonstandard published release appeared after the reviewed release boundary."
        )
    unresolved.extend(_lifecycle_unresolved(lifecycle))
    release_changed = (
        latest_release["tag"] != reviewed_release["tag"]
        or latest_release["commit"] != reviewed_release["commit"]
    )
    changes_detected = (
        comparison["status"] != "identical"
        or release_changed
        or bool(unexpected_releases)
        or bool(pull_requests)
    )
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise WatchError("watch timestamp must have a timezone")
    report = {
        "schema": 2 if lifecycle is not None else 1,
        "kind": "unleashedWatchReport",
        "generatedAt": _timestamp(timestamp),
        "control": {"repository": control_repository, "commit": control_commit},
        "watch": {
            "repository": repository,
            "branch": branch,
            "canonicalIssueMarker": ISSUE_MARKER,
            "canonicalIssueTitle": ISSUE_TITLE,
        },
        "reviewed": reviewed,
        "decisionLedger": decision_ledger_evidence,
        "current": {
            "branch": {
                "commit": head,
                "url": f"https://github.com/{repository}/commit/{head}",
            },
            "comparison": comparison,
            "latestRelease": latest_release,
            "unrecognizedPublishedReleases": unexpected_releases,
            "mergedPullRequests": pull_requests,
        },
        "changesDetected": changes_detected,
        "humanReviewRequired": changes_detected
        or (lifecycle is not None and lifecycle["reviewRequired"]),
        "unresolved": unresolved,
    }
    if lifecycle is not None:
        report["upstreamLifecycle"] = lifecycle
    return report


def render_report(report: dict[str, Any]) -> str:
    """Render stable issue text; generatedAt intentionally stays out of it."""

    watch_data = report["watch"]
    reviewed = report["reviewed"]
    current = report["current"]
    comparison = current["comparison"]
    release = current["latestRelease"]
    lines = [
        ISSUE_MARKER,
        "# Upstream watch: DarkFlippers/unleashed-firmware",
        "",
        "## Human-review boundary",
        "",
        "This workflow only reads the upstream public API and may write this issue. "
        "It cannot merge code, change Tumoflip pins, build firmware, publish a release, "
        "or advance the reviewed baseline.",
        "",
        "## Last human-reviewed baseline",
        "",
        f"- branch: `{watch_data['branch']}`",
        f"- commit: [`{reviewed['commit']}`](https://github.com/{watch_data['repository']}/commit/{reviewed['commit']})",
        f"- reviewed at: `{reviewed['reviewedAt']}`",
        f"- release: [`{reviewed['release']['tag']}`](https://github.com/{watch_data['repository']}/releases/tag/{reviewed['release']['tag']}) "
        f"at `{reviewed['release']['commit']}` (`{reviewed['release']['publishedAt']}`)",
        "",
    ]
    ledger = report.get("decisionLedger", [])
    if ledger:
        latest_batch = ledger[-1]
        counts = latest_batch["classifications"]
        lines.extend(
            (
                "## Recorded decision ledger",
                "",
                f"- exact range: `{latest_batch['fromExclusive']}` through `{latest_batch['throughInclusive']}`",
                f"- reviewed at: `{latest_batch['reviewedAt']}`",
                f"- decisions: `{latest_batch['entryCount']}` "
                f"(covered `{counts['covered']}`, issue-only `{counts['issueOnly']}`, metadata-only `{counts['metadataOnly']}`)",
                "",
            )
        )
    lines.extend(
        (
            "## Current exact upstream evidence",
            "",
            f"- branch head: [`{current['branch']['commit']}`]({current['branch']['url']})",
            f"- comparison: [`{comparison['status']}`]({comparison['url']}) "
            f"(`ahead={comparison['aheadBy']}`, `behind={comparison['behindBy']}`, `commits={comparison['totalCommits']}`)",
            f"- latest published release: [`{release['tag']}`]({release['url']}) "
            f"at `{release['commit']}` (`{release['publishedAt']}`)",
            "",
        )
    )
    commits = comparison["commitSample"]
    if commits:
        lines.extend(("## Unreviewed commit sample", ""))
        for item in commits:
            lines.append(
                f"- [`{item['sha']}`](https://github.com/{watch_data['repository']}/commit/{item['sha']}) — "
                f"{_escape_markdown_text(item['subject'], 'rendered commit subject')}"
            )
        if comparison["commitSampleTruncated"]:
            lines.append(
                "- The comparison API truncated the sample; use the comparison link above before deciding."
            )
        lines.append("")

    pull_requests = current["mergedPullRequests"]
    unexpected_releases = current["unrecognizedPublishedReleases"]
    lifecycle = report.get("upstreamLifecycle")
    if lifecycle is not None:
        summary = lifecycle["summary"]
        lines.extend(("## Upstream lifecycle and task eligibility", ""))
        milestone = lifecycle.get("milestone")
        if milestone is not None:
            lines.append(
                f"- tracked milestone: [#{milestone['number']}]({milestone['url']}) "
                f"**{milestone['state']}**, open `{milestone['openItems']}`, "
                f"closed `{milestone['closedItems']}` — "
                f"{_escape_markdown_text(milestone['title'], 'rendered milestone title')}"
            )
        lines.extend(
            (
                f"- exact branch checks: **{lifecycle['branchChecks']['status']}** "
                f"at `{lifecycle['branchChecks']['commit']}`",
                f"- pull requests: `{summary['pullRequests']}` "
                f"(eligible `{summary['eligible']}`, blocked `{summary['blocked']}`, "
                f"pending `{summary['pending']}`, deferred `{summary['deferred']}`, "
                f"declined `{summary['declined']}`)",
                f"- issues observed: `{summary['issues']}`; suppressed records: `{summary['suppressed']}`",
                "- only `eligible` pull requests may authorize creating a Tumoflip implementation task; "
                "open, draft, deferred, closed-without-merge, and issue-only records remain suppressed.",
                "",
            )
        )
        if lifecycle["pullRequests"]:
            lines.extend(("### Pull requests", ""))
            for item in lifecycle["pullRequests"]:
                lines.append(
                    f"- [#{item['number']}]({item['url']}) — "
                    f"**{item['outcome']}**, checks `{item['checks']['status']}`, "
                    f"mergeability `{item['mergeability']}`, milestone `{item['milestone']}`, "
                    f"release `{item['release']}`, task `{item['taskDisposition']}` "
                    f"(`{item['taskReason']}`) — "
                    f"{_escape_markdown_text(item['title'], 'rendered lifecycle pull request title')}"
                )
            lines.append("")
        if lifecycle["issues"]:
            lines.extend(("### Issues", ""))
            for item in lifecycle["issues"]:
                lines.append(
                    f"- [#{item['number']}]({item['url']}) — resolution "
                    f"`{item['resolution']}`, milestone `{item['milestone']}`, "
                    f"task `suppressed` — "
                    f"{_escape_markdown_text(item['title'], 'rendered lifecycle issue title')}"
                )
            lines.append("")
    else:
        lines.extend(("## Merged PR candidates after the reviewed boundary", ""))
        if pull_requests:
            for item in pull_requests:
                lines.append(
                    f"- [#{item['number']}]({item['url']}) `{item['mergeCommit']}` "
                    f"({item['mergedAt']}) — {_escape_markdown_text(item['title'], 'rendered pull request title')}"
                )
        else:
            lines.append("- None detected.")
        lines.append("")

    if unexpected_releases:
        lines.extend(("## Unrecognized published releases", ""))
        for item in unexpected_releases:
            lines.append(
                f"- [`{item['tag']}`]({item['url']}) at `{item['commit']}` "
                f"(`{item['publishedAt']}`)"
            )
        lines.append("")

    unresolved = report["unresolved"]
    if unresolved:
        lines.extend(("## Fail-closed condition", ""))
        lines.extend(f"- {item}" for item in unresolved)
        lines.append("")

    lines.extend(("## Required human action", ""))
    if report["humanReviewRequired"]:
        if lifecycle is not None:
            lines.extend(
                (
                    "- [ ] Review exact branch commits and only pull requests marked `eligible` against the Tumoflip `main` and `dev` baselines.",
                    "- [ ] Do not create a Tumoflip implementation task for `suppressed` records; upstream-declined work remains evidence only.",
                    "- [ ] Resolve `blocked` evidence before implementation; a merge with failed, missing, or contradictory checks is not accepted evidence.",
                    "- [ ] Keep upstream acceptance separate from Tumoflip build, API/C2, package, and physical-device acceptance.",
                    "- [ ] Advance `contracts/upstream-watchers.json` only in a separately reviewed control-plane PR after the ledger decision is recorded.",
                )
            )
        else:
            lines.extend(
                (
                    "- [ ] Review every commit and PR candidate against the Tumoflip `main` and `dev` baselines.",
                    "- [ ] Classify each candidate as covered, issue-only, deferred, or approved for a separate implementation task.",
                    "- [ ] Keep NFC/RF/API/protected-app work out of automatic integration and require its normal device-acceptance gate.",
                    "- [ ] Advance `contracts/upstream-watchers.json` only in a separately reviewed control-plane PR after the ledger decision is recorded.",
                )
            )
    else:
        lines.extend(
            (
                "- No unreviewed upstream change is detected.",
                "- The reviewed baseline remains unchanged; the watcher never advances it by itself.",
            )
        )
        if lifecycle is not None:
            lines.append(
                "- No upstream lifecycle record currently authorizes a new Tumoflip implementation task."
            )
    return "\n".join(lines) + "\n"


def verify_report(
    *,
    contract: dict[str, Any],
    report: dict[str, Any],
    markdown: str,
    control_repository: str,
    control_commit: str,
) -> None:
    """Verify an artifact before the separate issue-write privilege boundary."""

    contract = validate_contract(contract)
    expected_schema = 2 if contract["schema"] == 3 else 1
    if report.get("schema") != expected_schema or report.get("kind") != "unleashedWatchReport":
        raise WatchError("watch report schema is invalid")
    if report.get("control") != {
        "repository": _require_string(control_repository, "control repository"),
        "commit": _require_sha(control_commit, "control commit"),
    }:
        raise WatchError("watch report control identity differs")
    if report.get("watch") != {
        "repository": contract["repository"],
        "branch": contract["branch"],
        "canonicalIssueMarker": ISSUE_MARKER,
        "canonicalIssueTitle": ISSUE_TITLE,
    }:
        raise WatchError("watch report identity differs from contract")
    if report.get("reviewed") != contract["reviewed"]:
        raise WatchError("watch report reviewed baseline differs from contract")
    if report.get("decisionLedger") != _decision_ledger_summary(
        contract.get("decisionLedger", [])
    ):
        raise WatchError("watch report decision ledger differs from contract")
    _parse_timestamp(report.get("generatedAt"), "watch report generatedAt")
    if not isinstance(report.get("changesDetected"), bool) or not isinstance(
        report.get("humanReviewRequired"), bool
    ):
        raise WatchError("watch report review state is invalid")
    if report["changesDetected"] and not report["humanReviewRequired"]:
        raise WatchError("watch report cannot auto-clear a review requirement")
    if not isinstance(report.get("unresolved"), list) or not all(
        isinstance(item, str) and item for item in report["unresolved"]
    ):
        raise WatchError("watch report unresolved state is invalid")
    current = report.get("current")
    if not isinstance(current, dict):
        raise WatchError("watch report current state is invalid")
    branch = current.get("branch")
    comparison = current.get("comparison")
    release = current.get("latestRelease")
    unexpected_releases = current.get("unrecognizedPublishedReleases")
    pulls = current.get("mergedPullRequests")
    if (
        not all(isinstance(item, dict) for item in (branch, comparison, release))
        or not isinstance(unexpected_releases, list)
        or not isinstance(pulls, list)
    ):
        raise WatchError("watch report evidence is invalid")
    _require_sha(branch.get("commit"), "watch report branch commit")
    _require_sha(release.get("commit"), "watch report release commit")
    tag = _require_string(release.get("tag"), "watch report release tag")
    if RELEASE_TAG.fullmatch(tag) is None:
        raise WatchError("watch report release tag is invalid")
    _parse_timestamp(release.get("publishedAt"), "watch report release publishedAt")
    if branch.get("url") != f"https://github.com/{contract['repository']}/commit/{branch['commit']}":
        raise WatchError("watch report branch URL is invalid")
    if release.get("url") != f"https://github.com/{contract['repository']}/releases/tag/{tag}":
        raise WatchError("watch report release URL is invalid")
    if comparison.get("status") not in {"identical", "ahead", "behind", "diverged"}:
        raise WatchError("watch report comparison status is invalid")
    for key in ("aheadBy", "behindBy", "totalCommits"):
        if not isinstance(comparison.get(key), int) or isinstance(comparison[key], bool) or comparison[key] < 0:
            raise WatchError(f"watch report comparison {key} is invalid")
    sample = comparison.get("commitSample")
    if not isinstance(sample, list) or not isinstance(comparison.get("commitSampleTruncated"), bool):
        raise WatchError("watch report comparison sample is invalid")
    for item in sample:
        if not isinstance(item, dict):
            raise WatchError("watch report commit sample item is invalid")
        _require_sha(item.get("sha"), "watch report sampled commit")
        _normalise_text(item.get("subject"), "watch report sampled subject")
    if len(sample) > comparison["totalCommits"]:
        raise WatchError("watch report comparison sample exceeds total commits")
    if comparison["commitSampleTruncated"] != (len(sample) != comparison["totalCommits"]):
        raise WatchError("watch report comparison truncation state is invalid")
    baseline = contract["reviewed"]["commit"]
    branch_commit = branch["commit"]
    if comparison.get("url") != (
        f"https://github.com/{contract['repository']}/compare/{baseline}...{branch_commit}"
    ):
        raise WatchError("watch report comparison URL is invalid")
    status = comparison["status"]
    ahead = comparison["aheadBy"]
    behind = comparison["behindBy"]
    if status == "identical" and (ahead != 0 or behind != 0 or branch_commit != baseline):
        raise WatchError("watch report identical comparison is inconsistent")
    if status == "ahead" and (ahead < 1 or behind != 0):
        raise WatchError("watch report forward comparison is inconsistent")
    if status == "behind" and (behind < 1 or ahead != 0):
        raise WatchError("watch report backward comparison is inconsistent")
    if status == "diverged" and (ahead < 1 or behind < 1):
        raise WatchError("watch report diverged comparison is inconsistent")
    for item in unexpected_releases:
        if not isinstance(item, dict):
            raise WatchError("watch report unrecognized release is invalid")
        tag = _require_release_name(item.get("tag"), "watch report unrecognized release tag")
        _require_sha(item.get("commit"), "watch report unrecognized release commit")
        _parse_timestamp(item.get("publishedAt"), "watch report unrecognized release publishedAt")
        if item.get("url") != f"https://github.com/{contract['repository']}/releases/tag/{tag}":
            raise WatchError("watch report unrecognized release URL is invalid")
    for item in pulls:
        if not isinstance(item, dict):
            raise WatchError("watch report pull candidate is invalid")
        number = item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise WatchError("watch report pull candidate number is invalid")
        _require_sha(item.get("mergeCommit"), "watch report pull candidate merge commit")
        _parse_timestamp(item.get("mergedAt"), "watch report pull candidate mergedAt")
        _normalise_text(item.get("title"), "watch report pull candidate title")
        if item.get("url") != f"https://github.com/{contract['repository']}/pull/{number}":
            raise WatchError("watch report pull candidate URL is invalid")
    lifecycle = report.get("upstreamLifecycle")
    if contract["schema"] == 3:
        try:
            github_lifecycle.validate_evidence(
                lifecycle,
                repository=contract["repository"],
                branch_head=branch_commit,
                policy=contract["lifecycle"],
            )
        except github_lifecycle.LifecycleError as error:
            raise WatchError(str(error)) from error
        if lifecycle.get("since") != contract["reviewed"]["reviewedAt"]:
            raise WatchError("lifecycle evidence boundary differs from contract")
        expected_pulls = _merged_pulls_from_lifecycle(
            lifecycle,
            _parse_timestamp(
                contract["reviewed"]["reviewedAt"], "reviewed.reviewedAt"
            ),
        )
        if pulls != expected_pulls:
            raise WatchError("merged pull candidates differ from lifecycle evidence")
    elif lifecycle is not None:
        raise WatchError("legacy watch report cannot contain lifecycle evidence")
    expected_unresolved: list[str] = []
    if status in {"behind", "diverged"}:
        expected_unresolved.append(
            "The reviewed commit is not a proven ancestor of the current upstream branch."
        )
    if unexpected_releases:
        expected_unresolved.append(
            "A nonstandard published release appeared after the reviewed release boundary."
        )
    expected_unresolved.extend(_lifecycle_unresolved(lifecycle))
    if report["unresolved"] != expected_unresolved:
        raise WatchError("watch report fail-closed state differs from evidence")
    reviewed_release = contract["reviewed"]["release"]
    expected_changes = (
        status != "identical"
        or release["tag"] != reviewed_release["tag"]
        or release["commit"] != reviewed_release["commit"]
        or bool(unexpected_releases)
        or bool(pulls)
    )
    if report["changesDetected"] != expected_changes:
        raise WatchError("watch report change state differs from evidence")
    expected_review = expected_changes or (
        lifecycle is not None and lifecycle["reviewRequired"]
    )
    if report["humanReviewRequired"] != expected_review:
        raise WatchError("watch report review state differs from evidence")
    if markdown != render_report(report):
        raise WatchError("watch report Markdown differs from verified evidence")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("watch")
    collect.add_argument("--contract", type=Path, required=True)
    collect.add_argument("--control-repository", required=True)
    collect.add_argument("--control-commit", required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--markdown", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--contract", type=Path, required=True)
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--markdown", type=Path, required=True)
    verify.add_argument("--control-repository", required=True)
    verify.add_argument("--control-commit", required=True)
    resolve_issue = commands.add_parser("resolve-canonical-issue")
    resolve_issue.add_argument("--issues", type=Path, required=True)
    resolve_issue.add_argument("--output", type=Path, required=True)
    verify_issue = commands.add_parser("verify-canonical-issue")
    verify_issue.add_argument("--issue", type=Path, required=True)
    verify_issue.add_argument("--expected-number", type=int, required=True)
    compare_issue_body = commands.add_parser("compare-canonical-issue-body")
    compare_issue_body.add_argument("--expected", type=Path, required=True)
    compare_issue_body.add_argument("--issue", type=Path, required=True)
    compare_issue_body.add_argument("--output", type=Path, required=True)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "resolve-canonical-issue":
            _write_json(
                args.output,
                {"number": resolve_canonical_issue_number(_read_json(args.issues))},
            )
        elif args.command == "verify-canonical-issue":
            _canonical_issue_number(
                _read_object(args.issue), expected_number=args.expected_number
            )
        elif args.command == "compare-canonical-issue-body":
            try:
                expected = args.expected.read_text(encoding="utf-8")
            except OSError as error:
                raise WatchError(f"cannot read {args.expected}: {error}") from error
            issue = _read_object(args.issue)
            _write_json(
                args.output,
                {"matches": canonical_issue_body_matches(expected, issue.get("body"))},
            )
        else:
            contract = load_contract(args.contract)
            if args.command == "watch":
                report = watch(
                    contract=contract,
                    control_repository=args.control_repository,
                    control_commit=args.control_commit,
                )
                _write_json(args.output, report)
                args.markdown.write_text(render_report(report), encoding="utf-8")
            else:
                report = _read_object(args.report)
                try:
                    markdown = args.markdown.read_text(encoding="utf-8")
                except OSError as error:
                    raise WatchError(f"cannot read {args.markdown}: {error}") from error
                verify_report(
                    contract=contract,
                    report=report,
                    markdown=markdown,
                    control_repository=args.control_repository,
                    control_commit=args.control_commit,
                )
    except (WatchError, OSError, ValueError) as error:
        print(f"Unleashed watch failed closed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
