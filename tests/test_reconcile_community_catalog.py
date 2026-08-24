from __future__ import annotations

import unittest

from tools.reconcile_community_catalog import ReconciliationError, reconcile


def parity(status: str = "verified") -> dict:
    return {
        "schema": 1,
        "implementation": {"repository": "squazaryu/tumoflip", "commit": "a" * 40},
        "overallStatus": status,
        "apps": [{"appId": "proto_pirate"}],
    }


def ledger(status: str = "verified") -> dict:
    return {
        "schema": 2,
        "sourceRepository": "xMasterX/all-the-plugins",
        "audits": [{"sourceTag": "24aug2026", "sourceCommit": "b" * 40, "overallStatus": status}],
    }


class ReconcileCommunityCatalogTests(unittest.TestCase):
    def test_verified_inputs_are_ready_for_catalog_pr(self) -> None:
        report = reconcile(parity(), ledger(), generated_at="test")
        self.assertEqual(report["decision"], "readyForCatalogPR")
        self.assertEqual(report["comparison"]["identityFields"][0], "app_id")

    def test_any_unverified_input_requires_review(self) -> None:
        report = reconcile(parity("needsReview"), ledger(), generated_at="test")
        self.assertEqual(report["decision"], "needsReview")
        self.assertIn("parity", report["reviewReasons"][0])

    def test_stale_ledger_cannot_be_accepted_for_new_community_release(self) -> None:
        report = reconcile(
            parity(),
            ledger(),
            expected_source_tag="25aug2026",
            expected_source_commit="c" * 40,
        )
        self.assertEqual(report["decision"], "needsReview")
        self.assertEqual(len(report["reviewReasons"]), 2)

    def test_matching_community_identity_is_recorded(self) -> None:
        report = reconcile(
            parity(),
            ledger(),
            generated_at="test",
            expected_source_tag="24aug2026",
            expected_source_commit="b" * 40,
        )
        self.assertEqual(report["decision"], "readyForCatalogPR")
        self.assertEqual(report["community"]["commit"], "b" * 40)
        self.assertEqual(report["community"]["auditedCommit"], "b" * 40)

    def test_malformed_ledger_fails_closed(self) -> None:
        with self.assertRaises(ReconciliationError):
            reconcile(parity(), {"schema": 2, "sourceRepository": "wrong", "audits": []})
