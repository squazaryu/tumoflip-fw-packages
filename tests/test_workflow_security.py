from __future__ import annotations

import re
import unittest
from pathlib import Path


class WorkflowSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.workflows = sorted((self.root / ".github/workflows").glob("*.yml"))

    def test_every_action_is_pinned_to_full_sha(self) -> None:
        self.assertTrue(self.workflows)
        for path in self.workflows:
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                match = re.match(r"\s*uses:\s*([^#\s]+)", line)
                if match is None:
                    continue
                reference = match.group(1)
                self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$", path.name)

    def test_pull_request_workflows_never_receive_write_permissions(self) -> None:
        for path in self.workflows:
            text = path.read_text(encoding="utf-8")
            if "pull_request:" in text:
                self.assertNotIn("contents: write", text)

    def test_only_audits_and_read_only_watchers_are_scheduled(self) -> None:
        scheduled = [
            path.name
            for path in self.workflows
            if re.search(r"(?m)^\s*schedule\s*:", path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            scheduled,
            ["protected-app-audit.yml", "upstream-unleashed-watcher.yml"],
        )

    def test_unleashed_watcher_is_read_only_except_for_one_report_issue(self) -> None:
        text = (self.root / ".github/workflows/upstream-unleashed-watcher.yml").read_text(
            encoding="utf-8"
        )
        guard = (
            "if: github.ref == 'refs/heads/main' && "
            "github.event.repository.default_branch == 'main'"
        )
        self.assertEqual(text.count(guard), 2)
        self.assertIn("issues: write", text)
        self.assertIn("Reconcile one canonical human-review issue", text)
        self.assertIn("contracts/upstream-watchers.json", text)
        self.assertIn("tools/watch_unleashed.py verify", text)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", text)
        self.assertIn("actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", text)
        for forbidden in (
            "contents: write",
            "git push",
            "gh pr merge",
            "gh release",
            "publish_native.py",
            "publish_audit.py",
            "repository: squazaryu/tumoflip",
        ):
            self.assertNotIn(forbidden, text)

    def test_unleashed_watcher_revalidates_bot_owned_issue_before_mutation(self) -> None:
        text = (self.root / ".github/workflows/upstream-unleashed-watcher.yml").read_text(
            encoding="utf-8"
        )
        watcher = (self.root / "tools/watch_unleashed.py").read_text(encoding="utf-8")

        self.assertIn("resolve-canonical-issue", text)
        self.assertIn("verify-canonical-issue", text)
        self.assertIn('gh api "repos/$GITHUB_REPOSITORY/issues/$ISSUE_NUMBER"', text)
        self.assertNotIn("gh issue view", text)
        self.assertNotIn("contains($marker)", text)
        self.assertIn('ISSUE_AUTHOR = "github-actions[bot]"', watcher)
        self.assertIn('value.get("title") != ISSUE_TITLE', watcher)
        self.assertIn('body.startswith(ISSUE_MARKER)', watcher)
        self.assertIn('author.get("login") != ISSUE_AUTHOR', watcher)

        self.assertIn('[[ "$ISSUE_STATE" == open || "$ISSUE_STATE" == closed ]]', text)
        self.assertIn('if [[ "$ISSUE_STATE" == closed && "$CHANGES" == true ]]; then', text)
        self.assertNotIn('"$ISSUE_STATE" == OPEN', text)
        self.assertNotIn('"$ISSUE_STATE" == CLOSED', text)
        self.assertIn(
            "if [[ \"$ISSUE_STATE\" == closed && \"$CHANGES\" == true ]]; then\n"
            "            gh issue reopen \"$ISSUE_NUMBER\" --repo \"$GITHUB_REPOSITORY\"\n"
            "            load_canonical_issue\n"
            "            ISSUE_STATE=\"$(jq -r .state \"$RUNNER_TEMP/issue.json\")\"\n"
            "            [[ \"$ISSUE_STATE\" == open ]]\n"
            "          fi",
            text,
        )

        first_revalidation = text.index("load_canonical_issue\n          ISSUE_STATE")
        self.assertLess(first_revalidation, text.index('gh issue reopen "$ISSUE_NUMBER"'))
        self.assertLess(first_revalidation, text.index('gh issue edit "$ISSUE_NUMBER"'))
        self.assertIn("compare-canonical-issue-body", text)
        self.assertEqual(text.count("compare_canonical_issue_body"), 3)
        self.assertIn('if [[ "$BODY_MATCHES" == false ]]; then', text)
        self.assertIn('[[ "$BODY_MATCHES" == true ]]', text)
        edit = text.index('gh issue edit "$ISSUE_NUMBER"')
        self.assertLess(edit, text.index("compare_canonical_issue_body", edit))
        self.assertLess(edit, text.index('[[ "$BODY_MATCHES" == true ]]', edit))
        self.assertNotIn('cmp -s "$REPORT"', text)

    def test_history_mirror_is_exact_main_draft_first_and_reverified(self) -> None:
        text = (self.root / ".github/workflows/mirror-history.yml").read_text(
            encoding="utf-8"
        )
        guard = (
            "if: github.ref == 'refs/heads/main' && "
            "github.event.repository.default_branch == 'main'"
        )
        self.assertEqual(text.count(guard), 2)
        self.assertEqual(text.count("ref: ${{ github.sha }}"), 2)
        self.assertIn("Reverify after artifact boundary", text)
        self.assertIn("tools/mirror_history.py publish", text)
        self.assertNotIn("gh release create", text)

    def test_protected_audit_splits_privileges_and_orders_publication(self) -> None:
        text = (self.root / ".github/workflows/protected-app-audit.yml").read_text(
            encoding="utf-8"
        )
        for job in ("resolve-source:", "resolve-issue:", "scan:", "publish:", "finalize-issue:"):
            self.assertIn(job, text)
        self.assertIn("environment: production", text)
        self.assertIn("vars.IMMUTABLE_RELEASES_ENABLED == 'true'", text)
        self.assertIn(
            "actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349",
            text,
        )
        self.assertIn("permission-administration: read", text)
        self.assertIn("permission-contents: write", text)
        self.assertGreaterEqual(
            text.count("GH_TOKEN: ${{ steps.publisher-token.outputs.token }}"), 2
        )
        self.assertIn("--bootstrap-index audit/bootstrap/index.json", text)
        self.assertIn("--bootstrap-ledger audit/bootstrap/latest.json", text)
        self.assertIn(".publisherCommit ../publication/publication.json", text)
        self.assertLess(text.index("Publish or resume exact immutable release"), text.index("Publish transitional raw branch"))
        self.assertLess(text.index("Publish transitional raw branch"), text.index("Reconcile canonical issue only after publication proof"))

    def test_resource_decoder_build_backend_is_pinned_and_installed_first(self) -> None:
        build_requirements = (
            self.root / "tools/tumoflip/protected_audit_build_requirements.txt"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            build_requirements,
            r"setuptools==[0-9]+\.[0-9]+\.[0-9]+ --hash=sha256:[0-9a-f]{64}",
        )
        self.assertEqual(build_requirements.count("--hash=sha256:"), 1)

        for workflow_name in ("validate.yml", "protected-app-audit.yml"):
            text = (self.root / ".github/workflows" / workflow_name).read_text(
                encoding="utf-8"
            )
            backend = "protected_audit_build_requirements.txt"
            decoder = "protected_audit_requirements.txt"
            self.assertIn("--only-binary=:all:", text)
            self.assertLess(text.index(backend), text.index(decoder))

    def test_protected_audit_preserves_pending_and_flat_artifact_layout(self) -> None:
        text = (self.root / ".github/workflows/protected-app-audit.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('[[ "$STATUS" == verified || "$STATUS" == pending ]]', text)
        self.assertNotIn('[[ "$STATUS" == verified || "$STATUS" == needsReview ]]', text)
        self.assertIn('BUNDLE="$RUNNER_TEMP/publication-bundle"', text)
        self.assertIn("path: ${{ runner.temp }}/publication-bundle", text)
        self.assertNotRegex(text, r"(?m)^\s+\$\{\{ runner\.temp \}\}/release-assets$")

    def test_protected_audit_issue_lookup_is_paginated_and_unique(self) -> None:
        text = (self.root / ".github/workflows/protected-app-audit.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('issues?state=all&per_page=100', text)
        self.assertIn('--paginate --slurp > "$RUNNER_TEMP/issue-pages.json"', text)
        self.assertIn("test \"$(jq 'length' \"$RUNNER_TEMP/issue-matches.json\")\" -le 1", text)
        self.assertNotIn("gh issue list", text)
        self.assertNotIn("head -n1", text)

    def test_protected_audit_uses_exact_external_checkouts(self) -> None:
        text = (self.root / ".github/workflows/protected-app-audit.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ref: 8ab2ccdf7a34bbf3e07f2d4cbd459de1c6de8758", text)
        self.assertIn("repository: xMasterX/all-the-plugins", text)
        self.assertGreaterEqual(text.count("persist-credentials: false"), 6)
        self.assertIn("Download and verify exact release inputs by numeric ID", text)
        self.assertIn("python3 tools/audit_chain.py", text)
        self.assertNotIn("refs/heads/protected-app-audit-ledger", text)
        self.assertNotIn('EXISTING="$LEDGER/latest.json"', text)
        branch_publisher = (self.root / "tools/publish_audit_branch.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("force-with-lease", branch_publisher)

    def test_dispatch_inputs_are_not_interpolated_in_shell(self) -> None:
        text = (self.root / ".github/workflows/protected-app-audit.yml").read_text(
            encoding="utf-8"
        )
        in_run = False
        run_indent = 0
        for line in text.splitlines():
            match = re.match(r"(\s*)run:\s*\|", line)
            if match:
                in_run = True
                run_indent = len(match.group(1))
                continue
            if in_run and line.strip() and len(line) - len(line.lstrip()) <= run_indent:
                in_run = False
            if in_run:
                self.assertNotIn("${{ inputs.", line)

    def test_native_publisher_remains_fail_closed(self) -> None:
        text = (self.root / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("Native publishing is not enabled", text)
        self.assertIn("environment: production", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("tools/publish_native.py", text)

    def test_native_builder_is_exact_bounded_and_artifact_only(self) -> None:
        text = (self.root / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("ref: ${{ github.sha }}", text)
        self.assertIn("ref: ${{ inputs.source_commit }}", text)
        self.assertIn('test "$(git -C firmware rev-parse HEAD)" = "$EXPECTED"', text)
        self.assertIn("git -C firmware submodule status --recursive", text)
        self.assertIn("./fbt -j2", text)
        self.assertIn("--require-hashes -r control/requirements-build.txt", text)
        requirements = (self.root / "requirements-build.txt").read_text(
            encoding="utf-8"
        )
        self.assertRegex(requirements, r"Pillow==[0-9]+\.[0-9]+\.[0-9]+")
        self.assertRegex(requirements, r"heatshrink2==[0-9]+\.[0-9]+\.[0-9]+")
        self.assertEqual(requirements.count("--hash=sha256:"), 2)
        self.assertIn("control/tools/source_build_targets.py", text)
        self.assertIn("control/tools/preflight_native.py", text)
        self.assertLess(
            text.index("control/tools/preflight_native.py"),
            text.index("repository: squazaryu/tumoflip"),
        )
        self.assertIn("runs-on: ubuntu-24.04", text)
        self.assertIn("firmware/toolchain/x86_64-linux/VERSION", text)
        self.assertIn('--control-root control', text)
        self.assertIn('--channel "$CHANNEL"', text)
        self.assertIn('--revision "$REVISION"', text)
        self.assertIn("Download immutable predecessor catalog", text)
        self.assertIn("--base-directory base-release", text)
        self.assertIn("control/tools/native_release.py", text)
        self.assertIn("control/tools/verify_native.py", text)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", text)
        self.assertNotIn("gh release upload", text)
        self.assertNotIn("target_firmware_commit", text)

    def test_seed_workflow_uses_resumable_verified_publisher(self) -> None:
        text = (self.root / ".github/workflows/seed-legacy.yml").read_text(encoding="utf-8")
        self.assertIn("control/tools/publish_seed.py", text)
        self.assertNotIn("gh release create", text)
        self.assertIn("verify_catalog.py migration", text)

    def test_seed_dispatch_is_pinned_to_exact_default_main_commit(self) -> None:
        text = (self.root / ".github/workflows/seed-legacy.yml").read_text(encoding="utf-8")
        guard = (
            "if: github.ref == 'refs/heads/main' && "
            "github.event.repository.default_branch == 'main'"
        )
        self.assertEqual(text.count(guard), 2)
        self.assertEqual(text.count("ref: ${{ github.sha }}"), 2)
        self.assertEqual(
            text.count('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"'),
            2,
        )
        self.assertEqual(
            text.count(
                'test "$(git ls-remote origin refs/heads/main | cut -f1)" = "$GITHUB_SHA"'
            ),
            2,
        )
        self.assertLess(
            text.index("Prove exact default-branch control commit"),
            text.index("Upload verified seed"),
        )
        self.assertLess(
            text.index("Prove exact default-branch publisher commit"),
            text.index("Publish immutable mirrors"),
        )


if __name__ == "__main__":
    unittest.main()
