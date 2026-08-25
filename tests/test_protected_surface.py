from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.protected_surface import SurfaceError, scan


class ProtectedSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "firmware"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Surface Test")
        for path in ("applications_user/upstream", "applications_user/owned"):
            target = self.repo / path
            target.mkdir(parents=True)
            (target / "application.fam").write_text(
                'App(appid="%s")\n' % Path(path).name, encoding="utf-8"
            )
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.baseline = self.git("rev-parse", "HEAD")
        self.contract = self.root / "surface.json"
        self.registry = self.root / "registry.json"
        self._write_contract()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def _write_contract(self) -> None:
        self.contract.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "kind": "protectedSurface",
                    "firmwareRepository": "squazaryu/tumoflip",
                    "reviewedImplementations": {
                        "dev": {"ref": "refs/heads/dev", "commit": self.baseline},
                    },
                    "ownedSourcePaths": ["applications_user/owned"],
                    "reviewPrefixes": ["applications_user/"],
                }
            ),
            encoding="utf-8",
        )
        self.registry.write_text(
            json.dumps(
                {
                    "schema": 2,
                    "apps": [
                        {
                            "id": "upstream",
                            "localSourcePath": "applications_user/upstream",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_clean_surface_is_verified(self) -> None:
        report = scan(
            repo=self.repo,
            contract_path=self.contract,
            registry_path=self.registry,
            refs={"dev": "HEAD"},
            generated_at="2026-08-25T00:00:00+00:00",
        )
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["branches"][0]["status"], "verified")

    def test_changed_owned_source_requires_review(self) -> None:
        (self.repo / "applications_user/owned/app.c").write_text("change", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "owned change")
        report = scan(
            repo=self.repo,
            contract_path=self.contract,
            registry_path=self.registry,
            refs={"dev": "HEAD"},
        )
        self.assertEqual(report["status"], "needsReview")
        self.assertIn("applications_user/owned/app.c", report["branches"][0]["protectedChanges"])

    def test_new_application_root_is_not_silent(self) -> None:
        target = self.repo / "applications_user/new_app"
        target.mkdir(parents=True)
        (target / "application.fam").write_text('App(appid="new_app")\n', encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "new application")
        report = scan(
            repo=self.repo,
            contract_path=self.contract,
            registry_path=self.registry,
            refs={"dev": "HEAD"},
        )
        self.assertEqual(report["status"], "needsReview")
        self.assertEqual(report["branches"][0]["unclassifiedRoots"], ["applications_user/new_app"])

    def test_missing_baseline_fails_closed(self) -> None:
        document = json.loads(self.contract.read_text(encoding="utf-8"))
        document["reviewedImplementations"]["dev"]["commit"] = "a" * 40
        self.contract.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(SurfaceError, "baseline commit is unavailable"):
            scan(
                repo=self.repo,
                contract_path=self.contract,
                registry_path=self.registry,
                refs={"dev": "HEAD"},
            )

    def test_contract_rejects_unsafe_surface_path(self) -> None:
        document = json.loads(self.contract.read_text(encoding="utf-8"))
        document["ownedSourcePaths"] = ["applications_user/../escape"]
        self.contract.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(SurfaceError, "normalized repository-relative path"):
            scan(
                repo=self.repo,
                contract_path=self.contract,
                registry_path=self.registry,
                refs={"dev": "HEAD"},
            )

    def test_unavailable_audit_pin_fails_closed(self) -> None:
        targets = self.root / "targets.json"
        targets.write_text(
            json.dumps({"implementations": {"dev": {"commit": "b" * 40}}}),
            encoding="utf-8",
        )
        report = scan(
            repo=self.repo,
            contract_path=self.contract,
            registry_path=self.registry,
            refs={"dev": "HEAD"},
            targets_path=targets,
        )
        self.assertEqual(report["status"], "needsReview")
        self.assertEqual(report["auditPins"][0]["relation"], "unavailable")

    def test_ahead_audit_pin_fails_closed(self) -> None:
        (self.repo / "applications_user/upstream/new.c").write_text("future", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "future audit pin")
        future = self.git("rev-parse", "HEAD")
        targets = self.root / "targets.json"
        targets.write_text(
            json.dumps({"implementations": {"dev": {"commit": future}}}),
            encoding="utf-8",
        )
        report = scan(
            repo=self.repo,
            contract_path=self.contract,
            registry_path=self.registry,
            refs={"dev": self.baseline},
            targets_path=targets,
        )
        self.assertEqual(report["status"], "needsReview")
        self.assertEqual(report["auditPins"][0]["relation"], "ahead")


if __name__ == "__main__":
    unittest.main()
