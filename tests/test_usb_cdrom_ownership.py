"""USB CD-ROM is a Base binary overlay, not a Community overwrite or SD image."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UsbCdromOwnershipTests(unittest.TestCase):
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
