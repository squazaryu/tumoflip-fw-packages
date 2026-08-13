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

    def test_bootstrap_has_no_scheduled_or_pull_request_write_workflow(self) -> None:
        for path in self.workflows:
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^\s*schedule\s*:")
            if "pull_request:" in text:
                self.assertNotIn("contents: write", text)

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
