from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.catalog_contract import ContractError
from tools.mirror_history import load_contract, verify


class LegacyHistoryTests(unittest.TestCase):
    publisher_repository = "squazaryu/tumoflip-fw-packages"
    publisher_commit = "a" * 40
    tag = "fw-packages-dev-001"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.control = self.root / "control"
        self.history = self.root / "history"
        (self.control / "contracts").mkdir(parents=True)
        directory = self.history / self.tag
        directory.mkdir(parents=True)
        payloads = {
            "tumoflip-packages.json": b"manifest",
            "tumoflip-packages.zip": b"archive",
            f"{self.tag}-SHA256SUMS": b"checksums",
        }
        assets = {}
        evidence = {}
        for name, payload in payloads.items():
            (directory / name).write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            assets[name] = digest
            evidence[name] = {
                "bytes": len(payload),
                "sha256": digest,
                "githubAssetId": len(evidence) + 1,
            }
        self.item = {
            "assets": assets,
            "channel": "dev",
            "legacyGitHubReleaseId": 100,
            "prerelease": True,
            "releaseId": "b" * 64,
            "revision": 1,
            "sourceCommit": "c" * 40,
            "tag": self.tag,
            "tagCommit": "d" * 40,
            "targetFirmwareCommit": "e" * 40,
            "targetFirmwareTag": "t-dev-001",
        }
        contract = {
            "schema": 1,
            "repository": "squazaryu/tumoflip",
            "releases": [self.item],
        }
        (self.control / "contracts/legacy-history.json").write_text(
            json.dumps(contract), encoding="utf-8"
        )
        provenance = {
            "schema": 1,
            "kind": "legacyByteMirror",
            "channel": "dev",
            "publisher": {
                "repository": self.publisher_repository,
                "commit": self.publisher_commit,
            },
            "legacy": {
                "repository": "squazaryu/tumoflip",
                "tag": self.tag,
                "releaseId": 100,
                "releaseURL": f"https://github.com/squazaryu/tumoflip/releases/tag/{self.tag}",
                "tagCommit": "d" * 40,
            },
            "firmwareSourceCommit": "c" * 40,
            "manifestReleaseId": "b" * 64,
            "assets": evidence,
        }
        (directory / "migration-provenance.json").write_text(
            json.dumps(provenance), encoding="utf-8"
        )
        (self.history / "history-index.json").write_text(
            json.dumps({"schema": 1, "releases": {self.tag: provenance}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_history_provenance_is_accepted(self) -> None:
        with mock.patch("tools.mirror_history.verify_release_directory"):
            verify(
                self.control,
                self.history,
                self.publisher_repository,
                self.publisher_commit,
            )

    def test_asset_tamper_after_artifact_boundary_is_rejected(self) -> None:
        (self.history / self.tag / "tumoflip-packages.zip").write_bytes(b"changed")
        with (
            mock.patch("tools.mirror_history.verify_release_directory"),
            self.assertRaisesRegex(ContractError, "asset proof differs"),
        ):
            verify(
                self.control,
                self.history,
                self.publisher_repository,
                self.publisher_commit,
            )

    def test_duplicate_history_revision_is_rejected(self) -> None:
        path = self.control / "contracts/legacy-history.json"
        contract = json.loads(path.read_text())
        contract["releases"].append(dict(self.item))
        path.write_text(json.dumps(contract))
        with self.assertRaisesRegex(ContractError, "duplicate legacy history identity"):
            load_contract(self.control)


if __name__ == "__main__":
    unittest.main()
