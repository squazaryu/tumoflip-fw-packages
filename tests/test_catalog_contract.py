from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.catalog_contract import ContractError, manifest_release_id, verify_release_directory
from tools.verify_catalog import verify_contract


class CatalogContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _release(self, source: str = "apps/test.fap", target: str = "/ext/apps/test.fap") -> Path:
        directory = self.root / "release"
        directory.mkdir()
        data = b"package bytes"
        manifest = {
            "schema": 2,
            "firmware": {"version": "t-dev-004-015", "api": "88.0", "target": 7},
            "package_release": {
                "catalog_channel": "dev",
                "catalog_revision": 9,
                "catalog_release_tag": "fw-packages-dev-009",
                "source_commit": "a" * 40,
                "source_dirty": False,
            },
            "packages": {
                "base": [
                    {
                        "source": source,
                        "target": target,
                        "bytes": len(data),
                        "md5": hashlib.md5(data).hexdigest(),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                ]
            },
            "cleanup": [],
        }
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path = directory / "tumoflip-packages.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        archive_path = directory / "tumoflip-packages.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(source, data)
        checksums = directory / "fw-packages-dev-009-SHA256SUMS"
        checksums.write_text(
            f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  tumoflip-packages.json\n"
            f"{hashlib.sha256(archive_path.read_bytes()).hexdigest()}  tumoflip-packages.zip\n",
            encoding="utf-8",
        )
        return directory

    def test_valid_release_is_accepted(self) -> None:
        verify_release_directory(self._release())

    def test_manifest_release_id_is_recomputed(self) -> None:
        directory = self._release()
        manifest_path = directory / "tumoflip-packages.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["firmware"]["api"] = "99.0"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "release_id mismatch"):
            verify_release_directory(directory)

    def test_archive_traversal_is_rejected(self) -> None:
        directory = self._release(source="../escape.fap")
        with self.assertRaisesRegex(ContractError, "unsafe"):
            verify_release_directory(directory)

    def test_duplicate_target_is_rejected(self) -> None:
        directory = self._release()
        manifest_path = directory / "tumoflip-packages.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        duplicate = dict(manifest["packages"]["base"][0])
        duplicate["source"] = "apps/other.fap"
        manifest["packages"]["base"].append(duplicate)
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "duplicate package target"):
            verify_release_directory(directory)

    def test_repository_contracts_are_consistent(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        verify_contract(repository)


if __name__ == "__main__":
    unittest.main()
