import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BleRemoteOwnershipTests(unittest.TestCase):
    def test_only_standard_ble_remote_becomes_a_managed_package(self):
        policy = json.loads((ROOT / "contracts/native-build-policy.json").read_text())
        registry = json.loads((ROOT / "tools/tumoflip/protected_apps_registry.json").read_text())
        self.assertEqual(policy["allowedOverlays"].get("hid_ble"), "apps/Bluetooth/hid_ble.fap")
        self.assertEqual(policy["overlayGroups"].get("hid_ble"), "base")
        self.assertIn("hid_ble", registry["protectedKeys"])
        for unrelated in ("hid_usb", "bad_usb", "btremote_kodi"):
            self.assertNotIn(unrelated, registry["protectedKeys"])
            self.assertNotIn(unrelated, policy["allowedOverlays"])

    def test_hid_pairing_data_is_owned_but_not_pack_payload(self):
        registry = json.loads((ROOT / "tools/tumoflip/protected_apps_registry.json").read_text())
        self.assertIn({"prefix": "/ext/apps_data/hid_ble/", "owner": "hid_ble"},
                      registry["protectedDataFamilies"])


if __name__ == "__main__":
    unittest.main()
