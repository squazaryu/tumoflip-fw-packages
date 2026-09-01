from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tools import watch_unleashed as watcher


BASELINE = "240ba3db883cb0792b06c3445f9c38476e1dc5ec"
CHECKED_IN_BASELINE = "5629fc5e1758e58deb0a903c81d765a7d05e7fe8"
RELEASE = "3c9be0fdd9d301a9436765099a2d1780b36a1795"
CONTROL = "c" * 40
REPOSITORY = "DarkFlippers/unleashed-firmware"


def contract() -> dict[str, object]:
    return {
        "schema": 1,
        "kind": "upstreamWatch",
        "repository": REPOSITORY,
        "branch": "dev",
        "reviewed": {
            "commit": BASELINE,
            "reviewedAt": "2026-08-20T08:31:45Z",
            "release": {
                "tag": "unlshd-092",
                "commit": RELEASE,
                "publishedAt": "2026-08-21T23:00:27Z",
            },
        },
    }


def schema_three_contract() -> dict[str, object]:
    value = contract()
    value["schema"] = 3
    value["decisionLedger"] = [
        {
            "fromExclusive": "1" * 40,
            "throughInclusive": BASELINE,
            "reviewedAt": value["reviewed"]["reviewedAt"],
            "entries": [
                {
                    "upstreamCommit": BASELINE,
                    "classification": "metadataOnly",
                    "summary": "Reviewed boundary fixture.",
                }
            ],
        }
    ]
    value["lifecycle"] = {
        "provider": "github",
        "taskPolicy": "accepted-only",
        "trackPullRequests": True,
        "trackIssues": True,
        "trackReleases": False,
        "requiredChecks": [],
    }
    return value


def release(tag: str, commit: str, published_at: str) -> dict[str, object]:
    return {
        "tag_name": tag,
        "_test_commit": commit,
        "published_at": published_at,
        "draft": False,
        "prerelease": False,
    }


def issue(
    number: int,
    *,
    title: str = watcher.ISSUE_TITLE,
    body: str = watcher.ISSUE_MARKER + "\nmanaged by the watcher\n",
    author: str = watcher.ISSUE_AUTHOR,
    pull_request: object | None = None,
) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "body": body,
        "user": {"login": author},
        "pull_request": pull_request,
    }


