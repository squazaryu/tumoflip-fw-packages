"""The published Specter Dev 023 catalog has exact, protected provenance."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SpecterDev023Tests(unittest.TestCase):
    def test_published_release_has_exact_identity(self) -> None:
        policy = json.loads((ROOT / "contracts/native-build-policy.json").read_text())
        audit = json.loads((ROOT / "contracts/protected-audit-targets.json").read_text())
        entry = next(item for item in audit["packages"] if item["releaseTag"] == "fw-packages-dev-023")
        self.assertEqual(
            {
                "tag": entry["releaseTag"], "revision": 23,
                "prerelease": entry["prerelease"],
                "releaseId": entry["manifestReleaseId"],
                "tagCommit": entry["tagCommit"],
                "sourceCommit": entry["manifestSourceCommit"],
                "assets": {value["name"]: value["sha256"] for value in entry["assets"].values()
                           if value["name"] != "catalog-provenance.json"},
            },
            {
                "tag": "fw-packages-dev-023",
                "revision": 23,
                "prerelease": True,
                "releaseId": "84160432f17fb90e4c54cf3c8c3da909084e97e4a62730bf6587b44dbff27be6",
                "tagCommit": "dada34ef341305727a46e4bf32048a37b3ea3359",
                "sourceCommit": "42aad5d7e16cdb430fd7bbed1c37cf5164b3a334",
                "assets": {
                    "fw-packages-dev-023-SHA256SUMS": "f19491ea7a2f335588c56be20cec0a6ac1eb347b05a5201926bc7f550cfc6598",
                    "tumoflip-packages.json": "3a7e1b81c2f8a7e3a83a9967833fd4e4e683b2f3ebb9d8b7679d4b91117787e0",
                    "tumoflip-packages.zip": "60cafe6a6bcd2e900e228aa5d4f17308eada0a01260d823010c3fdcbdb75eefe",
                },
            },
        )
        self.assertNotIn("fw-packages-dev-023", policy["releasePlans"])

    def test_specter_remains_a_protected_base_overlay(self) -> None:
        policy = json.loads((ROOT / "contracts/native-build-policy.json").read_text())
        registry = json.loads(
            (ROOT / "tools/tumoflip/protected_apps_registry.json").read_text()
        )

        self.assertEqual(policy["allowedOverlays"]["specter"], "apps/NFC/specter.fap")
        self.assertEqual(policy["overlayGroups"]["specter"], "base")
        self.assertIn("specter", registry["protectedKeys"])

    def test_catalog_activates_dev023_and_retains_dev022(self) -> None:
        current = json.loads((ROOT / "contracts/current-releases.json").read_text())
        lineage = json.loads((ROOT / "contracts/catalog-lineage.json").read_text())
        index = json.loads((ROOT / "catalog-index.json").read_text())

        head = current["channels"]["dev"]
        self.assertGreaterEqual(head["revision"], 23)
        self.assertEqual(lineage["channels"]["dev"]["currentTag"], head["tag"])
        self.assertEqual(lineage["channels"]["dev"]["nextNativeRevision"], head["revision"] + 1)
        self.assertEqual(index["channels"]["dev"]["current_revision"], head["revision"])
        entries = {item["revision"]: item for item in index["channels"]["dev"]["releases"]}
        self.assertEqual(entries[22]["tag"], "fw-packages-dev-022")
        self.assertEqual(entries[23]["tag"], "fw-packages-dev-023")
        self.assertEqual(entries[23]["state"], "active")
        self.assertEqual(entries[23]["release_id"], "84160432f17fb90e4c54cf3c8c3da909084e97e4a62730bf6587b44dbff27be6")
        self.assertEqual(entries[23]["compatibility"], {"targets": [7], "api_majors": [88]})


if __name__ == "__main__":
    unittest.main()
