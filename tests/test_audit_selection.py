from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools import audit_release
from tools.tumoflip import protected_app_audit as audit_tool


class ExactAuditSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.ledger = json.loads((root / "audit/bootstrap/latest.json").read_text())
        self.requested = copy.deepcopy(self.ledger["audits"][0])

    def select(self, root: Path, *, requested: dict | None = None, issue: str | None = None,
               digest: str | None = None) -> dict:
        audit_tool.write_json(root / audit_release.LEDGER_ASSET, self.ledger)
        return audit_release.select_requested_audit(
            root=root,
            verified={
                "ledgerSHA256": digest or audit_release.sha256(root / audit_release.LEDGER_ASSET),
                "audit": self.ledger["audits"][-1],
            },
            requested=requested or self.requested,
            expected_issue_url=issue or self.requested["auditIssue"],
        )

    def test_noop_reuse_selects_requested_audit_not_release_bound_neighbor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            row = self.select(Path(temporary))
            self.assertEqual(row, self.requested)
            self.assertNotEqual(row["sourceTag"], self.ledger["audits"][-1]["sourceTag"])

    def test_modified_untrusted_request_cannot_change_pending_or_target_evidence(self) -> None:
        modified = copy.deepcopy(self.requested)
        modified["entries"][0]["note"] += " Unverified changes."
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(audit_release.AuditReleaseError):
                self.select(Path(temporary), requested=modified)

    def test_canonical_issue_must_match_requested_verified_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(audit_release.AuditReleaseError):
                self.select(Path(temporary), issue="https://github.com/squazaryu/tumoflip-fw-packages/issues/999")

    def test_changed_release_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(audit_release.AuditReleaseError):
                self.select(Path(temporary), digest="0" * 64)

    def test_unknown_requested_identity_is_rejected(self) -> None:
        modified = copy.deepcopy(self.requested)
        modified["sourceCommit"] = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(audit_release.AuditReleaseError):
                self.select(Path(temporary), requested=modified)


if __name__ == "__main__":
    unittest.main()
