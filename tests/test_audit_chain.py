from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.audit_chain import (
    ChainError,
    RemoteAuditRelease,
    allocate_tag,
    bootstrap_identity,
    resolve_current,
    validate_materialized_chain,
)
from tools.audit_release import ASSET_NAMES
from tools.tumoflip import protected_app_audit as audit_tool


class AuditChainTests(unittest.TestCase):
    repository = "squazaryu/tumoflip-fw-packages"
    publisher = "a" * 40

    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.bootstrap_index = self.root / "audit/bootstrap/index.json"
        self.bootstrap_ledger = self.root / "audit/bootstrap/latest.json"
        self.ledger = json.loads(self.bootstrap_ledger.read_text(encoding="utf-8"))
        self.bootstrap = bootstrap_identity(self.bootstrap_index, self.bootstrap_ledger)
        self.temporary = tempfile.TemporaryDirectory()
        self.scratch = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _new_audit(self, *, tag: str = "13aug2026", marker: str = "b") -> dict:
        value = copy.deepcopy(self.ledger["audits"][-1])
        value["sequence"] = max(item["sequence"] for item in self.ledger["audits"]) + 1
        value["sourceTag"] = tag
        value["sourceCommit"] = marker * 40
        value["generatedAt"] = "2026-08-13T10:00:00Z"
        value["auditIssue"] = "https://github.com/squazaryu/tumoflip-fw-packages/issues/400"
        for index, archive in enumerate(value["archives"]):
            archive["sha256"] = str(index + 1) * 64
        audit_tool.validate_audit(value)
        return value

    def _release(
        self,
        *,
        tag: str,
        release_id: int,
        predecessor: dict,
        parent_ledger: dict,
        audit: dict,
        ledger: dict | None = None,
    ) -> RemoteAuditRelease:
        release_root = self.scratch / f"release-{release_id}"
        release_root.mkdir()
        for name in ASSET_NAMES:
            (release_root / name).write_text(name, encoding="utf-8")
        merged = audit_tool.merge_ledger(parent_ledger, audit) if ledger is None else ledger
        return RemoteAuditRelease(
            release_id=release_id,
            tag=tag,
            tag_commit=f"{release_id:x}".zfill(40),
            root=release_root,
            verified={
                "predecessor": predecessor,
                "audit": audit,
                "ledgerSHA256": f"{release_id:x}".zfill(64),
                "provenanceSHA256": f"{release_id + 100:x}".zfill(64),
            },
            ledger=merged,
        )

    def test_chain_is_bootstrap_anchored_linear_and_history_preserving(self) -> None:
        genesis_audit = self.ledger["audits"][-1]
        genesis = self._release(
            tag="audit-ledger-20260812-001",
            release_id=1,
            predecessor=self.bootstrap,
            parent_ledger=self.ledger,
            audit=genesis_audit,
        )
        successor_audit = self._new_audit()
        successor = self._release(
            tag="audit-ledger-20260813-001",
            release_id=2,
            predecessor=genesis.predecessor_identity(),
            parent_ledger=genesis.ledger,
            audit=successor_audit,
        )
        ordered = validate_materialized_chain(
            [successor, genesis],
            expected_bootstrap=self.bootstrap,
            bootstrap_ledger=self.ledger,
        )
        self.assertEqual([item.tag for item in ordered], [genesis.tag, successor.tag])

        fork = self._release(
            tag="audit-ledger-20260814-001",
            release_id=3,
            predecessor=genesis.predecessor_identity(),
            parent_ledger=genesis.ledger,
            audit=self._new_audit(tag="14aug2026", marker="c"),
        )
        with self.assertRaisesRegex(ChainError, "forks"):
            validate_materialized_chain(
                [genesis, successor, fork],
                expected_bootstrap=self.bootstrap,
                bootstrap_ledger=self.ledger,
            )

    def test_chain_rejects_orphan_and_historical_rewrite(self) -> None:
        genesis = self._release(
            tag="audit-ledger-20260812-001",
            release_id=1,
            predecessor=self.bootstrap,
            parent_ledger=self.ledger,
            audit=self.ledger["audits"][-1],
        )
        orphan_predecessor = copy.deepcopy(genesis.predecessor_identity())
        orphan_predecessor["githubReleaseId"] = 999
        orphan = self._release(
            tag="audit-ledger-20260813-001",
            release_id=2,
            predecessor=orphan_predecessor,
            parent_ledger=genesis.ledger,
            audit=self._new_audit(),
        )
        with self.assertRaisesRegex(ChainError, "predecessor is missing"):
            validate_materialized_chain(
                [genesis, orphan],
                expected_bootstrap=self.bootstrap,
                bootstrap_ledger=self.ledger,
            )

        rewritten = copy.deepcopy(genesis.ledger)
        rewritten["audits"][0]["sourceCommit"] = "f" * 40
        successor = self._release(
            tag="audit-ledger-20260813-001",
            release_id=3,
            predecessor=genesis.predecessor_identity(),
            parent_ledger=genesis.ledger,
            audit=self._new_audit(),
            ledger=rewritten,
        )
        with self.assertRaisesRegex(ChainError, "rewrites cumulative history"):
            validate_materialized_chain(
                [genesis, successor],
                expected_bootstrap=self.bootstrap,
                bootstrap_ledger=self.ledger,
            )

    def test_tag_allocation_is_contiguous_and_bounded(self) -> None:
        audit = self._new_audit()
        first = self._release(
            tag="audit-ledger-20260813-001",
            release_id=1,
            predecessor=self.bootstrap,
            parent_ledger=self.ledger,
            audit=audit,
        )
        self.assertEqual(allocate_tag("13aug2026", [first]), "audit-ledger-20260813-002")
        gap = copy.copy(first)
        object.__setattr__(gap, "tag", "audit-ledger-20260813-003")
        with self.assertRaisesRegex(ChainError, "not contiguous"):
            allocate_tag("13aug2026", [gap])
        exhausted = copy.copy(first)
        object.__setattr__(exhausted, "tag", "audit-ledger-20260813-999")
        releases = [
            copy.copy(exhausted)
            for _ in range(999)
        ]
        for revision, item in enumerate(releases, 1):
            object.__setattr__(item, "tag", f"audit-ledger-20260813-{revision:03d}")
        with self.assertRaisesRegex(ChainError, "exhausted"):
            allocate_tag("13aug2026", releases)

    def test_resolution_publishes_genesis_reuses_noop_and_reaudits_as_next_revision(self) -> None:
        audit = self._new_audit()
        audit_path = self.scratch / "audit.json"
        audit_tool.write_json(audit_path, audit)
        first_output = self.scratch / "first"
        with mock.patch("tools.audit_chain.resolve_remote_chain", return_value=[]):
            first = resolve_current(
                repository=self.repository,
                publisher_commit=self.publisher,
                bootstrap_index=self.bootstrap_index,
                bootstrap_ledger=self.bootstrap_ledger,
                audit_path=audit_path,
                output=first_output,
            )
        self.assertEqual(first["mode"], "publish")
        self.assertEqual(first["auditReleaseTag"], "audit-ledger-20260813-001")
        self.assertEqual(first["predecessor"], self.bootstrap)

        head = self._release(
            tag=first["auditReleaseTag"],
            release_id=1,
            predecessor=self.bootstrap,
            parent_ledger=self.ledger,
            audit=audit,
        )
        reuse_output = self.scratch / "reuse"
        no_op = copy.deepcopy(audit)
        no_op["generatedAt"] = "2026-08-13T11:00:00Z"
        audit_tool.write_json(audit_path, no_op)
        with mock.patch("tools.audit_chain.resolve_remote_chain", return_value=[head]):
            reuse = resolve_current(
                repository=self.repository,
                publisher_commit="c" * 40,
                bootstrap_index=self.bootstrap_index,
                bootstrap_ledger=self.bootstrap_ledger,
                audit_path=audit_path,
                output=reuse_output,
            )
        self.assertEqual(reuse["mode"], "reuse")
        self.assertEqual(reuse["publisherCommit"], head.tag_commit)
        self.assertEqual(
            {path.name for path in (reuse_output / "release-assets").iterdir()},
            set(ASSET_NAMES),
        )

        changed = self._new_audit(marker="d")
        changed_path = self.scratch / "changed.json"
        audit_tool.write_json(changed_path, changed)
        with mock.patch("tools.audit_chain.resolve_remote_chain", return_value=[head]):
            replacement = resolve_current(
                repository=self.repository,
                publisher_commit=self.publisher,
                bootstrap_index=self.bootstrap_index,
                bootstrap_ledger=self.bootstrap_ledger,
                audit_path=changed_path,
                output=self.scratch / "replacement",
            )
        self.assertEqual(replacement["mode"], "publish")
        self.assertEqual(replacement["auditReleaseTag"], "audit-ledger-20260813-002")


if __name__ == "__main__":
    unittest.main()
