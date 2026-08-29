from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from urllib.parse import urlencode

from tools import github_lifecycle as lifecycle


REPOSITORY = "example/upstream"
BRANCH = "dev"
REVIEWED = "a" * 40
HEAD = "b" * 40
RELEASE = "c" * 40
PULL_HEAD = "d" * 40
MERGE = HEAD
SINCE = datetime(2026, 8, 20, 8, 31, 45, tzinfo=timezone.utc)


def policy() -> dict[str, object]:
    return {
        "provider": "github",
        "taskPolicy": "accepted-only",
        "trackPullRequests": True,
        "trackIssues": True,
        "trackReleases": True,
        "requiredChecks": [{"kind": "checkRun", "name": "firmware"}],
    }


def milestone_policy() -> dict[str, object]:
    value = policy()
    value["milestone"] = {"number": 5, "title": "unlshd-093"}
    return value


def search_endpoint(kind: str) -> str:
    query = (
        f"repo:{REPOSITORY} is:{kind} "
        + (f"base:{BRANCH} " if kind == "pr" else "")
        + f"updated:>={SINCE:%Y-%m-%d}"
    )
    return f"search/issues?{urlencode({'q': query, 'per_page': '100'})}"


def check_run(
    *,
    run_id: int,
    conclusion: str,
    completed_at: str,
    status: str = "completed",
) -> dict[str, object]:
    return {
        "id": run_id,
        "name": "firmware",
        "status": status,
        "conclusion": conclusion,
        "started_at": "2026-08-21T08:00:00Z",
        "completed_at": completed_at,
        "details_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
    }


def compare(base: str, head: str, *, included: bool) -> dict[str, object]:
    return {
        "status": "ahead" if included else "behind",
        "ahead_by": 1 if included else 0,
        "behind_by": 0 if included else 1,
        "base_commit": {"sha": base},
        "head_commit": {"sha": head},
    }


class FakeGitHub:
    def __init__(
        self,
        *,
        pull: dict[str, object] | None = None,
        issue: dict[str, object] | None = None,
        branch_checks: list[dict[str, object]] | None = None,
        pull_checks: list[dict[str, object]] | None = None,
        reviews: list[dict[str, object]] | None = None,
        milestone: bool = False,
        search_pull: bool = True,
    ) -> None:
        pulls = [] if pull is None or not search_pull else [{"number": pull["number"]}]
        issues = [] if issue is None else [{"number": issue["number"]}]
        resolved_branch_checks = branch_checks if branch_checks is not None else [
            check_run(
                run_id=10,
                conclusion="success",
                completed_at="2026-08-21T08:05:00Z",
            )
        ]
        self.responses: dict[str, object] = {
            search_endpoint("pr"): [
                {
                    "total_count": len(pulls),
                    "incomplete_results": False,
                    "items": pulls,
                }
            ],
            search_endpoint("issue"): [
                {
                    "total_count": len(issues),
                    "incomplete_results": False,
                    "items": issues,
                }
            ],
            f"repos/{REPOSITORY}/commits/{HEAD}/check-runs?per_page=100&filter=latest": {
                "total_count": len(resolved_branch_checks),
                "check_runs": resolved_branch_checks,
            },
            f"repos/{REPOSITORY}/commits/{HEAD}/statuses?per_page=100": [[]],
        }
        if milestone:
            milestone_items: list[dict[str, object]] = []
            if pull is not None:
                milestone_items.append(
                    {
                        "number": pull["number"],
                        "pull_request": {"url": "https://example.invalid/pull"},
                        "milestone": {"number": 5, "title": "unlshd-093"},
                    }
                )
            if issue is not None:
                milestone_items.append(
                    {
                        "number": issue["number"],
                        "pull_request": None,
                        "milestone": {"number": 5, "title": "unlshd-093"},
                    }
                )
            open_items = sum(
                1
                for item in (pull, issue)
                if item is not None and item.get("state") == "open"
            )
            self.responses[f"repos/{REPOSITORY}/milestones/5"] = {
                "number": 5,
                "title": "unlshd-093",
                "state": "open",
                "open_issues": open_items,
                "closed_issues": len(milestone_items) - open_items,
                "updated_at": "2026-08-21T12:00:00Z",
                "closed_at": None,
                "due_on": None,
            }
            self.responses[
                f"repos/{REPOSITORY}/issues?milestone=5&state=all&per_page=100"
            ] = [milestone_items]
        if pull is not None:
            number = int(pull["number"])
            self.responses[f"repos/{REPOSITORY}/pulls/{number}"] = pull
            self.responses[f"repos/{REPOSITORY}/pulls/{number}/reviews?per_page=100"] = (
                reviews or []
            )
            pull_head = str(pull["head"]["sha"])
            runs = pull_checks or [
                check_run(
                    run_id=20,
                    conclusion="success",
                    completed_at="2026-08-21T09:05:00Z",
                )
            ]
            self.responses[
                f"repos/{REPOSITORY}/commits/{pull_head}/check-runs?per_page=100&filter=latest"
            ] = {"total_count": len(runs), "check_runs": runs}
            self.responses[
                f"repos/{REPOSITORY}/commits/{pull_head}/statuses?per_page=100"
            ] = [[]]
            if pull.get("merged_at") is not None:
                merge = str(pull["merge_commit_sha"])
                self.responses[f"repos/{REPOSITORY}/compare/{merge}...{REVIEWED}"] = compare(
                    merge, REVIEWED, included=False
                )
                self.responses[f"repos/{REPOSITORY}/compare/{merge}...{RELEASE}"] = compare(
                    merge, RELEASE, included=False
                )
        if issue is not None:
            self.responses[f"repos/{REPOSITORY}/issues/{issue['number']}"] = issue

    def __call__(self, endpoint: str, *, paginate: bool = False) -> object:
        if endpoint not in self.responses:
            raise AssertionError(f"unexpected endpoint: {endpoint}")
        return self.responses[endpoint]


