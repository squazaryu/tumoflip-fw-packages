"""The field kit adds two binary overlays, never user captures or accepted pins."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FieldKitOwnershipTests(unittest.TestCase):
    def test_new_rx_leaves_and_status_have_explicit_native_overlay_ownership(self):
        policy = json.loads((ROOT / "contracts/native-build-policy.json").read_text())
        expected = {f"protocol_rx_{stem}": f"apps_data/subghz/plugins/protocol_rx_{stem}.fal"
                    for stem in ("x10", "urmet", "gm0", "gm1", "ty0", "mz2", "mb0", "mb1", "op1", "bmw")}
        expected["arf_status"] = "apps_data/arf_subghz_full/modules/arf_status.fap"
        for key, path in expected.items():
            self.assertEqual(policy["allowedOverlays"].get(key), path)
            self.assertEqual(policy["overlayGroups"].get(key),
                             "arf" if key == "arf_status" else "protocol_packs")

    def test_field_apps_are_narrow_base_overlays_with_protected_community_aliases(self):
        policy = json.loads((ROOT / "contracts/native-build-policy.json").read_text())
        registry = json.loads((ROOT / "tools/tumoflip/protected_apps_registry.json").read_text())
        for app, path in {"field_tools": "apps/Tools/field_tools.fap",
                          "tumo_nfc_sniffer": "apps/NFC/tumo_nfc_sniffer.fap"}.items():
            self.assertEqual(policy["allowedOverlays"].get(app), path)
            self.assertEqual(policy["overlayGroups"].get(app), "base")
            self.assertIn(app, registry["protectedKeys"])
        self.assertIn("nfc_sniffer", registry["protectedKeys"])
        self.assertIn({"prefix": "/ext/apps_data/device_library/", "owner": "device_library"}, registry["protectedDataFamilies"])
        for path in policy.get("allowedDataOverlays", {}).values():
            self.assertNotIn("device_library/", path)
            self.assertNotIn("nfc/field_sniffer/", path)


if __name__ == "__main__":
    unittest.main()
