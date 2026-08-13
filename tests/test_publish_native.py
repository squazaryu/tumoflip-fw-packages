from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from tools.catalog_contract import ContractError, manifest_release_id
from tools.native_release import finalize_native_release, load_native_plan
from tools.publish_native import RELEASE_NOTES, publish_native


class FakeGitHub:
    def __init__(self, repository: str, plan: dict[str, object]) -> None:
        self.repository = repository
        self.plan = plan
        self.release: dict[str, object] | None = None
        self.assets: dict[int, bytes] = {}
        self.tag_target: str | None = None
        self.next_asset = 1000
        self.events: list[str] = []
        self.commands: list[list[str]] = []

    @staticmethod
    def _response(command: list[str], value: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    @staticmethod
    def _form(command: list[str], name: str) -> str:
        prefix = name + "="
        for item in command:
            if item.startswith(prefix):
                return item[len(prefix) :]
        raise AssertionError(name)

    def add_release(self, *, draft: bool, directory: Path, names: list[str]) -> None:
        self.release = {
            "id": 123,
            "tag_name": self.plan["tag"],
            "target_commitish": self.plan["publisherCommit"],
            "name": f"FW Packages {self.plan['channel']} {self.plan['revision']:03d}",
            "body": RELEASE_NOTES,
            "draft": draft,
            "prerelease": self.plan["prerelease"],
            "assets": [],
        }
        for name in names:
            self._add_asset(name, (directory / name).read_bytes())
        if not draft:
            self.tag_target = str(self.plan["publisherCommit"])

    def _add_asset(self, name: str, data: bytes) -> None:
        assert self.release is not None
        asset_id = self.next_asset
        self.next_asset += 1
        self.assets[asset_id] = data
        self.release["assets"].append(  # type: ignore[union-attr]
            {"id": asset_id, "name": name, "size": len(data), "digest": None}
        )

    def runner(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        command = list(command)
        self.commands.append(command)
        if command[:2] == ["gh", "api"]:
            endpoint = next(
                (
                    item
                    for item in command[2:]
                    if item.startswith("repos/")
                    or item.startswith("https://uploads.github.com/")
                ),
                "",
            )
            if endpoint.endswith("/releases?per_page=100"):
                values = [] if self.release is None else [copy.deepcopy(self.release)]
                return self._response(command, [values])
            if "/git/ref/tags/" in endpoint:
                if self.tag_target is None:
                    return subprocess.CompletedProcess(command, 1, "", "HTTP 404: Not Found")
                return self._response(
                    command, {"object": {"type": "commit", "sha": self.tag_target}}
                )
            if endpoint.startswith("https://uploads.github.com/"):
                assert self.release is not None and self.release["draft"] is True
                if not self.events or self.events[-1] != "upload":
                    self.events.append("upload")
                name = parse_qs(urlparse(endpoint).query)["name"][0]
                path = Path(command[command.index("--input") + 1])
                self._add_asset(name, path.read_bytes())
                return self._response(command, {"name": name, "size": path.stat().st_size})
            if (
                "--method" in command
                and endpoint.endswith("/releases")
                and self._form(command, "draft") == "true"
            ):
                if self.release is not None:
                    raise AssertionError("duplicate create")
                self.events.append("create")
                self.release = {
                    "id": 123,
                    "tag_name": self._form(command, "tag_name"),
                    "target_commitish": self._form(command, "target_commitish"),
                    "name": self._form(command, "name"),
                    "body": self._form(command, "body"),
                    "draft": True,
                    "prerelease": self._form(command, "prerelease") == "true",
                    "assets": [],
                }
                return self._response(command, copy.deepcopy(self.release))
            if (
                "--method" in command
                and endpoint.endswith("/releases/123")
                and self._form(command, "draft") == "false"
            ):
                assert self.release is not None
                self.events.append("publish")
                self.release["draft"] = False
                self.tag_target = str(self.release["target_commitish"])
                return self._response(command, copy.deepcopy(self.release))
            if endpoint.endswith("/releases/123"):
                assert self.release is not None
                return self._response(command, copy.deepcopy(self.release))
        raise AssertionError(command)

    def downloader(
        self, command: list[str] | tuple[str, ...], destination: Path
    ) -> subprocess.CompletedProcess[str]:
        asset_id = int(list(command)[-1].rsplit("/", 1)[-1])
        destination.write_bytes(self.assets[asset_id])
        return subprocess.CompletedProcess(command, 0, "", "")


class NativePublicationTests(unittest.TestCase):
    source_commit = "a6bb38f027f5f17f2752d5dfca157478472b5c10"
    publisher_commit = "b" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.control = Path(__file__).resolve().parents[1]
        self.plan = load_native_plan(
            self.control, "dev", 9, self.source_commit, self.publisher_commit
        )
        self.directory = self.root / "release"
        self.directory.mkdir()
        fixture = self.control / "tests/fixtures/native"
        shutil.copy2(
            fixture / "legacy-manifest.json",
            self.directory / "tumoflip-packages.json",
        )
        manifest_path = self.directory / "tumoflip-packages.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["package_release"]["source_commit"] = self.source_commit
        manifest["package_release"]["target_release_id"] = self.plan[
            "targetFirmware"
        ]["releaseId"]
        manifest["package_release"]["overlay_targets"] = self.plan["overlayTargets"]
        manifest["package_release"]["synced_extapps"] = [
            {"target": target} for target in self.plan["overlayTargets"]
        ]
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        with zipfile.ZipFile(self.directory / "tumoflip-packages.zip", "w") as archive:
            for entries in manifest["packages"].values():
                for entry in entries:
                    archive.write(fixture / "resources" / entry["source"], entry["source"])
        finalize_native_release(self.directory, self.plan)
        self.names = sorted(path.name for path in self.directory.iterdir())
        self.github = FakeGitHub("squazaryu/tumoflip-fw-packages", self.plan)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def publish(self) -> None:
        publish_native(
            self.directory,
            self.control,
            self.github.repository,
            "dev",
            9,
            self.source_commit,
            self.publisher_commit,
            self.github.runner,
            self.github.downloader,
        )

    def test_create_response_id_avoids_eventual_consistency_lookup(self) -> None:
        self.publish()
        self.assertEqual(self.github.events, ["create", "upload", "publish"])
        self.assertFalse(self.github.release["draft"])  # type: ignore[index]
        upload_endpoints = [
            command[2]
            for command in self.github.commands
            if len(command) > 2 and command[2].startswith("https://uploads.github.com/")
        ]
        self.assertEqual(len(upload_endpoints), len(self.names))
        self.assertTrue(all("/releases/123/assets?name=" in item for item in upload_endpoints))
        self.assertFalse(any(command[:3] == ["gh", "release", "upload"] for command in self.github.commands))

    def test_partial_exact_draft_resumes_only_missing_assets(self) -> None:
        present = self.names[:2]
        self.github.add_release(draft=True, directory=self.directory, names=present)
        self.publish()
        self.assertEqual(self.github.events, ["upload", "publish"])

    def test_exact_public_release_is_read_only(self) -> None:
        self.github.add_release(draft=False, directory=self.directory, names=self.names)
        self.publish()
        self.assertEqual(self.github.events, [])

    def test_tampered_draft_is_terminal_without_mutation(self) -> None:
        self.github.add_release(draft=True, directory=self.directory, names=[self.names[0]])
        assert self.github.release is not None
        asset = self.github.release["assets"][0]  # type: ignore[index]
        self.github.assets[asset["id"]] = b"tampered"  # type: ignore[index]
        with self.assertRaisesRegex(ContractError, "asset bytes differ"):
            self.publish()
        self.assertEqual(self.github.events, [])

    def test_public_partial_release_is_terminal(self) -> None:
        self.github.add_release(draft=False, directory=self.directory, names=self.names[:-1])
        with self.assertRaisesRegex(ContractError, "missing asset"):
            self.publish()
        self.assertEqual(self.github.events, [])

    def test_local_provenance_is_reverified_before_github_calls(self) -> None:
        provenance = self.directory / "catalog-provenance.json"
        value = json.loads(provenance.read_text())
        value["publisher"]["commit"] = "c" * 40
        provenance.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContractError, "publisher differs"):
            self.publish()
        self.assertIsNone(self.github.release)


if __name__ == "__main__":
    unittest.main()
