from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.protected_source_history import scan


class ProtectedSourceHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "community"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "History Test")
        self.source = self.repo / "non_catalog_apps/freq_analyzer_ext"
        self.source.mkdir(parents=True)
        (self.source / "application.fam").write_text("v1", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "initial source")
        self.commit_one = self.git("rev-parse", "HEAD")
        self.git("tag", "1jan2026")
        (self.source / "application.fam").write_text("v2", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "source update")
        self.commit_two = self.git("rev-parse", "HEAD")
        self.git("tag", "2jan2026")
        (self.repo / "README.md").write_text("metadata", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "metadata")
        self.commit_three = self.git("rev-parse", "HEAD")
        self.git("tag", "3jan2026")
        self.registry = self.root / "registry.json"
        self.releases = self.root / "releases.json"
        self._write_registry(self.commit_two)
        self.releases.write_text(
            json.dumps(
                [
                    {"tag": "1jan2026", "publishedAt": "2026-01-01T00:00:00Z"},
                    {"tag": "2jan2026", "publishedAt": "2026-01-02T00:00:00Z"},
                    {"tag": "3jan2026", "publishedAt": "2026-01-03T00:00:00Z"},
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def _write_registry(self, reviewed: str) -> None:
        self.registry.write_text(
            json.dumps(
                {
                    "schema": 2,
                    "apps": [
                        {
                            "id": "arf_frequency_analyzer",
                            "packSourcePath": "non_catalog_apps/freq_analyzer_ext",
                            "author": {
                                "ref": "release-source",
                                "lastReviewedCommit": reviewed,
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_changes_before_review_boundary_are_recorded_but_accepted(self) -> None:
        report = scan(
            registry_path=self.registry,
            releases_path=self.releases,
            community_repo=self.repo,
            community_head=self.commit_three,
            generated_at="2026-08-25T00:00:00+00:00",
        )
        self.assertEqual(report["overallStatus"], "verified")
        self.assertEqual(report["unresolved"], [])
        self.assertEqual(len(report["events"]), 2)
        self.assertFalse(report["events"][0]["requiresReview"])
        self.assertFalse(report["events"][1]["requiresReview"])

    def test_change_after_review_boundary_requires_review(self) -> None:
        self._write_registry(self.commit_one)
        report = scan(
            registry_path=self.registry,
            releases_path=self.releases,
            community_repo=self.repo,
            community_head=self.commit_three,
        )
        self.assertEqual(report["overallStatus"], "needsReview")
        self.assertEqual(report["unresolved"], ["arf_frequency_analyzer"])
        self.assertTrue(report["events"][1]["requiresReview"])

    def test_checkout_head_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "does not match expected latest commit"):
            scan(
                registry_path=self.registry,
                releases_path=self.releases,
                community_repo=self.repo,
                community_head="a" * 40,
            )


if __name__ == "__main__":
    unittest.main()
