from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.catalog_contract import ContractError, manifest_release_id, sha256
from tools.native_release import (
    build_firmware_snapshot_release,
    load_native_plan,
    verify_native_release,
)


class FirmwareSnapshotReleaseTests(unittest.TestCase):
    source_commit = "1f9457fb9a513c08685c5d76178318491e8eb6c2"
    publisher_commit = "b" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        repository = Path(__file__).resolve().parents[1]
        self.control = self.root / "control"
        shutil.copytree(repository / "contracts", self.control / "contracts")
        self.base = self._release_directory(
            "base", b"old", version="t-flppr-fw-004", independent=True
        )
        self.target = self._release_directory(
            "target", b"new", version="t-flppr-fw-005", independent=False
        )
        self._pin_contracts()
        self.plan = load_native_plan(
            self.control, "stable", 2, self.source_commit, self.publisher_commit
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _release_directory(
        self, name: str, payload: bytes, *, version: str, independent: bool
    ) -> Path:
        directory = self.root / name
        directory.mkdir()
        entry = {
            "source": "apps/Tools/fixture.fap",
            "target": "/ext/apps/Tools/fixture.fap",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "md5": hashlib.md5(payload).hexdigest(),
        }
        package_release = None
        if independent:
            package_release = {
                "type": "package-only",
                "id": "fw-packages-stable-001",
                "source_commit": "a" * 40,
                "source_dirty": False,
                "source_firmware_version": version,
                "target_release_tag": "v1.0.4",
                "firmware_flash_unchanged": True,
                "catalog_channel": "stable",
                "catalog_revision": 1,
                "catalog_release_tag": "fw-packages-stable-001",
            }
        manifest = {
            "schema": 2,
            "firmware": {"name": "tumoflip", "version": version, "api": "88.0", "target": 7},
            "artifacts": {},
            "packages": {"base": [entry], "arf": [], "module_one": [], "protocol_packs": []},
            "cleanup": [],
            "safety": {},
            "package_release": package_release,
        }
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path = directory / "tumoflip-packages.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        with zipfile.ZipFile(directory / "tumoflip-packages.zip", "w") as archive:
            archive.writestr(entry["source"], payload)
        if independent:
            checksum = directory / "fw-packages-stable-001-SHA256SUMS"
            checksum.write_text(
                "".join(
                    f"{sha256(directory / asset)}  {asset}\n"
                    for asset in ("tumoflip-packages.json", "tumoflip-packages.zip")
                )
            )
        return directory

    def _pin_contracts(self) -> None:
        target_manifest = json.loads((self.target / "tumoflip-packages.json").read_text())
        baseline_path = self.control / "contracts/catalog-baselines.json"
        baselines = json.loads(baseline_path.read_text())
        stable = baselines["channels"]["stable"]
        stable.update(
            {
                "firmwareTag": "v1.0.5",
                "firmwareVersion": "t-flppr-fw-005",
                "firmwareCommit": self.source_commit,
                "firmwareReleaseId": target_manifest["release_id"],
                "packageManifestSHA256": sha256(self.target / "tumoflip-packages.json"),
                "packageZipSHA256": sha256(self.target / "tumoflip-packages.zip"),
            }
        )
        baseline_path.write_text(json.dumps(baselines))

        legacy_path = self.control / "contracts/legacy-sources.json"
        legacy = json.loads(legacy_path.read_text())
        base_manifest = json.loads((self.base / "tumoflip-packages.json").read_text())
        checksum = self.base / "fw-packages-stable-001-SHA256SUMS"
        legacy["channels"]["stable"].update(
            {
                "releaseId": base_manifest["release_id"],
                "assets": {
                    checksum.name: sha256(checksum),
                    "tumoflip-packages.json": sha256(self.base / "tumoflip-packages.json"),
                    "tumoflip-packages.zip": sha256(self.base / "tumoflip-packages.zip"),
                },
            }
        )
        legacy_path.write_text(json.dumps(legacy))

    def test_snapshot_is_exact_atomic_and_independently_reverified(self) -> None:
        output = self.root / "stable002"
        build_firmware_snapshot_release(self.target, self.base, output, self.plan)
        verify_native_release(output, self.plan, self.base, self.target)
        manifest = json.loads((output / "tumoflip-packages.json").read_text())
        self.assertEqual(manifest["package_release"]["catalog_release_tag"], "fw-packages-stable-002")
        self.assertEqual(manifest["package_release"]["target_release_id"], self.plan["targetFirmware"]["releaseId"])
        self.assertEqual(sha256(output / "tumoflip-packages.zip"), sha256(self.target / "tumoflip-packages.zip"))
        provenance = json.loads((output / "catalog-provenance.json").read_text())
        self.assertEqual(provenance["kind"], "firmwareSnapshotPackageRelease")
        self.assertEqual(provenance["changedSources"], ["apps/Tools/fixture.fap"])

    def test_snapshot_target_tamper_is_terminal_after_artifact_boundary(self) -> None:
        output = self.root / "stable002"
        build_firmware_snapshot_release(self.target, self.base, output, self.plan)
        with (self.target / "tumoflip-packages.zip").open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(ContractError, "snapshot ZIP SHA-256 differs"):
            verify_native_release(output, self.plan, self.base, self.target)


if __name__ == "__main__":
    unittest.main()
