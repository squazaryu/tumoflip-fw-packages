"""Specter Dev 023 is a single, source-owned package replacement."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SpecterDev023Tests(unittest.TestCase):
    def test_release_plan_is_specter_only(self) -> None:
        policy = json.loads((ROOT / "contracts/native-build-policy.json").read_text())
        plan = policy["releasePlans"].get("fw-packages-dev-023")

        self.assertIsNotNone(plan)
        self.assertEqual(plan["mode"], "overlay")
        self.assertEqual(plan["selectedOverlays"], ["specter"])
        self.assertEqual(plan["sourceCommit"], "42aad5d7e16cdb430fd7bbed1c37cf5164b3a334")
        self.assertEqual(
            plan["targetFirmware"],
            {
                "firmwareTag": "t-dev-009-016",
                "firmwareVersion": "t-dev-009-016",
                "firmwareCommit": "6d9e81f06382f7894a2209b2809ea1af16fb3fef",
                "firmwareReleaseId": "0c7d1eb4125fdc7032c3ca5ff250d27a20a72516958492118972d86134dbb660",
                "api": "88.14",
                "target": 7,
            },
        )

    def test_specter_remains_a_protected_base_overlay(self) -> None:
        policy = json.loads((ROOT / "contracts/native-build-policy.json").read_text())
        registry = json.loads(
            (ROOT / "tools/tumoflip/protected_apps_registry.json").read_text()
        )

        self.assertEqual(policy["allowedOverlays"]["specter"], "apps/NFC/specter.fap")
        self.assertEqual(policy["overlayGroups"]["specter"], "base")
        self.assertIn("specter", registry["protectedKeys"])

    def test_prep_does_not_claim_publication(self) -> None:
        current = json.loads((ROOT / "contracts/current-releases.json").read_text())
        lineage = json.loads((ROOT / "contracts/catalog-lineage.json").read_text())

        self.assertEqual(current["channels"]["dev"]["tag"], "fw-packages-dev-022")
        self.assertEqual(lineage["channels"]["dev"]["nextNativeTag"], "fw-packages-dev-023")


if __name__ == "__main__":
    unittest.main()
