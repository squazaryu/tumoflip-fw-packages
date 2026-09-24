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


if __name__ == "__main__":
    unittest.main()
