"""USB CD-ROM is a Base binary overlay, not a Community overwrite or SD image."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UsbCdromOwnershipTests(unittest.TestCase):
    def test_dev031_is_one_usb_overlay_or_already_activated(self):
        policy = json.loads((ROOT / 'contracts/native-build-policy.json').read_text())
        current = json.loads((ROOT / 'contracts/current-releases.json').read_text())
        plan = policy['releasePlans'].get('fw-packages-dev-031')
        if plan is None:
            self.assertGreaterEqual(current['channels']['dev']['revision'], 31)
            return
        self.assertEqual(plan['mode'], 'overlay')
        self.assertEqual(plan['selectedOverlays'], ['usb_cdrom'])
        self.assertEqual(plan['sourceCommit'], plan['targetFirmware']['firmwareCommit'])
        self.assertEqual(plan['targetFirmware']['firmwareTag'], 't-dev-009-034')
        self.assertEqual(plan['targetFirmware']['api'], '88.23')
        self.assertEqual(plan['targetFirmware']['target'], 7)

    def test_exact_owner_and_no_user_image_overlay(self):
        policy = json.loads((ROOT / 'contracts/native-build-policy.json').read_text())
        registry = json.loads((ROOT / 'tools/tumoflip/protected_apps_registry.json').read_text())
        surface = json.loads((ROOT / 'contracts/protected-surface.json').read_text())
        self.assertEqual(policy['allowedOverlays'].get('usb_cdrom'), 'apps/USB/usb_cdrom.fap')
        self.assertEqual(policy['overlayGroups'].get('usb_cdrom'), 'base')
        self.assertIn('usb_cdrom', registry['protectedKeys'])
        self.assertIn('applications_user/usb_cdrom', surface['ownedSourcePathsByImplementation']['dev'])
        self.assertNotIn('usb_floppy', registry['protectedKeys'])
        self.assertFalse(any(path.endswith('.iso') for path in policy['allowedDataOverlays'].values()))


if __name__ == '__main__': unittest.main()
