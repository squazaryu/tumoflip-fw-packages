"""The new Device Library/history pair is an additive, source-owned base overlay."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeviceLibraryReleaseTests(unittest.TestCase):
    def test_021_plan_pins_only_the_new_pair(self):
        policy = json.loads((ROOT / "contracts/native-build-policy.json").read_text())
        current = json.loads((ROOT / "contracts/current-releases.json").read_text())
        plan = policy["releasePlans"].get("fw-packages-dev-021")
        if plan is None:
            self.assertGreaterEqual(current["channels"]["dev"]["revision"], 21)
            return
        self.assertRegex(plan["sourceCommit"], r"^[0-9a-f]{40}$")
        self.assertEqual(set(plan["selectedOverlays"]), {"device_library", "file_history"})

    def test_pair_has_narrow_overlay_and_protection_ownership(self):
        policy = json.loads((ROOT / "contracts/native-build-policy.json").read_text())
        registry = json.loads((ROOT / "tools/tumoflip/protected_apps_registry.json").read_text())
        expected = {"device_library": "apps/Tools/device_library.fap",
                    "file_history": "apps_data/device_library/plugins/file_history.fal"}
        for name, path in expected.items():
            self.assertEqual(policy["allowedOverlays"].get(name), path)
            self.assertEqual(policy["overlayGroups"].get(name), "base")
        self.assertIn("device_library", registry["protectedKeys"])
        self.assertIn({"prefix": "/ext/apps_data/device_library/", "owner": "device_library"},
                      registry["protectedDataFamilies"])
        # Unrelated firmware-owned apps must not become managed overlays as a side effect.
        self.assertNotIn("tumo_acceptance_suite", policy["allowedOverlays"])


if __name__ == "__main__":
    unittest.main()