def pull_detail(
    *,
    number: int = 42,
    state: str = "closed",
    merged: bool = True,
    draft: bool = False,
    updated_at: str = "2026-08-21T10:00:00Z",
    milestone: bool = False,
    mergeable: bool | None = None,
    mergeable_state: str | None = None,
) -> dict[str, object]:
    return {
        "number": number,
        "title": "NFC fix",
        "state": state,
        "draft": draft,
        "updated_at": updated_at,
        "closed_at": "2026-08-21T10:00:00Z" if state == "closed" else None,
        "merged_at": "2026-08-21T10:00:00Z" if merged else None,
        "merge_commit_sha": MERGE if merged else None,
        "base": {"ref": BRANCH},
        "head": {"sha": PULL_HEAD},
        "milestone": {"number": 5, "title": "unlshd-093"} if milestone else None,
        "mergeable": mergeable,
        "mergeable_state": mergeable_state,
    }


def collect(fake: FakeGitHub) -> dict[str, object]:
    return lifecycle.collect(
        repository=REPOSITORY,
        branch=BRANCH,
        since=SINCE,
        reviewed_commit=REVIEWED,
        branch_head=HEAD,
        release_commit=RELEASE,
        policy=policy(),
        fetch=fake,
    )


class GitHubLifecycleTests(unittest.TestCase):
    def test_merged_green_pull_is_the_only_task_eligible_state(self) -> None:
        fake = FakeGitHub(
            pull=pull_detail(),
            pull_checks=[
                check_run(
                    run_id=19,
                    conclusion="cancelled",
                    completed_at="2026-08-21T09:00:00Z",
                ),
                check_run(
                    run_id=20,
                    conclusion="success",
                    completed_at="2026-08-21T09:05:00Z",
                ),
            ],
            reviews=[
                {
                    "id": 1,
                    "user": {"login": "maintainer"},
                    "state": "APPROVED",
                    "submitted_at": "2026-08-21T09:30:00Z",
                }
            ],
        )

        report = collect(fake)

        candidate = report["pullRequests"][0]
        self.assertEqual(candidate["outcome"], "accepted")
        self.assertEqual(candidate["checks"]["status"], "passed")
        self.assertEqual(candidate["review"], "approved")
        self.assertEqual(candidate["taskDisposition"], "eligible")
        self.assertEqual(report["summary"]["eligible"], 1)
        self.assertTrue(report["reviewRequired"])
        lifecycle.validate_evidence(
            report,
            repository=REPOSITORY,
            branch_head=HEAD,
            policy=policy(),
        )

    def test_closed_without_merge_is_declined_and_never_creates_a_task(self) -> None:
        report = collect(
            FakeGitHub(pull=pull_detail(state="closed", merged=False))
        )

        candidate = report["pullRequests"][0]
        self.assertEqual(candidate["outcome"], "declined")
        self.assertEqual(candidate["taskDisposition"], "suppressed")
        self.assertEqual(candidate["taskReason"], "upstreamDeclined")
        self.assertEqual(report["summary"]["eligible"], 0)
        self.assertFalse(report["reviewRequired"])

    def test_open_green_pull_remains_pending_and_suppressed(self) -> None:
        report = collect(
            FakeGitHub(pull=pull_detail(state="open", merged=False))
        )

        candidate = report["pullRequests"][0]
        self.assertEqual(candidate["outcome"], "pending")
        self.assertEqual(candidate["checks"]["status"], "passed")
        self.assertEqual(candidate["taskDisposition"], "suppressed")
        self.assertFalse(report["reviewRequired"])

    def test_milestone_keeps_an_older_conflicting_pull_under_observation(self) -> None:
        fake = FakeGitHub(
            pull=pull_detail(
                state="open",
                merged=False,
                updated_at="2026-08-19T10:00:00Z",
                milestone=True,
                mergeable=False,
                mergeable_state="dirty",
            ),
            milestone=True,
            search_pull=False,
        )

        report = lifecycle.collect(
            repository=REPOSITORY,
            branch=BRANCH,
            since=SINCE,
            reviewed_commit=REVIEWED,
            branch_head=HEAD,
            release_commit=RELEASE,
            policy=milestone_policy(),
            fetch=fake,
        )

        self.assertEqual(report["milestone"]["pullRequests"], [42])
        candidate = report["pullRequests"][0]
        self.assertEqual(candidate["milestone"], "included")
        self.assertEqual(candidate["mergeability"], "conflicting")
        self.assertEqual(candidate["outcome"], "pending")
        self.assertEqual(candidate["taskDisposition"], "suppressed")
        self.assertFalse(report["reviewRequired"])
        lifecycle.validate_evidence(
            report,
            repository=REPOSITORY,
            branch_head=HEAD,
            policy=milestone_policy(),
        )

    def test_milestone_verifier_rejects_a_removed_tracked_candidate(self) -> None:
        fake = FakeGitHub(
            pull=pull_detail(state="open", merged=False, milestone=True),
            milestone=True,
            search_pull=False,
        )
        report = lifecycle.collect(
            repository=REPOSITORY,
            branch=BRANCH,
            since=SINCE,
            reviewed_commit=REVIEWED,
            branch_head=HEAD,
            release_commit=RELEASE,
            policy=milestone_policy(),
            fetch=fake,
        )
        tampered = copy.deepcopy(report)
        tampered["pullRequests"] = []
        tampered["summary"] = {
            "accepted": 0,
            "blocked": 0,
            "declined": 0,
            "deferred": 0,
            "eligible": 0,
            "pending": 0,
            "suppressed": 0,
            "issues": 0,
            "pullRequests": 0,
        }

        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "tracked milestone candidate listing is incomplete"
        ):
            lifecycle.validate_evidence(
                tampered,
                repository=REPOSITORY,
                branch_head=HEAD,
                policy=milestone_policy(),
            )

    def test_merged_pull_with_failed_required_check_is_blocked(self) -> None:
        report = collect(
            FakeGitHub(
                pull=pull_detail(),
                pull_checks=[
                    check_run(
                        run_id=20,
                        conclusion="failure",
                        completed_at="2026-08-21T09:05:00Z",
                    )
                ],
            )
        )

        candidate = report["pullRequests"][0]
        self.assertEqual(candidate["outcome"], "blocked")
        self.assertEqual(candidate["taskDisposition"], "blocked")
        self.assertEqual(candidate["taskReason"], "checksFailed")
        self.assertTrue(report["reviewRequired"])

    def test_merged_pull_waits_for_exact_branch_checks(self) -> None:
        report = collect(
            FakeGitHub(
                pull=pull_detail(),
                branch_checks=[
                    check_run(
                        run_id=10,
                        conclusion=None,
                        completed_at="",
                        status="in_progress",
                    )
                ],
            )
        )

        candidate = report["pullRequests"][0]
        self.assertEqual(candidate["outcome"], "blocked")
        self.assertEqual(candidate["taskDisposition"], "blocked")
        self.assertEqual(candidate["taskReason"], "branchChecksPending")
        self.assertTrue(report["reviewRequired"])

    def test_not_planned_issue_is_upstream_declined_and_suppressed(self) -> None:
        issue = {
            "number": 77,
            "title": "Rejected idea",
            "state": "closed",
            "state_reason": "not_planned",
            "updated_at": "2026-08-21T11:00:00Z",
            "pull_request": None,
        }

        report = collect(FakeGitHub(issue=issue))

        candidate = report["issues"][0]
        self.assertEqual(candidate["resolution"], "notPlanned")
        self.assertEqual(candidate["taskDisposition"], "suppressed")
        self.assertEqual(candidate["taskReason"], "upstreamDeclined")
        self.assertFalse(report["reviewRequired"])

    def test_completed_issue_alone_is_not_implementation_acceptance(self) -> None:
        issue = {
            "number": 78,
            "title": "Fixed elsewhere",
            "state": "closed",
            "state_reason": "completed",
            "updated_at": "2026-08-21T11:00:00Z",
            "pull_request": None,
        }

        report = collect(FakeGitHub(issue=issue))

        candidate = report["issues"][0]
        self.assertEqual(candidate["resolution"], "completed")
        self.assertEqual(candidate["taskReason"], "issueIsNotImplementationEvidence")
        self.assertFalse(report["reviewRequired"])

    def test_verifier_rejects_a_declined_pull_marked_eligible(self) -> None:
        report = collect(
            FakeGitHub(pull=pull_detail(state="closed", merged=False))
        )
        tampered = copy.deepcopy(report)
        tampered["pullRequests"][0]["taskDisposition"] = "eligible"
        tampered["summary"]["eligible"] = 1
        tampered["summary"]["suppressed"] = 0
        tampered["reviewRequired"] = True

        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "disposition differs from exact evidence"
        ):
            lifecycle.validate_evidence(
                tampered,
                repository=REPOSITORY,
                branch_head=HEAD,
                policy=policy(),
            )

    def test_verifier_rejects_eligible_pull_with_removed_check_evidence(self) -> None:
        report = collect(FakeGitHub(pull=pull_detail()))
        tampered = copy.deepcopy(report)
        tampered["pullRequests"][0]["checks"] = {
            "commit": PULL_HEAD,
            "status": "passed",
            "required": [],
        }

        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "requirements differ from policy"
        ):
            lifecycle.validate_evidence(
                tampered,
                repository=REPOSITORY,
                branch_head=HEAD,
                policy=policy(),
            )

    def test_verifier_rejects_passed_state_with_failed_raw_check_evidence(self) -> None:
        report = collect(FakeGitHub(pull=pull_detail()))
        tampered = copy.deepcopy(report)
        required = tampered["pullRequests"][0]["checks"]["required"][0]
        required["conclusion"] = "failure"

        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "state differs from raw evidence"
        ):
            lifecycle.validate_evidence(
                tampered,
                repository=REPOSITORY,
                branch_head=HEAD,
                policy=policy(),
            )


if __name__ == "__main__":
    unittest.main()
