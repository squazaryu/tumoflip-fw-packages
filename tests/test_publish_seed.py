from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.publish_seed import publish_seed


class PartialPublicationRerunTests(unittest.TestCase):
    repository = "squazaryu/tumoflip-fw-packages"
    publisher_commit = "a" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.contract_root = self.root / "control"
        (self.contract_root / "contracts").mkdir(parents=True)
        channels = {}
        for channel, revision in (("stable", 1), ("dev", 8)):
            tag = f"fw-packages-{channel}-{revision:03d}"
            channels[channel] = {"tag": tag, "prerelease": channel == "dev"}
            directory = self.root / "seed" / channel
            directory.mkdir(parents=True)
            for name in (
                "tumoflip-packages.json",
                "tumoflip-packages.zip",
                f"{tag}-SHA256SUMS",
                "migration-provenance.json",
            ):
                (directory / name).write_bytes(f"{channel}:{name}".encode())
        (self.contract_root / "contracts/legacy-sources.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "repository": "squazaryu/tumoflip",
                    "channels": channels,
                }
            ),
            encoding="utf-8",
        )
        self.existing = {"fw-packages-stable-001"}
        self.created: list[str] = []
        self.downloaded: list[str] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _metadata(self, tag: str) -> dict[str, object]:
        channel = "stable" if "stable" in tag else "dev"
        directory = self.root / "seed" / channel
        return {
            "tag_name": tag,
            "draft": False,
            "prerelease": channel == "dev",
            "assets": [
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "digest": None,
                }
                for path in sorted(directory.iterdir())
            ],
        }

    def _runner(self, command: tuple[str, ...] | list[str]) -> subprocess.CompletedProcess[str]:
        command = list(command)
        if command[:2] == ["gh", "api"]:
            endpoint = command[2]
            if "/releases/tags/" in endpoint:
                tag = endpoint.rsplit("/", 1)[-1]
                if tag not in self.existing:
                    return subprocess.CompletedProcess(command, 1, "", "HTTP 404: Not Found")
                return subprocess.CompletedProcess(command, 0, json.dumps(self._metadata(tag)), "")
            if "/git/ref/tags/" in endpoint:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {"object": {"type": "commit", "sha": self.publisher_commit}}
                    ),
                    "",
                )
        if command[:3] == ["gh", "release", "create"]:
            tag = command[3]
            self.assertNotIn(tag, self.existing)
            self.existing.add(tag)
            self.created.append(tag)
            return subprocess.CompletedProcess(command, 0, "created", "")
        if command[:3] == ["gh", "release", "download"]:
            tag = command[3]
            channel = "stable" if "stable" in tag else "dev"
            destination = Path(command[command.index("--dir") + 1])
            for path in (self.root / "seed" / channel).iterdir():
                shutil.copy2(path, destination / path.name)
            self.downloaded.append(tag)
            return subprocess.CompletedProcess(command, 0, "", "")
        self.fail(f"unexpected command: {command}")

    @mock.patch("tools.publish_seed.verify_release_directory")
    @mock.patch("tools.publish_seed.verify_migration_provenance")
    def test_rerun_verifies_existing_stable_then_creates_only_missing_dev(
        self,
        _verify_provenance: mock.Mock,
        _verify_release: mock.Mock,
    ) -> None:
        publish_seed(
            self.root / "seed",
            self.contract_root,
            self.repository,
            self.publisher_commit,
            self._runner,
        )

        self.assertEqual(self.created, ["fw-packages-dev-008"])
        self.assertEqual(
            self.downloaded,
            ["fw-packages-stable-001", "fw-packages-dev-008"],
        )
        self.assertEqual(_verify_release.call_count, 4)
        _verify_provenance.assert_called_once()


if __name__ == "__main__":
    unittest.main()
