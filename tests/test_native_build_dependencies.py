"""Native builds must not retain the Pillow versions fixed by the 13.0 release."""
from pathlib import Path
import re
import unittest


class NativeBuildDependencyTests(unittest.TestCase):
    def test_pillow_uses_a_hash_pinned_version_with_the_critical_fixes(self):
        requirements = (Path(__file__).resolve().parents[1] / "requirements-build.txt").read_text()
        match = re.search(r"(?m)^Pillow==([0-9]+)\.([0-9]+)\.([0-9]+)\s*\\\n\s*--hash=sha256:([0-9a-f]{64})$", requirements)
        self.assertIsNotNone(match, "The native image dependency must remain version- and hash-pinned")
        version = tuple(map(int, match.groups()[:3]))
        # GHSA-5x8w-8jj5-q52h, GHSA-v9pv-5gc8-w2m6 and the other Pillow
        # critical decoder/writer fixes all first land in 13.0.0.
        self.assertGreaterEqual(version, (13, 0, 0))


if __name__ == "__main__":
    unittest.main()
