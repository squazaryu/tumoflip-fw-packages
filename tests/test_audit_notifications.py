"""Monitoring repeats must not clear findings or emit repeated failure alerts."""
from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
                      {"apps": [{"error": "TLS failure", "upstream": {"currentCommit": None}}]},
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

    def test_source_resolution_distinguishes_missing_repository_from_network_failure(self):
        from tools import protected_source_parity as parity
        cases = [(0, "", parity.ParityError),
                 (128, "remote: Repository not found.", parity.ParityError),
                 (128, "TLS connection failed", parity.OperationalSourceError)]
        for code, stderr, expected in cases:
            with patch.object(parity.subprocess, "run", return_value=subprocess.CompletedProcess([], code, "", stderr)):
                with self.assertRaises(expected) as caught:
                    parity.fetch_head("https://example.invalid/repo.git", "refs/heads/main", {})
                self.assertEqual(isinstance(caught.exception, parity.OperationalSourceError), expected is parity.OperationalSourceError)
        with patch.object(parity.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 45)):
            with self.assertRaises(parity.OperationalSourceError):
                parity.fetch_head("https://example.invalid/repo.git", "refs/heads/main", {})

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

    def test_reconcile_new_repeat_change_recovery_and_reopen(self):
        api = self.api()
        fake = FakeGitHub()
        report = {**self.report(), "kind": "sourceWatchReport"}
        body = "<!-- source-matrix-watch -->\nReport\n"
        def run(value):
            return api.reconcile("source", value, body, scan_exit=int(value["status"] == "needsReview"), call=fake)
        self.assertTrue(run(report)["failure"])
        self.assertEqual(len(fake.issues), 1)
        writes = fake.writes
        self.assertFalse(run({**report, "generatedAt": "later"})["failure"])
        self.assertEqual(fake.writes, writes)
        self.assertTrue(run({**report, "firmware": {"commit": "b" * 40}})["failure"])
        self.assertFalse(run({**report, "status": "verified"})["failure"])
        self.assertEqual(fake.issues[0]["state"], "closed")
        writes = fake.writes
        run({**report, "status": "verified"})
        self.assertEqual(fake.writes, writes)
        self.assertTrue(run(report)["failure"])
        self.assertEqual(fake.issues[0]["state"], "open")
        self.assertEqual(fake.comments, [])

    def test_repeated_error_still_fails_without_rewriting_identical_issue(self):
        api = self.api()
        fake = FakeGitHub()
        report = {**self.report(), "kind": "sourceWatchReport", "error": "HTTP 503"}
        for attempt in range(2):
            self.assertTrue(api.reconcile("source", report, "<!-- source-matrix-watch -->\nError",
                                         scan_exit=1, call=fake)["failure"])
        self.assertEqual(fake.writes, 1)

    def test_esp_updates_one_bot_comment_without_touching_human_body(self):
        api = self.api()
        fake = FakeGitHub()
        fake.issues = [{"number": 21, "state": "open", "body": "human evidence", "user": {"login": "owner"}}]
        report = {"schema": 1, "kind": "espInstallerAuditReport", "status": "needsReview",
                  "automaticFlashPackageAuthorization": False}
        def run(value):
            return api.reconcile("esp", value, "<!-- esp-installer-audit -->\nEvidence", tracking_issue=21,
                                 scan_exit=int(value["status"] != "verified"), call=fake)
        self.assertTrue(run(report)["failure"])
        self.assertFalse(run(report)["failure"])
        self.assertEqual(fake.writes, 1)
        self.assertEqual(fake.issues[0]["body"], "human evidence")
        accepted = {**report, "status": "verified", "automaticFlashPackageAuthorization": True}
        self.assertFalse(run(accepted)["failure"])
        self.assertEqual(fake.issues[0]["state"], "closed")
        self.assertTrue(run(report)["failure"])
        self.assertEqual(fake.issues[0]["state"], "open")
        self.assertEqual(len(fake.comments), 1)
        fake.comments.append(copy.deepcopy(fake.comments[0]))
        with self.assertRaisesRegex(api.NotificationError, "Multiple canonical"):
            run(report)

    def test_history_reuses_legacy_issue_instead_of_creating_another(self):
        api = self.api()
        fake = FakeGitHub()
        fake.issues = [{"number": 160, "state": "open", "title": api.CONFIGS["history"][0],
                        "user": {"login": api.BOT},
                        "body": "# Protected Community Pack source history\n\n<!-- protected-source-history -->\nold"}]
        report = {"schema": 1, "overallStatus": "needsReview", "apps": []}
        result = api.reconcile("history", report, fake.issues[0]["body"], scan_exit=1, call=fake)
        self.assertEqual(result["issue"], 160)
        self.assertEqual(len(fake.issues), 1)

    def test_dry_run_never_writes_and_invalid_reports_never_call_github(self):
        api = self.api()
        fake = FakeGitHub()
        report = {**self.report(), "kind": "communityPackAbiAudit"}
        api.reconcile("abi", report, "<!-- community-pack-abi-audit -->\nReport", scan_exit=1, call=fake, dry_run=True)
        self.assertEqual(fake.writes, 0)
        for mode, document, code in (("abi", report, 0), ("abi", {**report, "kind": "wrong"}, 1),
                                     ("abi", {**report, "schema": True}, 1),
                                     ("abi", {**report, "schema": 99}, 1),
                                     ("abi", {**report, "status": "baselineStale"}, 0),
                                     ("abi", report, 2),
                                     ("parity", {"schema": 1, "status": "needsReview"}, 1),
                                     ("esp", {"schema": 1, "kind": "espInstallerAuditReport", "status": "verified"}, 0)):
            with self.assertRaises(api.NotificationError):
                api.reconcile(mode, document, "wrong marker", scan_exit=code, call=lambda *a, **k: self.fail("GitHub called"))

    def test_oversized_report_keeps_complete_fingerprint_and_bounded_tracker(self):
        api = self.api()
        fake = FakeGitHub()
        body = "<!-- tumoflip-implementation-drift -->\n" + "path\n" * 20000
        report = {**self.report(), "kind": "protectedSurfaceReport"}
        first = api.reconcile("drift", report, body, scan_exit=1, call=fake)
        second = api.reconcile("drift", report, body, scan_exit=1, call=fake)
        self.assertTrue(first["failure"])
        self.assertFalse(second["failure"])
        self.assertEqual(fake.writes, 1)
        self.assertLess(len(fake.issues[0]["body"]), 65000)
        self.assertIn("Full JSON/Markdown", fake.issues[0]["body"])
        self.assertTrue(api.same_report(fake.issues[0]["body"], report))

    def test_github_io_is_json_only_and_errors_fail_closed(self):
        api = self.api()
        for payload, pages, response, expected in ((None, False, '{"number":1}', {"number": 1}),
                                                  ({"body": "quoted"}, False, '{}', {}),
                                                  (None, True, '[[{"number":1}],[]]', [{"number": 1}])):
            with patch.object(api.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, response, "")) as runner:
                self.assertEqual(api.github("issues", payload=payload, pages=pages), expected)
                self.assertNotIn("shell", runner.call_args.kwargs)
        for code, response in ((1, ""), (0, "not json"), (0, "[]")):
            with patch.object(api.subprocess, "run", return_value=subprocess.CompletedProcess([], code, response, "failure")):
                with self.assertRaises(api.NotificationError):
                    api.github("issues")
        with patch.object(api.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '[{}]', "")):
            with self.assertRaises(api.NotificationError):
                api.github("issues", pages=True)

    def test_cli_local_dry_run_and_write_guard(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            summary = Path(directory) / "summary.md"
            report.write_text(json.dumps({**self.report(), "kind": "communityPackAbiAudit"}))
            argv = ["audit_notifications.py", "--mode", "abi", "--report", str(report), "--scan-exit", "1"]
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), patch("sys.argv", argv):
                self.assertEqual(api.main(), 2)
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), patch("sys.argv", argv + ["--dry-run"]), \
                    patch.object(api, "reconcile", return_value={"status": "needsReview", "unchanged": True, "failure": False, "issue": 95}):
                self.assertEqual(api.main(), 0)
                self.assertIn("does not authorize publication", summary.read_text())


class FakeGitHub:
    def __init__(self):
        self.issues = []
        self.comments = []
        self.writes = 0

    def __call__(self, path, *, payload=None, method="GET", pages=False):
        if method == "GET":
            if pages:
                return copy.deepcopy(self.comments if "/comments?" in path else self.issues)
            return copy.deepcopy(next(item for item in self.issues if item["number"] == int(path.split("/")[1])))
        self.writes += 1
        if path == "issues":
            item = {"number": 95, "state": "open", "user": {"login": "github-actions[bot]"}, **payload}
            self.issues.append(item)
        elif path.endswith("/comments"):
            item = {"id": 7, "user": {"login": "github-actions[bot]"}, **payload}
            self.comments.append(item)
        elif path.startswith("issues/comments/"):
            item = self.comments[0]
            item.update(payload)
        else:
            item = next(item for item in self.issues if item["number"] == int(path.split("/")[1]))
            item.update(payload)
        return copy.deepcopy(item)


if __name__ == "__main__":
    unittest.main()
