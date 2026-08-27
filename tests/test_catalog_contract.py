from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tools.catalog_contract import (
    ContractError,
    MAX_ARCHIVE_BYTES,
    manifest_release_id,
    sha256,
    verify_archive,
    verify_migration_provenance,
    verify_release_directory,
)
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

    @staticmethod
    def _manifest(directory: Path) -> dict[str, object]:
        return json.loads(
            (directory / "tumoflip-packages.json").read_text(encoding="utf-8")
        )

    def _pinned_contract(self, directory: Path) -> dict[str, object]:
        manifest = self._manifest(directory)
        tag = manifest["package_release"]["catalog_release_tag"]
        names = (
            "tumoflip-packages.json",
            "tumoflip-packages.zip",
            f"{tag}-SHA256SUMS",
        )
        return {
            "tag": tag,
            "releaseId": manifest["release_id"],
            "assets": {name: sha256(directory / name) for name in names},
        }

    def _append_target_collision(self, directory: Path, target: str) -> None:
        manifest_path = directory / "tumoflip-packages.json"
        manifest = self._manifest(directory)
        duplicate = dict(manifest["packages"]["base"][0])
        duplicate["source"] = "apps/other.fap"
        duplicate["target"] = target
        manifest["packages"]["base"].append(duplicate)
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

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

    def test_preserve_existing_requires_dictionary_kind(self) -> None:
        directory = self._release()
        manifest_path = directory / "tumoflip-packages.json"
        manifest = self._manifest(directory)
        manifest["packages"]["base"][0]["preserve_existing"] = True
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(
            ContractError, "preserve_existing requires kind=dictionary"
        ):
            verify_release_directory(directory)

    def test_double_slash_target_collision_is_rejected_as_noncanonical(self) -> None:
        directory = self._release()
        self._append_target_collision(directory, "/ext/apps//test.fap")
        with self.assertRaisesRegex(ContractError, "unsafe .*target"):
            verify_release_directory(directory)

    def test_dot_segment_target_collision_is_rejected_as_noncanonical(self) -> None:
        directory = self._release()
        self._append_target_collision(directory, "/ext/apps/./test.fap")
        with self.assertRaisesRegex(ContractError, "unsafe .*target"):
            verify_release_directory(directory)

    def test_parent_target_segment_is_rejected(self) -> None:
        directory = self._release(target="/ext/apps/../test.fap")
        with self.assertRaisesRegex(ContractError, "unsafe .*target"):
            verify_release_directory(directory)

    def test_backslash_and_control_targets_are_rejected(self) -> None:
        directory = self._release()
        manifest_path = directory / "tumoflip-packages.json"
        for target in ("/ext/apps\\test.fap", "/ext/apps/test\u0000.fap"):
            with self.subTest(target=repr(target)):
                manifest = self._manifest(directory)
                manifest["packages"]["base"][0]["target"] = target
                manifest["release_id"] = manifest_release_id(manifest)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ContractError, "unsafe .*target"):
                    verify_release_directory(directory)

    def test_pinned_manifest_hash_is_checked_before_json_parsing(self) -> None:
        directory = self._release()
        expected = self._pinned_contract(directory)
        (directory / "tumoflip-packages.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(
            ContractError,
            "legacy asset differs from pinned contract: tumoflip-packages.json",
        ):
            verify_release_directory(directory, expected)

    def test_pinned_archive_hash_is_checked_before_zip_parsing(self) -> None:
        directory = self._release()
        expected = self._pinned_contract(directory)
        (directory / "tumoflip-packages.zip").write_bytes(b"not a zip")
        with self.assertRaisesRegex(
            ContractError,
            "legacy asset differs from pinned contract: tumoflip-packages.zip",
        ):
            verify_release_directory(directory, expected)

    def test_pinned_checksum_hash_is_checked_before_checksum_parsing(self) -> None:
        directory = self._release()
        expected = self._pinned_contract(directory)
        (directory / "fw-packages-dev-009-SHA256SUMS").write_text(
            "not a checksum\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            ContractError,
            "legacy asset differs from pinned contract: fw-packages-dev-009-SHA256SUMS",
        ):
            verify_release_directory(directory, expected)

    def test_archive_declared_size_limit_is_checked_before_read(self) -> None:
        directory = self._release()
        manifest = self._manifest(directory)
        manifest["packages"]["base"][0]["bytes"] = MAX_ARCHIVE_BYTES + 1
        manifest["release_id"] = manifest_release_id(manifest)
        info = mock.Mock(
            filename="apps/test.fap",
            file_size=MAX_ARCHIVE_BYTES + 1,
            external_attr=0,
        )
        info.is_dir.return_value = False
        archive = mock.MagicMock()
        archive.__enter__.return_value = archive
        archive.infolist.return_value = [info]
        archive.read.side_effect = AssertionError("archive.read must not be called")
        with mock.patch("tools.catalog_contract.zipfile.ZipFile", return_value=archive):
            with self.assertRaisesRegex(ContractError, "declared size limit"):
                verify_archive(manifest, Path("unused.zip"))
        archive.read.assert_not_called()

    def test_archive_negative_declared_size_is_checked_before_read(self) -> None:
        directory = self._release()
        manifest = self._manifest(directory)
        info = mock.Mock(filename="apps/test.fap", file_size=-1, external_attr=0)
        info.is_dir.return_value = False
        archive = mock.MagicMock()
        archive.__enter__.return_value = archive
        archive.infolist.return_value = [info]
        archive.read.side_effect = AssertionError("archive.read must not be called")
        with mock.patch("tools.catalog_contract.zipfile.ZipFile", return_value=archive):
            with self.assertRaisesRegex(ContractError, "invalid declared size"):
                verify_archive(manifest, Path("unused.zip"))
        archive.read.assert_not_called()

    def test_archive_actual_size_must_match_declared_size(self) -> None:
        directory = self._release()
        manifest = self._manifest(directory)
        declared = manifest["packages"]["base"][0]["bytes"]
        info = mock.Mock(
            filename="apps/test.fap", file_size=declared, external_attr=0
        )
        info.is_dir.return_value = False
        archive = mock.MagicMock()
        archive.__enter__.return_value = archive
        archive.infolist.return_value = [info]
        archive.read.return_value = b"short"
        with mock.patch("tools.catalog_contract.zipfile.ZipFile", return_value=archive):
            with self.assertRaisesRegex(ContractError, "actual size differs"):
                verify_archive(manifest, Path("unused.zip"))

    def test_archive_running_declared_total_is_checked_before_next_read(self) -> None:
        directory = self._release()
        manifest = self._manifest(directory)
        original = manifest["packages"]["base"][0]
        duplicate = dict(original)
        duplicate["source"] = "apps/other.fap"
        duplicate["target"] = "/ext/apps/other.fap"
        manifest["packages"]["base"].append(duplicate)
        manifest["release_id"] = manifest_release_id(manifest)
        infos = []
        for source in ("apps/test.fap", "apps/other.fap"):
            info = mock.Mock(
                filename=source, file_size=original["bytes"], external_attr=0
            )
            info.is_dir.return_value = False
            infos.append(info)
        archive = mock.MagicMock()
        archive.__enter__.return_value = archive
        archive.infolist.return_value = infos
        archive.read.return_value = b"package bytes"
        with mock.patch("tools.catalog_contract.zipfile.ZipFile", return_value=archive):
            with mock.patch("tools.catalog_contract.MAX_ARCHIVE_BYTES", 20):
                with self.assertRaisesRegex(ContractError, "declared size limit"):
                    verify_archive(manifest, Path("unused.zip"))
        self.assertEqual(archive.read.call_count, 1)

    def test_repository_contracts_are_consistent(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        verify_contract(repository)


class MigrationProvenanceTests(unittest.TestCase):
    publisher_repository = "squazaryu/tumoflip-fw-packages"
    publisher_commit = "9" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.contract = {
            "schema": 1,
            "repository": "squazaryu/tumoflip",
            "channels": {},
        }
        index = {
            "schema": 1,
            "sourceRepository": "squazaryu/tumoflip",
            "channels": {},
        }
        for offset, (channel, revision) in enumerate((("stable", 1), ("dev", 8)), 1):
            tag = f"fw-packages-{channel}-{revision:03d}"
            directory = self.root / channel
            directory.mkdir()
            names = (
                "tumoflip-packages.json",
                "tumoflip-packages.zip",
                f"{tag}-SHA256SUMS",
            )
            assets = {}
            pinned = {}
            for asset_offset, name in enumerate(names, 1):
                path = directory / name
                path.write_bytes(f"{channel}:{name}".encode())
                digest = sha256(path)
                pinned[name] = digest
                assets[name] = {
                    "bytes": path.stat().st_size,
                    "sha256": digest,
                    "githubAssetId": offset * 10 + asset_offset,
                }
            source_commit = str(offset) * 40
            release_id = str(offset + 2) * 64
            tag_commit = str(offset + 4) * 40
            legacy_release_id = offset * 100
            release_url = f"https://github.com/squazaryu/tumoflip/releases/tag/{tag}"
            self.contract["channels"][channel] = {
                "tag": tag,
                "revision": revision,
                "prerelease": channel == "dev",
                "releaseId": release_id,
                "tagCommit": tag_commit,
                "sourceCommit": source_commit,
                "assets": pinned,
            }
            index["channels"][channel] = {
                "tag": tag,
                "legacyReleaseId": legacy_release_id,
                "legacyReleaseURL": release_url,
                "legacyTagCommit": tag_commit,
                "sourceCommit": source_commit,
                "manifestReleaseId": release_id,
                "assets": assets,
            }
            provenance = {
                "schema": 1,
                "kind": "legacyByteMirror",
                "channel": channel,
                "publisher": {
                    "repository": self.publisher_repository,
                    "commit": self.publisher_commit,
                },
                "legacy": {
                    "repository": self.contract["repository"],
                    "tag": tag,
                    "releaseId": legacy_release_id,
                    "releaseURL": release_url,
                    "tagCommit": tag_commit,
                },
                "firmwareSourceCommit": source_commit,
                "manifestReleaseId": release_id,
                "assets": assets,
            }
            (directory / "migration-provenance.json").write_text(
                json.dumps(provenance), encoding="utf-8"
            )
        (self.root / "seed-index.json").write_text(json.dumps(index), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _verify(self) -> None:
        verify_migration_provenance(
            self.root,
            self.contract,
            self.publisher_repository,
            self.publisher_commit,
        )

    def test_exact_migration_provenance_is_accepted(self) -> None:
        self._verify()

    def test_asset_tampering_after_artifact_boundary_is_rejected(self) -> None:
        with (self.root / "dev/tumoflip-packages.zip").open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(ContractError, "bytes changed after verification"):
            self._verify()


if __name__ == "__main__":
    unittest.main()
