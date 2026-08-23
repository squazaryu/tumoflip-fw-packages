from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.protected_source_parity import ParityError, scan


class ProtectedSourceParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "firmware"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Parity Test")
        for path in ("applications_user/proto", "applications_user/other"):
            target = self.repo / path
            target.mkdir(parents=True)
            (target / "application.fam").write_text(path, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.implementation_commit = self.git("rev-parse", "HEAD")
        self.registry = self.root / "registry.json"
        self.imports = self.root / "imports.json"
        self.fixtures = self.root / "heads.json"
        self.output = self.root / "report.json"
        self.markdown = self.root / "report.md"
        self._write_inputs()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def _write_inputs(self) -> None:
        first = "a" * 40
        second = "b" * 40
        registry = {
            "schema": 2,
            "apps": [
                {
                    "id": "proto",
                    "localSourcePath": "applications_user/proto",
                    "author": {
                        "repository": "https://example.invalid/proto.git",
                        "ref": "refs/heads/main",
                        "lastReviewedCommit": first,
                    },
                },
                {
                    "id": "other",
                    "localSourcePath": "applications_user/other",
                    "author": {
                        "repository": "https://example.invalid/other.git",
                        "ref": "refs/heads/main",
                        "lastReviewedCommit": second,
                    },
                },
            ],
        }
        imports = {
            "schema": 1,
            "implementation": {
                "repository": "squazaryu/tumoflip",
                "commit": self.implementation_commit,
            },
            "imports": [
                {
                    "appId": "proto",
                    "localSourcePath": "applications_user/proto",
                    "implementationCommit": self.implementation_commit,
                    "upstreamRepository": "https://example.invalid/proto.git",
                    "upstreamRef": "refs/heads/main",
                    "upstreamCommit": first,
                },
                {
                    "appId": "other",
                    "localSourcePath": "applications_user/other",
                    "implementationCommit": self.implementation_commit,
                    "upstreamRepository": "https://example.invalid/other.git",
                    "upstreamRef": "refs/heads/main",
                    "upstreamCommit": second,
                },
            ],
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        self.imports.write_text(json.dumps(imports), encoding="utf-8")
        self.fixtures.write_text(
            json.dumps(
                {
                    "heads": {
                        "https://example.invalid/proto.git refs/heads/main": first,
                        "https://example.invalid/other.git refs/heads/main": second,
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_matching_imports_are_verified(self) -> None:
        report = scan(
            registry_path=self.registry,
            imports_path=self.imports,
            implementation_repo=self.repo,
            community_commit=None,
            author_heads=self.fixtures,
            generated_at="2026-08-23T00:00:00+00:00",
        )
        self.assertEqual(report["overallStatus"], "verified")
        self.assertEqual(report["unresolved"], [])

    def test_upstream_change_cannot_be_hidden_by_unchanged_package_bytes(self) -> None:
        fixture = json.loads(self.fixtures.read_text(encoding="utf-8"))
        fixture["heads"]["https://example.invalid/proto.git refs/heads/main"] = "c" * 40
        self.fixtures.write_text(json.dumps(fixture), encoding="utf-8")
        report = scan(
            registry_path=self.registry,
            imports_path=self.imports,
            implementation_repo=self.repo,
            community_commit=None,
            author_heads=self.fixtures,
            generated_at="2026-08-23T00:00:00+00:00",
        )
        self.assertEqual(report["overallStatus"], "needsReview")
        self.assertEqual(report["unresolved"], ["proto"])

    def test_import_manifest_must_cover_registry_exactly(self) -> None:
        document = json.loads(self.imports.read_text(encoding="utf-8"))
        document["imports"].pop()
        self.imports.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ParityError, "import registry mismatch"):
            scan(
                registry_path=self.registry,
                imports_path=self.imports,
                implementation_repo=self.repo,
                community_commit=None,
                author_heads=self.fixtures,
                generated_at="2026-08-23T00:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()
