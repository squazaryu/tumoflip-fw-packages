from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_bootstrap import BootstrapError, validate_index, verify
from tools.audit_inputs import InputError, validate_source_fixture, validate_targets
from tools.audit_release import AuditReleaseError, validate_evidence
from tools.publish_audit_branch import BranchError, prepare_tree
from tools.resolve_audit_source import resolve
from tools.tumoflip import protected_app_audit as audit_tool


class AuditControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_checked_in_bootstrap_is_strict_and_content_addressed(self) -> None:
        index = verify(
            index_path=self.root / "audit/bootstrap/index.json",
            normalized_latest=self.root / "audit/bootstrap/latest.json",
            legacy_root=None,
        )
        self.assertEqual(index["legacy"]["commit"], "a95ee7dc6d8add5e5f4b25e7abbb426634fd0dca")
        self.assertEqual(index["legacy"]["tree"], "0d8fea0355141001ca6cb234189b2eb4d5c7f1ac")
        self.assertEqual(
            index["legacy"]["historyTree"],
            "f688ddab98827bf5025beeef4d5c7144a525784e",
        )
        self.assertEqual(len(index["legacy"]["history"]), 11)
        self.assertEqual(
            sum(not item["clientStrict"] for item in index["legacy"]["history"]),
            4,
        )

    def test_bootstrap_classification_cannot_hide_legacy_generator_snapshot(self) -> None:
        index = json.loads((self.root / "audit/bootstrap/index.json").read_text())
        index["clientContract"]["legacyGeneratorSnapshots"].pop()
        with self.assertRaisesRegex(BootstrapError, "classification differs"):
            validate_index(index)

    def test_target_contract_pins_mirror_and_legacy_release_ids(self) -> None:
        contract = validate_targets(
            json.loads((self.root / "contracts/protected-audit-targets.json").read_text())
        )
        packages = {item["releaseTag"]: item for item in contract["packages"]}
        self.assertEqual(
            packages["fw-packages-stable-001"]["repository"],
            "squazaryu/tumoflip-fw-packages",
        )
        self.assertNotEqual(
            packages["fw-packages-stable-001"]["tagCommit"],
            packages["fw-packages-stable-001"]["manifestSourceCommit"],
        )
        self.assertEqual(
            packages["fw-packages-dev-005"]["repository"], "squazaryu/tumoflip"
        )
        self.assertIn("fw-packages-dev-007", packages)
        self.assertIn("fw-packages-dev-008", packages)

    def test_target_contract_rejects_duplicate_release_identity(self) -> None:
        contract = json.loads((self.root / "contracts/protected-audit-targets.json").read_text())
        contract["packages"].append(copy.deepcopy(contract["packages"][0]))
        with self.assertRaisesRegex(InputError, "duplicated"):
            validate_targets(contract)

    def test_e2e_fixture_is_exact_schema_two_source_input(self) -> None:
        fixture = validate_source_fixture(
            json.loads((self.root / "contracts/protected-audit-e2e-12aug.json").read_text())
        )
        self.assertEqual(fixture["current"]["releaseTag"], "12aug2026")
        self.assertEqual(fixture["current"]["tagCommit"], "585b144ac5b4d9a48a0e5a74570a6584353fdbba")
        self.assertEqual(fixture["canonicalIssue"]["number"], 308)

    def test_release_evidence_accepts_mirror_tag_distinct_from_source(self) -> None:
        evidence = {
            "schema": 1,
            "kind": "protectedAppAuditEvidence",
            "control": {"repository": "squazaryu/tumoflip-fw-packages", "commit": "a" * 40},
            "implementation": {"repository": "squazaryu/tumoflip", "commit": "b" * 40},
            "community": {
                "repository": audit_tool.SOURCE_REPOSITORY,
                "tag": "12aug2026",
                "commit": "c" * 40,
                "archives": {
                    "base": {"fileName": "all-the-apps-base.zip", "sha256": "d" * 64},
                    "extra": {"fileName": "all-the-apps-extra.zip", "sha256": "e" * 64},
                },
            },
            "issue": {"number": 1, "url": "https://github.com/squazaryu/tumoflip-fw-packages/issues/1"},
            "packages": [
                {
                    "repository": "squazaryu/tumoflip-fw-packages",
                    "releaseTag": "fw-packages-stable-001",
                    "githubReleaseId": 10,
                    "tagCommit": "f" * 40,
                    "sourceCommit": "1" * 40,
                    "manifestReleaseId": "2" * 64,
                    "manifestSHA256": "3" * 64,
                    "archiveSHA256": "4" * 64,
                    "packageRelease": {
                        "catalog_release_tag": "fw-packages-stable-001",
                        "source_commit": "1" * 40,
                    },
                }
            ],
            "firmware": [
                {
                    "repository": "squazaryu/tumoflip",
                    "releaseTag": "v1.0.4",
                    "githubReleaseId": 20,
                    "tagCommit": "5" * 40,
                    "updaterSHA256": "6" * 64,
                    "resourceManifestSHA256": "7" * 64,
                    "resourcesSHA256": "8" * 64,
                }
            ],
        }
        validate_evidence(evidence)
        evidence["packages"][0]["packageRelease"]["source_commit"] = "9" * 40
        with self.assertRaisesRegex(AuditReleaseError, "provenance differs"):
            validate_evidence(evidence)

    def test_prepare_tree_is_idempotent_for_existing_semantic_audit(self) -> None:
        ledger = json.loads((self.root / "audit/bootstrap/latest.json").read_text())
        audit = ledger["audits"][-1]
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            (tree / "history").mkdir()
            (tree / "latest.json").write_text(
                json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            (tree / "history/existing.json").write_text(
                json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            audit_path = tree / "audit.json"
            audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
            released = tree / "released.json"
            normalized = audit_tool.merge_ledger(ledger, audit)
            audit_tool.write_json(released, normalized)
            self.assertEqual(
                prepare_tree(root=tree, audit_path=audit_path, released_ledger=released),
                [],
            )

    def test_prepare_tree_rejects_release_raw_divergence(self) -> None:
        ledger = json.loads((self.root / "audit/bootstrap/latest.json").read_text())
        audit = ledger["audits"][-1]
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            (tree / "history").mkdir()
            audit_tool.write_json(tree / "latest.json", ledger)
            audit_tool.write_json(tree / "audit.json", audit)
            (tree / "released.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(BranchError, "differs"):
                prepare_tree(
                    root=tree,
                    audit_path=tree / "audit.json",
                    released_ledger=tree / "released.json",
                )


if __name__ == "__main__":
    unittest.main()
