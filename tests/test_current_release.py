from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.catalog_contract import ContractError, manifest_release_id, sha256
from tools.verify_catalog import resolve_current_release, verify_current_release


class CurrentReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.control = self.root / "control"
        (self.control / "contracts").mkdir(parents=True)
        self.release = self.root / "release"
        self.release.mkdir()

        payload = b"current package bytes"
        manifest = {
            "schema": 2,
            "firmware": {"version": "t-dev-008-015", "api": "88.4", "target": 7},
            "package_release": {
                "catalog_channel": "dev",
                "catalog_revision": 12,
                "catalog_release_tag": "fw-packages-dev-012",
                "source_commit": "a" * 40,
                "source_dirty": False,
            },
            "packages": {
                "base": [
                    {
                        "source": "apps/Tools/quac.fap",
                        "target": "/ext/apps/Tools/quac.fap",
                        "bytes": len(payload),
                        "md5": hashlib.md5(payload).hexdigest(),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ]
            },
            "cleanup": [],
        }
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path = self.release / "tumoflip-packages.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        archive_path = self.release / "tumoflip-packages.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("apps/Tools/quac.fap", payload)
        checksum_path = self.release / "fw-packages-dev-012-SHA256SUMS"
        checksum_path.write_text(
            f"{sha256(manifest_path)}  tumoflip-packages.json\n"
            f"{sha256(archive_path)}  tumoflip-packages.zip\n",
            encoding="utf-8",
        )
        self.expected = {
            "tag": "fw-packages-dev-012",
            "revision": 12,
            "prerelease": True,
            "releaseId": manifest["release_id"],
            "tagCommit": "b" * 40,
            "sourceCommit": "a" * 40,
            "targetFirmwareTag": "t-dev-004-015",
            "targetFirmwareCommit": "c" * 40,
            "assets": {
                name: sha256(self.release / name)
                for name in (
                    "fw-packages-dev-012-SHA256SUMS",
                    "tumoflip-packages.json",
                    "tumoflip-packages.zip",
                )
            },
        }
        self._write_contract()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_contract(self) -> None:
        (self.control / "contracts/current-releases.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "repository": "squazaryu/tumoflip-fw-packages",
                    "channels": {"dev": self.expected},
                }
            ),
            encoding="utf-8",
        )

    def test_selected_channel_resolves_and_verifies_exact_current_release(self) -> None:
        self.assertEqual(resolve_current_release(self.control, "dev"), self.expected)
        verify_current_release(self.control, self.release, "dev")

    def test_current_release_asset_digest_mismatch_is_terminal(self) -> None:
        self.expected["assets"]["tumoflip-packages.json"] = "0" * 64
        self._write_contract()

        with self.assertRaisesRegex(ContractError, "asset differs from pinned contract"):
            verify_current_release(self.control, self.release, "dev")


if __name__ == "__main__":
    unittest.main()
