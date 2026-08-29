from __future__ import annotations

import unittest
import subprocess
from unittest.mock import patch

from tools import github_lifecycle
from tools.watch_sources import WatchError, fetch_head, scan


def contract() -> dict[str, object]:
    return {
        "schema": 1,
        "kind": "sourceWatchers",
        "policy": "review-only",
        "watchers": [
            {
                "id": "proto",
                "repository": "https://example.invalid/proto.git",
                "ref": "refs/heads/main",
                "reviewedCommit": "a" * 40,
                "relatedLocalPaths": ["applications_user/protopirate"],
            }
        ],
    }


def github_contract() -> dict[str, object]:
    return {
        "schema": 2,
        "kind": "sourceWatchers",
        "policy": "review-only",
        "watchers": [
            {
                "id": "arf",
                "repository": "https://github.com/example/upstream.git",
                "ref": "refs/heads/main",
                "reviewedCommit": "a" * 40,
                "relatedLocalPaths": ["applications_user/arf_tools"],
                "githubLifecycle": {
                    "repository": "example/upstream",
                    "branch": "main",
                    "reviewedAt": "2026-08-25T09:15:28Z",
                    "policy": {
                        "provider": "github",
                        "taskPolicy": "accepted-only",
                        "trackPullRequests": True,
                        "trackIssues": True,
                        "trackReleases": False,
                        "requiredChecks": [],
                    },
                },
            }
        ],
    }


def lifecycle_report(*, review_required: bool) -> dict[str, object]:
    return {
        "reviewRequired": review_required,
        "summary": {
            "eligible": 1 if review_required else 0,
            "blocked": 0,
            "pending": 0,
            "deferred": 0,
            "declined": 1 if not review_required else 0,
            "issues": 0,
        },
    }


class SourceWatcherTests(unittest.TestCase):
    def test_checked_in_github_source_has_accepted_only_lifecycle_policy(self) -> None:
        from pathlib import Path

        from tools.watch_sources import read_json, validate_contract

        path = Path(__file__).resolve().parents[1] / "contracts/source-watchers.json"
        watchers = validate_contract(read_json(path))
        arf = next(item for item in watchers if item["id"] == "arf-main")
        self.assertEqual(arf["githubLifecycle"]["repository"], "D4C1-Labs/Flipper-ARF")
        self.assertEqual(arf["githubLifecycle"]["policy"]["taskPolicy"], "accepted-only")
        self.assertEqual(
            arf["githubLifecycle"]["policy"]["requiredChecks"],
            [{"kind": "checkRun", "name": "build"}],
        )

    def test_matching_source_is_verified(self) -> None:
        report = scan(
            contract(),
            generated_at="2026-08-25T00:00:00+00:00",
            fetch=lambda repository, ref: "a" * 40,
        )
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["watchers"][0]["status"], "verified")

    def test_changed_source_requires_review(self) -> None:
        report = scan(contract(), fetch=lambda repository, ref: "b" * 40)
        self.assertEqual(report["status"], "needsReview")
        self.assertEqual(report["watchers"][0]["currentCommit"], "b" * 40)

    def test_unavailable_source_fails_closed(self) -> None:
        def fail(repository: str, ref: str) -> str:
            raise WatchError("network unavailable")

        report = scan(contract(), fetch=fail)
        self.assertEqual(report["status"], "needsReview")
        self.assertIn("network unavailable", report["watchers"][0]["error"])

    def test_fetch_timeout_fails_closed(self) -> None:
        timeout = subprocess.TimeoutExpired(["git", "ls-remote"], 45)
        with patch("tools.watch_sources.subprocess.run", side_effect=timeout):
            with self.assertRaisesRegex(WatchError, "timed out"):
                fetch_head("https://example.invalid/proto.git", "refs/heads/main")

    def test_declined_github_candidate_does_not_create_source_review_work(self) -> None:
        with patch(
            "tools.watch_sources.github_lifecycle.collect",
            return_value=lifecycle_report(review_required=False),
        ):
            report = scan(
                github_contract(),
                fetch=lambda repository, ref: "a" * 40,
            )

        self.assertEqual(report["status"], "verified")
        lifecycle_state = report["watchers"][0]["upstreamLifecycle"]
        self.assertEqual(lifecycle_state["capability"], "github")
        self.assertEqual(lifecycle_state["summary"]["declined"], 1)

    def test_eligible_github_candidate_requires_source_review(self) -> None:
        with patch(
            "tools.watch_sources.github_lifecycle.collect",
            return_value=lifecycle_report(review_required=True),
        ):
            report = scan(
                github_contract(),
                fetch=lambda repository, ref: "a" * 40,
            )

        self.assertEqual(report["status"], "needsReview")

    def test_github_lifecycle_outage_fails_closed(self) -> None:
        with patch(
            "tools.watch_sources.github_lifecycle.collect",
            side_effect=github_lifecycle.LifecycleError("API unavailable"),
        ):
            report = scan(
                github_contract(),
                fetch=lambda repository, ref: "a" * 40,
            )

        self.assertEqual(report["status"], "needsReview")
        lifecycle_state = report["watchers"][0]["upstreamLifecycle"]
        self.assertEqual(lifecycle_state["capability"], "unavailable")
        self.assertIn("API unavailable", lifecycle_state["error"])


if __name__ == "__main__":
    unittest.main()
