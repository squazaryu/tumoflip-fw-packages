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


if __name__ == "__main__":
    unittest.main()
