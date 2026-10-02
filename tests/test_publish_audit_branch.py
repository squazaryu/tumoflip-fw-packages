from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.publish_audit_branch import BranchError, prepare_tree
from tools.tumoflip import protected_app_audit as audit_tool


class RawMirrorCatchupTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.ledger = json.loads((root / "audit/bootstrap/latest.json").read_text())

    def stage(self, root: Path, raw: dict, released: dict, current: dict) -> None:
        (root / "history").mkdir()
        audit_tool.write_json(root / "latest.json", raw)
        audit_tool.write_json(root / "released.json", released)
        audit_tool.write_json(root / "audit.json", current)

    def prepare(self, root: Path) -> list[str]:
        return prepare_tree(
            root=root, audit_path=root / "audit.json", released_ledger=root / "released.json"
        )

    def test_missing_previous_publications_are_recovered_from_verified_snapshot(self) -> None:
        raw = copy.deepcopy(self.ledger)
        raw["audits"] = raw["audits"][:-1]
        current = self.ledger["audits"][0]
        released = audit_tool.merge_ledger(self.ledger, current)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root, raw, released, current)
            changed = self.prepare(root)
            self.assertIn("latest.json", changed)
            self.assertEqual((root / "latest.json").read_bytes(), (root / "released.json").read_bytes())
            self.assertEqual(len(list((root / "history").glob("*.json"))), len(released["audits"]))

    def test_superseded_raw_snapshot_is_preserved_without_overwriting_history(self) -> None:
        current = self.ledger["audits"][-1]
        released = audit_tool.merge_ledger(self.ledger, current)
        released["audits"][0]["entries"][0]["note"] += " Reviewed target evidence refreshed."
        audit_tool.validate_ledger(released)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root, self.ledger, released, current)
            old = root / "history/previous.json"
            audit_tool.write_json(old, self.ledger["audits"][0])
            original = old.read_bytes()
            self.prepare(root)
            self.assertEqual(old.read_bytes(), original)
            snapshots = [json.loads(path.read_text()) for path in (root / "history").glob("*.json")]
            self.assertIn(self.ledger["audits"][0], snapshots)
            self.assertIn(released["audits"][0], snapshots)
            self.assertEqual((root / "latest.json").read_bytes(), (root / "released.json").read_bytes())

    def test_unknown_raw_identity_is_not_silently_dropped(self) -> None:
        raw = copy.deepcopy(self.ledger)
        unknown = copy.deepcopy(raw["audits"][-1])
        unknown["sourceTag"] = "31aug2026"
        unknown["sourceURL"] = "https://github.com/xMasterX/all-the-plugins/releases/tag/31aug2026"
        unknown["sequence"] = 20260831000000
        raw["audits"].append(unknown)
        audit_tool.validate_ledger(raw)
        current = self.ledger["audits"][-1]
        released = audit_tool.merge_ledger(self.ledger, current)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root, raw, released, current)
            original = (root / "latest.json").read_bytes()
            with self.assertRaises(BranchError):
                self.prepare(root)
            self.assertEqual((root / "latest.json").read_bytes(), original)
            self.assertEqual(list((root / "history").iterdir()), [])

    def test_changed_raw_source_identity_is_rejected_before_mutation(self) -> None:
        raw = copy.deepcopy(self.ledger)
        raw["audits"][0]["sourceCommit"] = "a" * 40
        current = self.ledger["audits"][-1]
        released = audit_tool.merge_ledger(self.ledger, current)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root, raw, released, current)
            original = (root / "latest.json").read_bytes()
            with self.assertRaises(BranchError):
                self.prepare(root)
            self.assertEqual((root / "latest.json").read_bytes(), original)

    def test_current_audit_must_match_the_released_snapshot(self) -> None:
        current = copy.deepcopy(self.ledger["audits"][-1])
        released = audit_tool.merge_ledger(self.ledger, current)
        current["entries"][0]["note"] += " Unexpected mutation."
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root, self.ledger, released, current)
            original = (root / "latest.json").read_bytes()
            with self.assertRaises(BranchError):
                self.prepare(root)
            self.assertEqual((root / "latest.json").read_bytes(), original)

    def test_invalid_history_is_detected_before_latest_is_written(self) -> None:
        current = copy.deepcopy(self.ledger["audits"][-1])
        current["entries"][0]["note"] += " New reviewed evidence."
        released = audit_tool.merge_ledger(self.ledger, current)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root, self.ledger, released, current)
            (root / "history/broken.json").write_text("not JSON")
            original = (root / "latest.json").read_bytes()
            with self.assertRaises(BranchError):
                self.prepare(root)
            self.assertEqual((root / "latest.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
