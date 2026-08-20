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


BASELINE = "8bbf9fc09f58881145b2b7bacd50cbf2e407d78b"
RELEASE = "941f302d0377f3d8553df0a6628bf329e3e63941"
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
                "tag": "unlshd-091",
                "commit": RELEASE,
                "publishedAt": "2026-08-15T22:11:08Z",
            },
        },
    }


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
        self.assertEqual(checked_in["reviewed"]["commit"], BASELINE)
        self.assertEqual(checked_in["reviewed"]["release"]["tag"], "unlshd-091")

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
        latest = latest or release("unlshd-091", RELEASE, "2026-08-15T22:11:08Z")
        releases = [latest]
        if latest["tag_name"] != "unlshd-091":
            releases.append(release("unlshd-091", RELEASE, "2026-08-15T22:11:08Z"))
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
            f"repos/{REPOSITORY}/git/ref/tags/unlshd-091": {
                "object": {"type": "commit", "sha": RELEASE}
            },
        }
        if latest["tag_name"] != "unlshd-091":
            responses[f"repos/{REPOSITORY}/git/ref/tags/{latest['tag_name']}"] = {
                "object": {"type": "commit", "sha": latest["_test_commit"]}
            }
        search_query = watcher.urlencode(
            {
                "q": f"repo:{REPOSITORY} is:pr is:merged base:dev merged:>=2026-08-20",
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
            latest=release("unlshd-092", "d" * 40, "2026-08-21T12:00:00Z"),
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
        self.assertEqual(report["current"]["latestRelease"]["tag"], "unlshd-092")
        self.assertEqual(report["current"]["mergedPullRequests"][0]["mergeCommit"], merge)
        text = watcher.render_report(report)
        self.assertIn("#1083", text)
        self.assertIn("Fix NFC without Markdown injection", text)
        self.assertIn("Advance `contracts/upstream-watchers.json` only", text)

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
                release("unlshd-091", RELEASE, "2026-08-15T22:11:08Z"),
                release(unknown_tag, unknown_commit, "2026-08-21T12:00:00Z"),
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
        responses[f"repos/{REPOSITORY}/git/ref/tags/unlshd-091"] = {
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
