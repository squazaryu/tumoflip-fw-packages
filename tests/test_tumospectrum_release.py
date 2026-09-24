"""TumoSpectrum upgrades use a narrow Module One package overlay."""

import json
from pathlib import Path
import unittest

from tools.native_release import load_native_plan

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

    def test_dev022_uses_exact_firmware_provenance_without_rebasing_catalog(self):
        baselines = json.loads((ROOT / "contracts/catalog-baselines.json").read_text())
        self.assertEqual(
            baselines["channels"]["dev"]["firmwareTag"], "t-dev-004-015"
        )
        source_commit = "9531d28b88970df05423c538cefd7966bd08e8af"
        plan = load_native_plan(ROOT, "dev", 22, source_commit, "f" * 40)
        self.assertEqual(
            plan["targetFirmware"],
            {
                "repository": "squazaryu/tumoflip",
                "tag": "t-dev-009-012",
                "commit": source_commit,
                "releaseId": "ed024a4619b45fb21cc8e6ea39a48e135027f381a731c2d54b8ba16ff9a94912",
                "version": "t-dev-009-012",
                "api": "88.14",
                "target": 7,
                "packageManifestSHA256": None,
                "packageZipSHA256": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
