from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest import mock

from tools.catalog_contract import ContractError
from tools.publish_seed import RELEASE_NOTES, publish_seed


class FakeGitHub:
    def __init__(self, root: Path, repository: str, publisher_commit: str) -> None:
        self.root = root
        self.repository = repository
        self.publisher_commit = publisher_commit
        self.releases: dict[str, dict[str, object]] = {}
        self.asset_bytes: dict[int, bytes] = {}
        self.tags: dict[str, str] = {}
        self.next_release_id = 100
        self.next_asset_id = 1_000
        self.events: list[tuple[str, str, tuple[str, ...]]] = []
        self.release_list_calls = 0
        self.hide_created_releases_from_list = False
        self.hidden_from_release_list: set[str] = set()
        self.published_tag_visibility_delay = 0
        self.pending_tags: dict[str, tuple[str, int]] = {}

    @staticmethod
    def channel(tag: str) -> str:
        return "stable" if "-stable-" in tag else "dev"

    @staticmethod
    def title(channel: str, tag: str) -> str:
        return f"FW Packages {channel} {tag.rsplit('-', 1)[-1]} (legacy mirror)"

    def local_names(self, tag: str) -> list[str]:
        channel = self.channel(tag)
        return sorted(path.name for path in (self.root / "seed" / channel).iterdir())

    def add_release(
        self,
        tag: str,
        *,
        draft: bool,
        names: list[str] | None = None,
        tamper: str | None = None,
        unexpected: str | None = None,
        target: str | None = None,
        name: str | None = None,
    ) -> dict[str, object]:
        channel = self.channel(tag)
        release: dict[str, object] = {
            "id": self.next_release_id,
            "tag_name": tag,
            "target_commitish": target or self.publisher_commit,
            "name": name or self.title(channel, tag),
            "body": RELEASE_NOTES,
            "draft": draft,
            "prerelease": channel == "dev",
            "assets": [],
        }
        self.next_release_id += 1
        self.releases[tag] = release
        for asset_name in names or []:
            data = (self.root / "seed" / channel / asset_name).read_bytes()
            if asset_name == tamper:
                data = bytes([data[0] ^ 1]) + data[1:]
            self._add_asset(release, asset_name, data)
        if unexpected is not None:
            self._add_asset(release, unexpected, b"unexpected")
        if not draft:
            self.tags[tag] = target or self.publisher_commit
        return release

    def _add_asset(self, release: dict[str, object], name: str, data: bytes) -> None:
        asset_id = self.next_asset_id
        self.next_asset_id += 1
        self.asset_bytes[asset_id] = data
        assets = release["assets"]
        assert isinstance(assets, list)
        assets.append(
            {
                "id": asset_id,
                "name": name,
                "size": len(data),
                "digest": None,
            }
        )

    @staticmethod
    def _flag_value(command: list[str], flag: str) -> str:
        return command[command.index(flag) + 1]

    @staticmethod
    def _response(command: list[str], value: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    @staticmethod
    def _fields(command: list[str]) -> dict[str, str]:
        values: dict[str, str] = {}
        for index, item in enumerate(command[:-1]):
            if item not in {"--field", "--raw-field"}:
                continue
            key, separator, value = command[index + 1].partition("=")
            if not separator:
                raise AssertionError(f"invalid API field: {command[index + 1]}")
            values[key] = value
        return values

    def runner(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        command = list(command)
        if command[:2] == ["gh", "api"]:
            if len(command) >= 3 and command[2].endswith("/releases?per_page=100"):
                self.release_list_calls += 1
                releases = [
                    copy.deepcopy(item)
                    for tag, item in self.releases.items()
                    if tag not in self.hidden_from_release_list
                ]
                return self._response(command, [releases])
            endpoint = command[2]
            method = (
                self._flag_value(command, "--method")
                if "--method" in command
                else "GET"
            )
            if endpoint == f"repos/{self.repository}/releases" and method == "POST":
                fields = self._fields(command)
                tag = fields["tag_name"]
                if tag in self.releases or fields.get("draft") != "true":
                    raise AssertionError("release must be created once and as a draft")
                self.events.append(("create-draft", tag, ()))
                release = self.add_release(
                    tag,
                    draft=True,
                    target=fields["target_commitish"],
                    name=fields["name"],
                )
                release["body"] = fields["body"]
                release["prerelease"] = fields["prerelease"] == "true"
                if self.hide_created_releases_from_list:
                    self.hidden_from_release_list.add(tag)
                return self._response(command, copy.deepcopy(release))
            if endpoint.startswith("https://uploads.github.com/") and method == "POST":
                parsed = urlsplit(endpoint)
                release_id = int(parsed.path.split("/releases/", 1)[1].split("/", 1)[0])
                name_values = parse_qs(parsed.query).get("name", [])
                if len(name_values) != 1:
                    raise AssertionError("asset upload must have exactly one name")
                name = name_values[0]
                release = self._release_for_id(release_id)
                tag = str(release["tag_name"])
                if release["draft"] is not True:
                    raise AssertionError("publisher attempted to upload to a public release")
                existing = {
                    item["name"]
                    for item in release["assets"]  # type: ignore[index]
                    if isinstance(item, dict)
                }
                if name in existing:
                    return subprocess.CompletedProcess(command, 1, "", "already_exists")
                path = Path(self._flag_value(command, "--input"))
                self._add_asset(release, name, path.read_bytes())
                self.events.append(("upload", tag, (name,)))
                assets = release["assets"]
                assert isinstance(assets, list)
                return self._response(command, copy.deepcopy(assets[-1]))
            if "/git/ref/tags/" in endpoint:
                tag = endpoint.rsplit("/", 1)[-1]
                pending = self.pending_tags.get(tag)
                if pending is not None:
                    target, remaining = pending
                    if remaining > 0:
                        self.pending_tags[tag] = (target, remaining - 1)
                    else:
                        self.tags[tag] = target
                        del self.pending_tags[tag]
                target = self.tags.get(tag)
                if target is None:
                    return subprocess.CompletedProcess(command, 1, "", "HTTP 404: Not Found")
                return self._response(command, {"object": {"type": "commit", "sha": target}})
            if "/releases/" in endpoint and method == "PATCH":
                release_id = int(endpoint.rsplit("/", 1)[-1])
                release = self._release_for_id(release_id)
                if release["draft"] is not True:
                    raise AssertionError("only a verified draft may be published")
                fields = self._fields(command)
                if fields.get("draft") != "false" or fields.get("make_latest") != "false":
                    raise AssertionError("release patch fields are not fail-closed")
                tag = fields["tag_name"]
                self.events.append(("publish", tag, ()))
                release["target_commitish"] = fields["target_commitish"]
                release["name"] = fields["name"]
                release["body"] = fields["body"]
                release["draft"] = False
                release["prerelease"] = fields["prerelease"] == "true"
                target = str(release["target_commitish"])
                if self.published_tag_visibility_delay:
                    self.pending_tags[tag] = (
                        target,
                        self.published_tag_visibility_delay,
                    )
                else:
                    self.tags[tag] = target
                return self._response(command, copy.deepcopy(release))
            if "/releases/" in endpoint:
                release_id = int(endpoint.rsplit("/", 1)[-1])
                return self._response(command, copy.deepcopy(self._release_for_id(release_id)))
        raise AssertionError(f"unexpected command: {command}")

    def _release_for_id(self, release_id: int) -> dict[str, object]:
        for release in self.releases.values():
            if release["id"] == release_id:
                return release
        raise AssertionError(f"unknown release {release_id}")

    def downloader(
        self, command: list[str] | tuple[str, ...], destination: Path
    ) -> subprocess.CompletedProcess[str]:
        command = list(command)
        endpoint = command[-1]
        asset_id = int(endpoint.rsplit("/", 1)[-1])
        destination.write_bytes(self.asset_bytes[asset_id])
        return subprocess.CompletedProcess(command, 0, "", "")


class AtomicPublicationTests(unittest.TestCase):
    repository = "squazaryu/tumoflip-fw-packages"
    publisher_commit = "a" * 40
    stable_tag = "fw-packages-stable-001"
    dev_tag = "fw-packages-dev-008"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.contract_root = self.root / "control"
        (self.contract_root / "contracts").mkdir(parents=True)
        channels: dict[str, dict[str, object]] = {}
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
        self.github = FakeGitHub(self.root, self.repository, self.publisher_commit)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def names(self, tag: str) -> list[str]:
        return self.github.local_names(tag)

    def publish(self) -> None:
        with (
            mock.patch("tools.publish_seed.verify_release_directory"),
            mock.patch("tools.publish_seed.verify_migration_provenance"),
            mock.patch("tools.publish_seed.time.sleep"),
        ):
            publish_seed(
                self.root / "seed",
                self.contract_root,
                self.repository,
                self.publisher_commit,
                self.github.runner,
                self.github.downloader,
            )

    def add_exact_published(self, tag: str) -> None:
        self.github.add_release(tag, draft=False, names=self.names(tag))

    def test_missing_release_is_drafted_uploaded_verified_then_published(self) -> None:
        self.add_exact_published(self.stable_tag)

        self.publish()

        dev_events = [event for event in self.github.events if event[1] == self.dev_tag]
        self.assertEqual(
            dev_events,
            [
                ("create-draft", self.dev_tag, ()),
                *[
                    ("upload", self.dev_tag, (name,))
                    for name in self.names(self.dev_tag)
                ],
                ("publish", self.dev_tag, ()),
            ],
        )
        self.assertFalse(self.github.releases[self.dev_tag]["draft"])

    def test_partial_draft_resumes_only_missing_assets_then_publishes(self) -> None:
        self.add_exact_published(self.stable_tag)
        present = self.names(self.dev_tag)[:2]
        self.github.add_release(self.dev_tag, draft=True, names=present)

        self.publish()

        missing = tuple(sorted(set(self.names(self.dev_tag)) - set(present)))
        self.assertEqual(
            [event for event in self.github.events if event[1] == self.dev_tag],
            [
                *[("upload", self.dev_tag, (name,)) for name in missing],
                ("publish", self.dev_tag, ()),
            ],
        )

    def test_empty_draft_from_previous_publisher_commit_is_resumed_by_id(self) -> None:
        self.add_exact_published(self.stable_tag)
        draft = self.github.add_release(
            self.dev_tag,
            draft=True,
            target="b" * 40,
        )
        draft_id = draft["id"]

        self.publish()

        self.assertEqual(self.github.releases[self.dev_tag]["id"], draft_id)
        self.assertEqual(
            self.github.releases[self.dev_tag]["target_commitish"],
            self.publisher_commit,
        )
        self.assertFalse(self.github.releases[self.dev_tag]["draft"])

    def test_create_response_is_authoritative_when_release_list_lags(self) -> None:
        self.add_exact_published(self.dev_tag)
        self.github.hide_created_releases_from_list = True

        self.publish()

        self.assertIn(self.stable_tag, self.github.hidden_from_release_list)
        self.assertEqual(self.github.release_list_calls, 1)
        stable_events = [
            event for event in self.github.events if event[1] == self.stable_tag
        ]
        self.assertEqual(stable_events[0], ("create-draft", self.stable_tag, ()))
        self.assertEqual(stable_events[-1], ("publish", self.stable_tag, ()))
        self.assertFalse(self.github.releases[self.stable_tag]["draft"])

    def test_published_tag_visibility_is_retried_by_exact_target(self) -> None:
        self.add_exact_published(self.stable_tag)
        self.github.published_tag_visibility_delay = 2

        self.publish()

        self.assertEqual(self.github.tags[self.dev_tag], self.publisher_commit)
        self.assertNotIn(self.dev_tag, self.github.pending_tags)

    def test_exact_published_releases_verify_and_skip_all_mutation(self) -> None:
        self.add_exact_published(self.stable_tag)
        self.add_exact_published(self.dev_tag)

        self.publish()

        self.assertEqual(self.github.events, [])

    def test_published_partial_release_is_terminal(self) -> None:
        self.github.add_release(
            self.stable_tag, draft=False, names=self.names(self.stable_tag)[:-1]
        )

        with self.assertRaisesRegex(ContractError, "release is missing asset"):
            self.publish()
        self.assertEqual(self.github.events, [])

    def test_published_tampered_release_is_terminal(self) -> None:
        tampered = self.names(self.stable_tag)[0]
        self.github.add_release(
            self.stable_tag,
            draft=False,
            names=self.names(self.stable_tag),
            tamper=tampered,
        )

        with self.assertRaisesRegex(ContractError, "release asset bytes differ"):
            self.publish()
        self.assertEqual(self.github.events, [])

    def test_draft_tamper_is_terminal_without_upload_or_publish(self) -> None:
        self.add_exact_published(self.stable_tag)
        tampered = self.names(self.dev_tag)[0]
        self.github.add_release(
            self.dev_tag, draft=True, names=[tampered], tamper=tampered
        )

        with self.assertRaisesRegex(ContractError, "release asset bytes differ"):
            self.publish()
        self.assertEqual(self.github.events, [])

    def test_draft_unexpected_asset_is_terminal(self) -> None:
        self.add_exact_published(self.stable_tag)
        self.github.add_release(self.dev_tag, draft=True, unexpected="surprise.bin")

        with self.assertRaisesRegex(ContractError, "unexpected asset"):
            self.publish()
        self.assertEqual(self.github.events, [])

    def test_published_wrong_tag_target_is_terminal(self) -> None:
        self.github.add_release(
            self.stable_tag,
            draft=False,
            names=self.names(self.stable_tag),
        )
        self.github.tags[self.stable_tag] = "b" * 40

        with self.assertRaisesRegex(ContractError, "tag target differs"):
            self.publish()
        self.assertEqual(self.github.events, [])

    def test_draft_metadata_drift_is_terminal(self) -> None:
        self.add_exact_published(self.stable_tag)
        self.github.add_release(
            self.dev_tag,
            draft=True,
            name="not the canonical title",
        )

        with self.assertRaisesRegex(ContractError, "metadata differs"):
            self.publish()
        self.assertEqual(self.github.events, [])


if __name__ == "__main__":
    unittest.main()
