"""Monitoring repeats must not clear findings or emit repeated failure alerts."""
from __future__ import annotations

import copy
import importlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    "community-abi-audit.yml", "source-matrix-watcher.yml",
    "implementation-drift.yml", "protected-source-parity.yml",
    "protected-source-history.yml", "esp-installer-audit.yml",
)


class NotificationTests(unittest.TestCase):
    def api(self):
        self.assertTrue((ROOT / "tools/audit_notifications.py").is_file(),
                        "Missing semantic notification deduplication")
        return importlib.import_module("tools.audit_notifications")

    def report(self):
        return {"schema": 1, "status": "needsReview", "generatedAt": "today",
                "firmware": {"commit": "a" * 40},
                "findings": [{"missing_symbols": ["gps_rpc"], "count": 41}]}

    def test_workflows_do_not_unconditionally_fail_on_known_review(self):
        for name in WORKFLOWS:
            with self.subTest(name=name):
                text = (ROOT / ".github/workflows" / name).read_text()
                self.assertIn("tools/audit_notifications.py", text)
                self.assertNotIn("gh issue close", text)

    def test_only_top_level_generated_time_is_ignored(self):
        api = self.api()
        report = self.report()
        other = copy.deepcopy(report)
        other["generatedAt"] = "tomorrow"
        self.assertEqual(api.fingerprint(report), api.fingerprint(other))
        other["findings"][0]["generatedAt"] = "evidence-time"
        self.assertNotEqual(api.fingerprint(report), api.fingerprint(other))

    def test_new_commit_symbol_or_evidence_changes_fingerprint(self):
        api = self.api()
        report = self.report()
        for key, value in (("firmware", {"commit": "b" * 40}),
                           ("findings", [{"missing_symbols": ["different"]}]),
                           ("assets", [{"digest": "sha256:changed"}])):
            other = {**report, key: value}
            self.assertNotEqual(api.fingerprint(report), api.fingerprint(other))

    def test_unchanged_review_is_quiet_but_not_verified(self):
        api = self.api()
        report = self.report()
        self.assertFalse(api.must_fail(report, unchanged=True))
        self.assertEqual(report["status"], "needsReview")
        self.assertTrue(api.must_fail(report, unchanged=False))

    def test_errors_and_unavailable_sources_always_fail(self):
        api = self.api()
        for extra in ({"error": "network down"}, {"status": "rejected"},
                      {"watchers": [{"status": "unavailable"}]},
                      {"watchers": [{"error": "HTTP 503"}]}):
            self.assertTrue(api.must_fail({**self.report(), **extra}, unchanged=True))

    def test_invalid_or_conflicting_status_fails_closed(self):
        api = self.api()
        for report in ({}, {"status": "unknown"},
                       {"status": "verified", "overallStatus": "needsReview"}):
            with self.assertRaises(api.NotificationError):
                api.must_fail(report, unchanged=True)

    def test_verified_and_baseline_stale_do_not_need_failure(self):
        api = self.api()
        for status in ("verified", "baselineStale"):
            self.assertFalse(api.must_fail({"status": status}, unchanged=False))

    def test_legacy_history_heading_identifies_existing_bot_issue(self):
        api = self.api()
        marker = "<!-- protected-source-history -->"
        issue = {"number": 160, "title": "Review protected Community Pack source history",
                 "state": "open", "user": {"login": "github-actions[bot]"},
                 "body": "# Protected Community Pack source history\n\n" + marker + "\nold"}
        self.assertTrue(api.matches(issue, api.CONFIGS["history"]))
        for update in ({"user": {"login": "someone"}}, {"pull_request": {}},
                       {"title": "Unrelated issue"}, {"body": "text " + marker}):
            self.assertFalse(api.matches({**issue, **update}, api.CONFIGS["history"]))

    def test_receipt_is_bound_to_report_body_and_fingerprint(self):
        api = self.api()
        body = api.with_receipt("<!-- source-matrix-watch -->\nReport\n", self.report())
        self.assertTrue(api.same_report(body, self.report()))
        self.assertFalse(api.same_report(body.replace("Report", "Changed"), self.report()))
        self.assertFalse(api.same_report(body, {**self.report(), "extra": 1}))
        self.assertFalse(api.same_report("Report", self.report()))


if __name__ == "__main__":
    unittest.main()
