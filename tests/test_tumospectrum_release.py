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

    def test_dev022_is_active_with_exact_firmware_and_asset_identity(self):
        policy = json.loads(
            (ROOT / "contracts/native-build-policy.json").read_text()
        )
        current = json.loads((ROOT / "contracts/current-releases.json").read_text())
        expected_current = {
            "tag": "fw-packages-dev-022",
            "revision": 22,
            "prerelease": True,
            "releaseId": "9b2edb16ee57b09a60058d9764633398620cc0ed24fca0db08aef83906dafa8b",
            "tagCommit": "192a6c88b9d7b1d9dfac8f15ced81261693be076",
            "sourceCommit": "9531d28b88970df05423c538cefd7966bd08e8af",
            "targetFirmwareTag": "t-dev-009-012",
            "targetFirmwareCommit": "9531d28b88970df05423c538cefd7966bd08e8af",
            "api": "88.14",
            "target": 7,
            "assets": {
                "fw-packages-dev-022-SHA256SUMS": "567e670c944f04c637ef497cf08bde812abe834ab15e2ecfe8f97da8da7f9f28",
                "tumoflip-packages.json": "2cc23946a5d34efac9b63cf9fda728f0c70dc19f32c8d082eed79c714cf8cf3e",
                "tumoflip-packages.zip": "b59ab74fdab578749cf36f5ca98672c282267fbf2b9a29f1b70c552b7b0a9feb",
            },
        }
        self.assertEqual(current["channels"]["dev"], expected_current)
        self.assertNotIn("fw-packages-dev-022", policy["releasePlans"])

        lineage = json.loads((ROOT / "contracts/catalog-lineage.json").read_text())
        self.assertEqual(lineage["channels"]["dev"]["currentRevision"], 22)
        self.assertEqual(lineage["channels"]["dev"]["nextNativeRevision"], 23)

        index = json.loads((ROOT / "catalog-index.json").read_text())
        self.assertEqual(index["current_revision"], 22)
        entry = next(item for item in index["releases"] if item["revision"] == 22)
        self.assertEqual(entry["tag"], "fw-packages-dev-022")
        self.assertEqual(entry["state"], "active")

    def test_global_dev_baseline_stays_independent_from_firmware_release(self):
        baselines = json.loads((ROOT / "contracts/catalog-baselines.json").read_text())
        self.assertEqual(
            baselines["channels"]["dev"]["firmwareTag"], "t-dev-004-015"
        )


if __name__ == "__main__":
    unittest.main()
