from __future__ import annotations

import unittest
import subprocess
from unittest.mock import patch

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


class SourceWatcherTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
