"""Package ownership only: no catalog activation or fabricated audit acceptance."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    'servo_tester_plus': 'apps/GPIO/servo_tester_plus.fap',
    'power_journal': 'apps/Tools/Diagnostics/power_journal.fap',
    'sensor_watch': 'apps/Sub-GHz/sensor_watch.fap',
    'tumo_varta': 'apps/RFID/tumo_varta.fap',
    'tumo_uhf_inventory': 'apps/GPIO/tumo_uhf_inventory.fap',
}


class FieldBatchOwnershipTests(unittest.TestCase):
    def test_new_tools_have_binary_only_base_overlays_and_do_not_replace_full_community_apps(self):
        policy = json.loads((ROOT / 'contracts/native-build-policy.json').read_text())
        registry = json.loads((ROOT / 'tools/tumoflip/protected_apps_registry.json').read_text())
        for app, path in TARGETS.items():
            self.assertEqual(policy['allowedOverlays'].get(app), path)
            self.assertEqual(policy['overlayGroups'].get(app), 'base')
            self.assertIn(app, registry['protectedKeys'])
        for separate in ('varta', 'uhf_expansion', 'gbhobbytech_servo_tester'):
            self.assertNotIn(separate, registry['protectedKeys'])
        for path in policy['allowedDataOverlays'].values():
            self.assertNotIn('device_library/', path)


if __name__ == '__main__': unittest.main()
