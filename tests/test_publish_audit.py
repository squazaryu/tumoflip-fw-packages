from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.audit_release import ASSET_NAMES
from tools.publish_audit import (
    PublishError,
    RELEASE_NOTES,
    _require_immutable_releases_enabled,
    publish,
)


class FakeAuditGitHub:
    def __init__(self, assets: Path, repository: str, commit: str, tag: str) -> None:
        self.assets = assets
        self.repository = repository
        self.commit = commit
        self.tag = tag
        self.release: dict[str, object] | None = None
        self.asset_bytes: dict[int, bytes] = {}
        self.next_asset = 100
        self.events: list[str] = []
        self.tag_visible = False
        self.immutable_enabled = True

    def _response(self, command: list[str], value: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    def _new_release(self, *, draft: bool, immutable: bool) -> dict[str, object]:
        return {
            "id": 42,
            "tag_name": self.tag,
            "target_commitish": self.commit,
            "name": f"Protected App Audit {self.tag}",
            "body": RELEASE_NOTES,
            "draft": draft,
            "prerelease": False,
            "immutable": immutable,
            "assets": [],
        }

    def add_public(self, *, immutable: bool, tamper: str | None = None) -> None:
        self.release = self._new_release(draft=False, immutable=immutable)
        self.tag_visible = True
        for name in ASSET_NAMES:
            data = (self.assets / name).read_bytes()
            if name == tamper:
                data += b"tampered"
            self._add_asset(name, data)

    def _add_asset(self, name: str, data: bytes) -> None:
        assert self.release is not None
        asset_id = self.next_asset
        self.next_asset += 1
        self.asset_bytes[asset_id] = data
        assets = self.release["assets"]
        assert isinstance(assets, list)
        assets.append(
            {
                "id": asset_id,
                "name": name,
                "size": len(data),
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
            }
        )

    def runner(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        command = list(command)
        if command[:2] == ["gh", "api"] and command[-1].endswith("/immutable-releases"):
            self.events.append("immutable-preflight")
            return self._response(command, {"enabled": self.immutable_enabled})
        if command[:2] == ["gh", "api"] and command[2].endswith("releases?per_page=100"):
            pages = [] if self.release is None else [copy.deepcopy(self.release)]
            return self._response(command, [pages])
        if command[:2] == ["gh", "api"] and "/git/ref/tags/" in command[2]:
            if not self.tag_visible:
                return subprocess.CompletedProcess(command, 1, "", "HTTP 404: Not Found")
            return self._response(command, {"object": {"type": "commit", "sha": self.commit}})
        if command[:2] == ["gh", "api"] and command[2].endswith("/releases"):
            self.events.append("create")
            self.release = self._new_release(draft=True, immutable=False)
            return self._response(command, copy.deepcopy(self.release))
        if command[:2] == ["gh", "api"] and command[2].endswith("/releases/42"):
            assert self.release is not None
            if "PATCH" in command:
                self.events.append("publish")
                self.release["draft"] = False
                self.release["immutable"] = True
                self.tag_visible = True
            return self._response(command, copy.deepcopy(self.release))
        if command[:2] == ["gh", "api"] and command[2].startswith("https://uploads.github.com/"):
            name = command[2].split("name=", 1)[1]
            path = Path(command[command.index("--input") + 1])
            self.events.append(f"upload:{name}")
            self._add_asset(name, path.read_bytes())
            assert self.release is not None
            return self._response(command, copy.deepcopy(self.release["assets"][-1]))
        raise AssertionError(f"unexpected command: {command}")

    def downloader(
        self, command: list[str] | tuple[str, ...], destination: Path
    ) -> subprocess.CompletedProcess[str]:
        asset_id = int(list(command)[-1].rsplit("/", 1)[-1])
        destination.write_bytes(self.asset_bytes[asset_id])
        return subprocess.CompletedProcess(command, 0, "", "")


class AuditPublicationTests(unittest.TestCase):
    repository = "squazaryu/tumoflip-fw-packages"
    commit = "a" * 40
    tag = "audit-ledger-20260812-001"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.assets = Path(self.temporary.name)
        for name in ASSET_NAMES:
            (self.assets / name).write_bytes(name.encode())
        self.github = FakeAuditGitHub(self.assets, self.repository, self.commit, self.tag)
        root = Path(__file__).resolve().parents[1]
        self.bootstrap_index = root / "audit/bootstrap/index.json"
        self.bootstrap_ledger = root / "audit/bootstrap/latest.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_release_is_drafted_uploaded_verified_and_made_immutable(self) -> None:
        with mock.patch("tools.publish_audit.verify_release", return_value={"audit": {"sourceTag": "12aug2026"}}), mock.patch(
            "tools.publish_audit._verify_live_chain_state", return_value=False
        ):
            release_id = publish(
                assets_root=self.assets,
                repository=self.repository,
                tag=self.tag,
                publisher_commit=self.commit,
                bootstrap_index=self.bootstrap_index,
                bootstrap_ledger=self.bootstrap_ledger,
                runner=self.github.runner,
                downloader=self.github.downloader,
            )
        self.assertEqual(release_id, 42)
        self.assertEqual(self.github.events[0], "immutable-preflight")
        self.assertLess(self.github.events.index("immutable-preflight"), self.github.events.index("create"))
        self.assertEqual(self.github.events[-1], "publish")
        self.assertTrue(self.github.release["immutable"])

    def test_existing_mutable_public_release_is_terminal(self) -> None:
        self.github.add_public(immutable=False)
        with mock.patch("tools.publish_audit.verify_release", return_value={"audit": {"sourceTag": "12aug2026"}}), mock.patch(
            "tools.publish_audit._verify_live_chain_state", return_value=True
        ):
            with self.assertRaisesRegex(PublishError, "not immutable"):
                publish(
                    assets_root=self.assets,
                    repository=self.repository,
                    tag=self.tag,
                    publisher_commit=self.commit,
                    bootstrap_index=self.bootstrap_index,
                    bootstrap_ledger=self.bootstrap_ledger,
                    runner=self.github.runner,
                    downloader=self.github.downloader,
                )
        self.assertEqual(self.github.events, [])

    def test_existing_tampered_immutable_release_is_terminal(self) -> None:
        self.github.add_public(immutable=True, tamper=ASSET_NAMES[0])
        with mock.patch("tools.publish_audit.verify_release", return_value={"audit": {"sourceTag": "12aug2026"}}), mock.patch(
            "tools.publish_audit._verify_live_chain_state", return_value=True
        ):
            with self.assertRaisesRegex(PublishError, "size differs|digest differs"):
                publish(
                    assets_root=self.assets,
                    repository=self.repository,
                    tag=self.tag,
                    publisher_commit=self.commit,
                    bootstrap_index=self.bootstrap_index,
                    bootstrap_ledger=self.bootstrap_ledger,
                    runner=self.github.runner,
                    downloader=self.github.downloader,
                )

    def test_disabled_immutable_setting_fails_before_any_release_mutation(self) -> None:
        self.github.immutable_enabled = False
        with mock.patch("tools.publish_audit.verify_release", return_value={"audit": {"sourceTag": "12aug2026"}}), mock.patch(
            "tools.publish_audit._verify_live_chain_state", return_value=False
        ) as chain:
            with self.assertRaisesRegex(PublishError, "not enabled"):
                publish(
                    assets_root=self.assets,
                    repository=self.repository,
                    tag=self.tag,
                    publisher_commit=self.commit,
                    bootstrap_index=self.bootstrap_index,
                    bootstrap_ledger=self.bootstrap_ledger,
                    runner=self.github.runner,
                    downloader=self.github.downloader,
                )
        self.assertEqual(self.github.events, ["immutable-preflight"])
        chain.assert_called_once()

    def test_immutable_setting_unauthorized_missing_or_malformed_fails_closed(self) -> None:
        failures = (
            subprocess.CompletedProcess([], 1, "", "HTTP 403: Forbidden"),
            subprocess.CompletedProcess([], 1, "", "HTTP 404: Not Found"),
            subprocess.CompletedProcess([], 0, "[]", ""),
            subprocess.CompletedProcess([], 0, '{"enabled":"true"}', ""),
        )
        for result in failures:
            with self.subTest(result=result):
                with self.assertRaises(PublishError):
                    _require_immutable_releases_enabled(
                        lambda _: result, self.repository
                    )

    def test_exact_concurrent_publication_is_verified_without_a_second_patch(self) -> None:
        calls = 0

        def live_state(**_: object) -> bool:
            nonlocal calls
            calls += 1
            if calls == 2:
                assert self.github.release is not None
                self.github.release["draft"] = False
                self.github.release["immutable"] = True
                self.github.tag_visible = True
                return True
            return False

        with mock.patch("tools.publish_audit.verify_release", return_value={"audit": {"sourceTag": "12aug2026"}}), mock.patch(
            "tools.publish_audit._verify_live_chain_state", side_effect=live_state
        ):
            release_id = publish(
                assets_root=self.assets,
                repository=self.repository,
                tag=self.tag,
                publisher_commit=self.commit,
                bootstrap_index=self.bootstrap_index,
                bootstrap_ledger=self.bootstrap_ledger,
                runner=self.github.runner,
                downloader=self.github.downloader,
            )
        self.assertEqual(release_id, 42)
        self.assertNotIn("publish", self.github.events)

    def test_changed_live_predecessor_before_publication_leaves_a_draft(self) -> None:
        with mock.patch("tools.publish_audit.verify_release", return_value={"audit": {"sourceTag": "12aug2026"}}), mock.patch(
            "tools.publish_audit._verify_live_chain_state",
            side_effect=(False, PublishError("predecessor differs")),
        ):
            with self.assertRaisesRegex(PublishError, "predecessor differs"):
                publish(
                    assets_root=self.assets,
                    repository=self.repository,
                    tag=self.tag,
                    publisher_commit=self.commit,
                    bootstrap_index=self.bootstrap_index,
                    bootstrap_ledger=self.bootstrap_ledger,
                    runner=self.github.runner,
                    downloader=self.github.downloader,
                )
        self.assertIsNotNone(self.github.release)
        self.assertTrue(self.github.release["draft"])
        self.assertNotIn("publish", self.github.events)


if __name__ == "__main__":
    unittest.main()
