"""TumoSpectrum upgrades use a narrow Module One package overlay."""

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TumoSpectrumReleaseTests(unittest.TestCase):
    def test_signal_workbench_is_explicitly_owned_by_module_one(self):
        policy = json.loads(
            (ROOT / "contracts/native-build-policy.json").read_text()
        )
        target = "apps/Module One/Signals/signal_workbench.fap"
        self.assertEqual(policy["allowedOverlays"].get("signal_workbench"), target)
        self.assertEqual(policy["overlayGroups"].get("signal_workbench"), "module_one")

    def test_dev022_plan_pins_exact_firmware_release_and_two_updated_apps(self):
        policy = json.loads(
            (ROOT / "contracts/native-build-policy.json").read_text()
        )
        plan = policy["releasePlans"].get("fw-packages-dev-022")
        self.assertIsNotNone(plan)
        self.assertEqual(
            plan["sourceCommit"],
            "9531d28b88970df05423c538cefd7966bd08e8af",
        )
        self.assertEqual(
            set(plan["selectedOverlays"]),
            {"capture_inspector", "signal_workbench"},
        )

        current = json.loads((ROOT / "contracts/current-releases.json").read_text())
        self.assertEqual(current["channels"]["dev"]["revision"], 21)


if __name__ == "__main__":
    unittest.main()