class UnleashedWatcherTests(unittest.TestCase):
    def test_checked_in_contract_has_the_reviewed_unleashed_boundary(self) -> None:
        path = Path(__file__).resolve().parents[1] / "contracts/upstream-watchers.json"
        checked_in = watcher.load_contract(path)

        self.assertEqual(checked_in["repository"], REPOSITORY)
        self.assertEqual(checked_in["branch"], "dev")
        self.assertEqual(checked_in["schema"], 3)
        self.assertEqual(checked_in["lifecycle"]["taskPolicy"], "accepted-only")
        self.assertEqual(
            checked_in["lifecycle"]["milestone"],
            {"number": 5, "title": "unlshd-093"},
        )
        self.assertEqual(
            checked_in["lifecycle"]["requiredChecks"],
            [{"kind": "checkRun", "name": "f7 firmware"}],
        )
        self.assertEqual(checked_in["reviewed"]["commit"], CHECKED_IN_BASELINE)
        self.assertEqual(checked_in["reviewed"]["release"]["tag"], "unlshd-092")
        ledger = checked_in["decisionLedger"]
        self.assertEqual(len(ledger), 2)
        self.assertEqual(ledger[0]["fromExclusive"], BASELINE)
        self.assertEqual(
            ledger[0]["throughInclusive"],
            "469ff492613b7ffec6e4df2835d6dce4df115be4",
        )
        self.assertEqual(len(ledger[0]["entries"]), 10)
        self.assertEqual(
            {entry["classification"] for entry in ledger[0]["entries"]},
            {"covered", "metadataOnly"},
        )
        cuid = next(
            entry
            for entry in ledger[0]["entries"]
            if entry["upstreamCommit"]
            == "be559b2ee1b2bbae3eaf109a4a1b0d7337b3d8a8"
        )
        self.assertEqual(cuid["tumoflip"]["releaseTag"], "t-dev-008-003")
        self.assertEqual(cuid["tumoflip"]["hardwareAcceptance"], "pending")
        latest = ledger[1]
        self.assertEqual(
            latest["fromExclusive"],
            "469ff492613b7ffec6e4df2835d6dce4df115be4",
        )
        self.assertEqual(latest["throughInclusive"], CHECKED_IN_BASELINE)
        self.assertEqual(len(latest["entries"]), 9)
        self.assertEqual(
            {entry["classification"] for entry in latest["entries"]},
            {"covered", "metadataOnly"},
        )
        subghz = next(
            entry
            for entry in latest["entries"]
            if entry["upstreamCommit"] == CHECKED_IN_BASELINE
        )
        self.assertEqual(subghz["tumoflip"]["releaseTag"], "t-dev-008-010")

    def test_schema_two_rejects_a_ledger_that_skips_the_reviewed_commit(self) -> None:
        value = contract()
        value["schema"] = 2
        value["decisionLedger"] = [
            {
                "fromExclusive": "1" * 40,
                "throughInclusive": "2" * 40,
                "reviewedAt": "2026-08-28T13:59:53Z",
                "entries": [
                    {
                        "upstreamCommit": "2" * 40,
                        "classification": "metadataOnly",
                        "summary": "Metadata only.",
                    }
                ],
            }
        ]
        with self.assertRaisesRegex(watcher.WatchError, "does not end at the reviewed commit"):
            watcher.validate_contract(value)

    def test_schema_three_suppresses_declined_upstream_task(self) -> None:
        value = schema_three_contract()
        responses = self._responses()
        responses[f"repos/{REPOSITORY}/compare/{'1' * 40}...{BASELINE}"] = {
            "status": "ahead",
            "ahead_by": 1,
            "behind_by": 0,
            "total_commits": 1,
            "base_commit": {"sha": "1" * 40},
            "head_commit": {"sha": BASELINE},
            "commits": [{"sha": BASELINE}],
        }
        lifecycle_evidence = {
            "schema": 1,
            "provider": "github",
            "policy": value["lifecycle"],
            "since": value["reviewed"]["reviewedAt"],
            "branchChecks": {
                "commit": BASELINE,
                "status": "notRequired",
                "required": [],
            },
            "pullRequests": [
                {
                    "number": 99,
                    "title": "Declined upstream proposal",
                    "url": f"https://github.com/{REPOSITORY}/pull/99",
                    "updatedAt": "2026-08-21T10:00:00Z",
                    "closedAt": "2026-08-21T10:00:00Z",
                    "mergedAt": None,
                    "headCommit": "9" * 40,
                    "mergeCommit": None,
                    "lifecycle": "closedUnmerged",
                    "mergeable": None,
                    "mergeableState": None,
                    "mergeability": "notApplicable",
                    "milestone": "notTracked",
                    "review": "none",
                    "checks": {
                        "commit": "9" * 40,
                        "status": "notRequired",
                        "required": [],
                    },
                    "reviewedBoundary": "notIncluded",
                    "branch": "notIncluded",
                    "release": "notReleased",
                    "outcome": "declined",
                    "taskDisposition": "suppressed",
                    "taskReason": "upstreamDeclined",
                }
            ],
            "issues": [],
            "summary": {
                "accepted": 0,
                "blocked": 0,
                "declined": 1,
                "deferred": 0,
                "eligible": 0,
                "pending": 0,
                "suppressed": 1,
                "issues": 0,
                "pullRequests": 1,
            },
            "reviewRequired": False,
        }

        def fake(endpoint: str, *, paginate: bool = False) -> object:
            self.assertIn(endpoint, responses)
            return responses[endpoint]

        with mock.patch.object(watcher, "_gh_json", side_effect=fake), mock.patch.object(
            watcher.github_lifecycle, "collect", return_value=lifecycle_evidence
        ):
            report = watcher.watch(
                contract=value,
                control_repository="squazaryu/tumoflip-fw-packages",
                control_commit=CONTROL,
                now=datetime(2026, 8, 21, 11, tzinfo=timezone.utc),
            )

        self.assertEqual(report["schema"], 2)
        self.assertFalse(report["changesDetected"])
        self.assertFalse(report["humanReviewRequired"])
        self.assertEqual(report["upstreamLifecycle"]["summary"]["declined"], 1)
        self.assertIn("task `suppressed`", watcher.render_report(report))
        watcher.verify_report(
            contract=value,
            report=report,
            markdown=watcher.render_report(report),
            control_repository="squazaryu/tumoflip-fw-packages",
            control_commit=CONTROL,
        )

    def test_decision_ledger_live_range_requires_every_exact_commit(self) -> None:
        ledger = [
            {
                "fromExclusive": "1" * 40,
                "throughInclusive": "3" * 40,
                "reviewedAt": "2026-08-28T13:59:53Z",
                "entries": [
                    {
                        "upstreamCommit": "2" * 40,
                        "classification": "metadataOnly",
                        "summary": "Metadata only.",
                    },
                    {
                        "upstreamCommit": "3" * 40,
                        "classification": "issueOnly",
                        "issue": "https://github.com/squazaryu/tumoflip/issues/421",
                        "summary": "Needs isolated review.",
                    },
                ],
            }
        ]
        response = {
            "status": "ahead",
            "ahead_by": 2,
            "behind_by": 0,
            "total_commits": 2,
            "base_commit": {"sha": "1" * 40},
            "head_commit": None,
            "commits": [{"sha": "2" * 40}, {"sha": "3" * 40}],
        }
        with mock.patch.object(watcher, "_gh_json", return_value=response):
            summary = watcher._verify_decision_ledger_ranges(REPOSITORY, ledger)
        self.assertEqual(summary[0]["entryCount"], 2)
        self.assertEqual(summary[0]["classifications"]["issueOnly"], 1)

        response["commits"] = [{"sha": "3" * 40}, {"sha": "2" * 40}]
        with mock.patch.object(watcher, "_gh_json", return_value=response):
            with self.assertRaisesRegex(watcher.WatchError, "does not cover the exact range"):
                watcher._verify_decision_ledger_ranges(REPOSITORY, ledger)

    def _responses(
        self,
        *,
        head: str = BASELINE,
        status: str = "identical",
        ahead: int = 0,
        behind: int = 0,
        commits: list[dict[str, object]] | None = None,
        latest: dict[str, object] | None = None,
        search: list[dict[str, object]] | None = None,
        pull_details: dict[int, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        commits = commits if commits is not None else []
        latest = latest or release("unlshd-092", RELEASE, "2026-08-21T23:00:27Z")
        releases = [latest]
        if latest["tag_name"] != "unlshd-092":
            releases.append(release("unlshd-092", RELEASE, "2026-08-21T23:00:27Z"))
        responses: dict[str, object] = {
            f"repos/{REPOSITORY}": {"default_branch": "dev"},
            f"repos/{REPOSITORY}/branches/dev": {"commit": {"sha": head}},
            f"repos/{REPOSITORY}/compare/{BASELINE}...dev": {
                "status": status,
                "ahead_by": ahead,
                "behind_by": behind,
                "total_commits": len(commits),
                "base_commit": {"sha": BASELINE},
                "head_commit": None if status == "identical" else {"sha": head},
                "commits": commits,
            },
            f"repos/{REPOSITORY}/releases?per_page=100": [releases],
            f"repos/{REPOSITORY}/git/ref/tags/unlshd-092": {
                "object": {"type": "commit", "sha": RELEASE}
            },
        }
        if latest["tag_name"] != "unlshd-092":
            responses[f"repos/{REPOSITORY}/git/ref/tags/{latest['tag_name']}"] = {
                "object": {"type": "commit", "sha": latest["_test_commit"]}
            }
        search_query = watcher.urlencode(
            {
                "q": f"repo:{REPOSITORY} is:pr is:merged base:dev merged:>={contract()['reviewed']['reviewedAt'][:10]}",
                "per_page": "100",
            }
        )
        responses[f"search/issues?{search_query}"] = search if search is not None else []
        for number, detail in (pull_details or {}).items():
            responses[f"repos/{REPOSITORY}/pulls/{number}"] = detail
        return responses

    def _watch(self, responses: dict[str, object]) -> dict[str, object]:
        def fake(endpoint: str, *, paginate: bool = False) -> object:
            self.assertIn(endpoint, responses)
            return responses[endpoint]

        with mock.patch.object(watcher, "_gh_json", side_effect=fake):
            return watcher.watch(
                contract=contract(),
                control_repository="squazaryu/tumoflip-fw-packages",
                control_commit=CONTROL,
                now=datetime(2026, 8, 20, 9, tzinfo=timezone.utc),
            )

    def test_identical_boundary_requires_no_review_and_keeps_baseline(self) -> None:
        report = self._watch(self._responses())

        self.assertFalse(report["changesDetected"])
        self.assertFalse(report["humanReviewRequired"])
        self.assertEqual(report["reviewed"], contract()["reviewed"])
        self.assertEqual(report["current"]["comparison"]["status"], "identical")
        text = watcher.render_report(report)
        self.assertIn("No unreviewed upstream change is detected.", text)
        self.assertNotIn(report["generatedAt"], text)

    def test_forward_change_reports_exact_release_commits_and_pr_candidates(self) -> None:
        head = "a" * 40
        merge = "b" * 40
        responses = self._responses(
            head=head,
            status="ahead",
            ahead=1,
            commits=[{"sha": head, "commit": {"message": "fix: bounded NFC state\n\nbody"}}],
            latest=release("unlshd-093", "d" * 40, "2026-08-22T12:00:00Z"),
            search=[
                {
                    "total_count": 1,
                    "incomplete_results": False,
                    "items": [
                        {
                            "number": 1083,
                            "pull_request": {"merged_at": "2026-08-21T11:00:00Z"},
                        }
                    ],
                }
            ],
            pull_details={
                1083: {
                    "number": 1083,
                    "state": "closed",
                    "merged_at": "2026-08-21T11:00:00Z",
                    "base": {"ref": "dev"},
                    "merge_commit_sha": merge,
                    "title": "Fix NFC\nwithout Markdown injection",
                }
            },
        )

        report = self._watch(responses)

        self.assertTrue(report["changesDetected"])
        self.assertTrue(report["humanReviewRequired"])
        self.assertEqual(report["current"]["branch"]["commit"], head)
        self.assertEqual(report["current"]["latestRelease"]["tag"], "unlshd-093")
        self.assertEqual(report["current"]["mergedPullRequests"][0]["mergeCommit"], merge)
        text = watcher.render_report(report)
        self.assertIn("#1083", text)
        self.assertIn("Fix NFC without Markdown injection", text)
        self.assertIn("Advance `contracts/upstream-watchers.json` only", text)

    def test_forward_change_accepts_compare_without_duplicate_head_commit(self) -> None:
        head = "a" * 40
        responses = self._responses(
            head=head,
            status="ahead",
            ahead=1,
            commits=[{"sha": head, "commit": {"message": "fix: compare evidence"}}],
        )
        responses[f"repos/{REPOSITORY}/compare/{BASELINE}...dev"]["head_commit"] = None

        report = self._watch(responses)

        self.assertEqual(report["current"]["branch"]["commit"], head)
        self.assertEqual(report["current"]["comparison"]["status"], "ahead")

    def test_compare_rejects_a_present_head_commit_that_disagrees_with_branch(self) -> None:
        head = "a" * 40
        responses = self._responses(
            head=head,
            status="ahead",
            ahead=1,
            commits=[{"sha": head, "commit": {"message": "fix: compare evidence"}}],
        )
        responses[f"repos/{REPOSITORY}/compare/{BASELINE}...dev"]["head_commit"] = {
            "sha": "b" * 40
        }

        with self.assertRaisesRegex(watcher.WatchError, "comparison head differs"):
            self._watch(responses)

    def test_report_escapes_upstream_text_and_neutralizes_mentions(self) -> None:
        head = "a" * 40
        merge = "b" * 40
        subject = "fix [click](https://example.test) <tag> @octocat *bold* `code`"
        title = "PR [click](https://example.test) <tag> @octocat _italic_ ~strike~"
        report = self._watch(
            self._responses(
                head=head,
                status="ahead",
                ahead=1,
                commits=[{"sha": head, "commit": {"message": subject}}],
                search=[
                    {
                        "total_count": 1,
                        "incomplete_results": False,
                        "items": [
                            {
                                "number": 1084,
                                "pull_request": {"merged_at": "2026-08-21T11:00:00Z"},
                            }
                        ],
                    }
                ],
                pull_details={
                    1084: {
                        "number": 1084,
                        "state": "closed",
                        "merged_at": "2026-08-21T11:00:00Z",
                        "base": {"ref": "dev"},
                        "merge_commit_sha": merge,
                        "title": title,
                    }
                },
            )
        )

        text = watcher.render_report(report)

        self.assertNotIn("@octocat", text)
        self.assertIn("@\u200boctocat", text)
        self.assertNotIn("[click](https://example.test)", text)
        self.assertIn(f"\\[click\\]\\(https:{watcher.URL_BREAK}//example\\.test\\)", text)
        self.assertNotIn("<tag>", text)
        self.assertIn(r"\<tag\>", text)
        self.assertIn(r"\*bold\*", text)
        self.assertIn(r"\`code\`", text)
        self.assertIn(r"\_italic\_", text)
        self.assertIn(r"\~strike\~", text)
        watcher.verify_report(
            contract=contract(),
            report=report,
            markdown=text,
            control_repository="squazaryu/tumoflip-fw-packages",
            control_commit=CONTROL,
        )

    def test_report_neutralizes_percent_encoded_host_urls_without_touching_trusted_links(self) -> None:
        head = "a" * 40
        merge = "b" * 40
        untrusted_url = "https://evil%2eexample/review"
        report = self._watch(
            self._responses(
                head=head,
                status="ahead",
                ahead=1,
                commits=[{"sha": head, "commit": {"message": f"fix: inspect {untrusted_url}"}}],
                search=[
                    {
                        "total_count": 1,
                        "incomplete_results": False,
                        "items": [
                            {
                                "number": 1085,
                                "pull_request": {"merged_at": "2026-08-21T11:00:00Z"},
                            }
                        ],
                    }
                ],
                pull_details={
                    1085: {
                        "number": 1085,
                        "state": "closed",
                        "merged_at": "2026-08-21T11:00:00Z",
                        "base": {"ref": "dev"},
                        "merge_commit_sha": merge,
                        "title": f"Review {untrusted_url}",
                    }
                },
            )
        )

        text = watcher.render_report(report)

        neutralized_url = f"https:{watcher.URL_BREAK}//evil%2eexample/review"
        self.assertNotIn(untrusted_url, text)
        self.assertEqual(text.count(neutralized_url), 2)
        self.assertIn(
            f"[`{head}`](https://github.com/{REPOSITORY}/commit/{head})",
            text,
        )
        self.assertIn(
            f"[#1085](https://github.com/{REPOSITORY}/pull/1085)",
            text,
        )
        self.assertNotIn(
            f"https:{watcher.URL_BREAK}//github.com/{REPOSITORY}/commit/{head}",
            text,
        )
        watcher.verify_report(
            contract=contract(),
            report=report,
            markdown=text,
            control_repository="squazaryu/tumoflip-fw-packages",
            control_commit=CONTROL,
        )

    def test_external_spoofs_cannot_be_reconciled_or_block_canonical_creation(self) -> None:
        spoofed = [
            issue(41, author="external-user"),
            issue(42, title="External lookalike"),
            issue(43, body="prefix " + watcher.ISSUE_MARKER),
        ]

        self.assertIsNone(watcher.resolve_canonical_issue_number([spoofed]))
        for value in spoofed:
            with self.assertRaisesRegex(watcher.WatchError, "canonical issue"):
                watcher._canonical_issue_number(value)

        canonical = issue(44)
        self.assertEqual(
            watcher.resolve_canonical_issue_number([spoofed + [canonical]]), 44
        )
        with self.assertRaisesRegex(watcher.WatchError, "multiple bot-owned canonical"):
            watcher.resolve_canonical_issue_number([[canonical, issue(45)]])

    def test_issue_cli_revalidates_ownership_after_discovery_or_creation(self) -> None:
        spoofed = issue(51, author="external-user")
        canonical = issue(52)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pages_path = root / "issues.json"
            output_path = root / "canonical.json"
            issue_path = root / "issue.json"

            pages_path.write_text(json.dumps([[spoofed]]), encoding="utf-8")
            self.assertEqual(
                watcher.main(
                    [
                        "resolve-canonical-issue",
                        "--issues",
                        str(pages_path),
                        "--output",
                        str(output_path),
                    ]
                ),
                0,
            )
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), {"number": None})

            pages_path.write_text(json.dumps([[spoofed, canonical]]), encoding="utf-8")
            self.assertEqual(
                watcher.main(
                    [
                        "resolve-canonical-issue",
                        "--issues",
                        str(pages_path),
                        "--output",
                        str(output_path),
                    ]
                ),
                0,
            )
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), {"number": 52})

            issue_path.write_text(json.dumps(spoofed), encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(
                    watcher.main(
                        [
                            "verify-canonical-issue",
                            "--issue",
                            str(issue_path),
                            "--expected-number",
                            "51",
                        ]
                    ),
                    1,
                )
            issue_path.write_text(json.dumps(canonical), encoding="utf-8")
            self.assertEqual(
                watcher.main(
                    [
                        "verify-canonical-issue",
                        "--issue",
                        str(issue_path),
                        "--expected-number",
                        "52",
                    ]
                ),
                0,
            )

    def test_issue_body_comparator_allows_only_github_terminal_newlines(self) -> None:
        expected = watcher.ISSUE_MARKER + "\n# Canonical report\n"
        github_body = expected.replace("\n", "\r\n") + "\r\n"

        self.assertTrue(watcher.canonical_issue_body_matches(expected, github_body))
        self.assertFalse(
            watcher.canonical_issue_body_matches(
                expected, github_body.replace("Canonical", "Tampered")
            )
        )
        self.assertFalse(
            watcher.canonical_issue_body_matches(
                expected, github_body.replace("# Canonical", "\n# Canonical")
            )
        )
        self.assertFalse(
            watcher.canonical_issue_body_matches(expected, expected[:-1] + "\r")
        )
        with self.assertRaisesRegex(watcher.WatchError, "canonical issue body must be a string"):
            watcher.canonical_issue_body_matches(expected, None)

    def test_issue_body_cli_reports_tampering_after_a_github_terminal_newline(self) -> None:
        expected = watcher.ISSUE_MARKER + "\n# Canonical report\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected_path = root / "expected.md"
            issue_path = root / "issue.json"
            output_path = root / "comparison.json"
            expected_path.write_text(expected, encoding="utf-8")

            issue_path.write_text(
                json.dumps({"body": expected + "\n"}), encoding="utf-8"
            )
            self.assertEqual(
                watcher.main(
                    [
                        "compare-canonical-issue-body",
                        "--expected",
                        str(expected_path),
                        "--issue",
                        str(issue_path),
                        "--output",
                        str(output_path),
                    ]
                ),
                0,
            )
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")), {"matches": True}
            )

            issue_path.write_text(
                json.dumps({"body": expected.replace("Canonical", "Tampered") + "\n"}),
                encoding="utf-8",
            )
            self.assertEqual(
                watcher.main(
                    [
                        "compare-canonical-issue-body",
                        "--expected",
                        str(expected_path),
                        "--issue",
                        str(issue_path),
                        "--output",
                        str(output_path),
                    ]
                ),
                0,
            )
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")), {"matches": False}
            )

    def test_non_ancestor_boundary_stays_fail_closed(self) -> None:
        head = "e" * 40
        report = self._watch(
            self._responses(head=head, status="diverged", ahead=2, behind=1, commits=[])
        )

        self.assertTrue(report["changesDetected"])
        self.assertTrue(report["humanReviewRequired"])
        self.assertTrue(report["unresolved"])
        self.assertIn("not a proven ancestor", watcher.render_report(report))

    def test_new_nonstandard_release_is_reported_instead_of_silently_ignored(self) -> None:
        unknown_tag = "unx-candidate"
        unknown_commit = "9" * 40
        responses = self._responses()
        responses[f"repos/{REPOSITORY}/releases?per_page=100"] = [
            [
                release("unlshd-092", RELEASE, "2026-08-21T23:00:27Z"),
                release(unknown_tag, unknown_commit, "2026-08-22T12:00:00Z"),
            ]
        ]
        responses[f"repos/{REPOSITORY}/git/ref/tags/{unknown_tag}"] = {
            "object": {"type": "commit", "sha": unknown_commit}
        }

        report = self._watch(responses)

        self.assertTrue(report["changesDetected"])
        self.assertEqual(
            report["current"]["unrecognizedPublishedReleases"][0]["tag"], unknown_tag
        )
        self.assertIn("Unrecognized published releases", watcher.render_report(report))

    def test_retagged_reviewed_release_is_terminal(self) -> None:
        responses = self._responses()
        responses[f"repos/{REPOSITORY}/git/ref/tags/unlshd-092"] = {
            "object": {"type": "commit", "sha": "f" * 40}
        }
        with self.assertRaisesRegex(watcher.WatchError, "release identity changed"):
            self._watch(responses)

    def test_report_verification_rejects_tampered_markdown(self) -> None:
        report = self._watch(self._responses())
        markdown = watcher.render_report(report)
        watcher.verify_report(
            contract=contract(),
            report=report,
            markdown=markdown,
            control_repository="squazaryu/tumoflip-fw-packages",
            control_commit=CONTROL,
        )
        with self.assertRaisesRegex(watcher.WatchError, "Markdown differs"):
            watcher.verify_report(
                contract=contract(),
                report=report,
                markdown=markdown + "tampered\n",
                control_repository="squazaryu/tumoflip-fw-packages",
                control_commit=CONTROL,
            )

    def test_contract_rejects_an_unapproved_branch(self) -> None:
        invalid = copy.deepcopy(contract())
        invalid["branch"] = "main"
        with self.assertRaisesRegex(watcher.WatchError, "not allow-listed"):
            watcher.validate_contract(invalid)

    def test_cli_verify_checks_checked_in_contract_and_artifact(self) -> None:
        report = self._watch(self._responses())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract_path = root / "contract.json"
            report_path = root / "report.json"
            markdown_path = root / "report.md"
            contract_path.write_text(json.dumps(contract()), encoding="utf-8")
            report_path.write_text(json.dumps(report), encoding="utf-8")
            markdown_path.write_text(watcher.render_report(report), encoding="utf-8")
            self.assertEqual(
                watcher.main(
                    [
                        "verify",
                        "--contract",
                        str(contract_path),
                        "--report",
                        str(report_path),
                        "--markdown",
                        str(markdown_path),
                        "--control-repository",
                        "squazaryu/tumoflip-fw-packages",
                        "--control-commit",
                        CONTROL,
                    ]
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
