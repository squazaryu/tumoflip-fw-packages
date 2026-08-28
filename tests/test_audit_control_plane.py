from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from tools.audit_bootstrap import BootstrapError, validate_index, verify
from tools.audit_inputs import (
    InputError,
    select_rolling_firmware_release,
    validate_source_fixture,
    validate_targets,
)
from tools.audit_release import (
    CHECKSUM_ASSET,
    LEDGER_ASSET,
    PROVENANCE_ASSET,
    AuditReleaseError,
    canonical_sha256,
    prepare_release,
    sha256,
    validate_evidence,
    verify_release,
)
from tools.publish_audit_branch import BranchError, prepare_tree
from tools.resolve_audit_source import resolve
from tools.tumoflip import protected_app_audit as audit_tool


class AuditControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_checked_in_bootstrap_is_strict_and_content_addressed(self) -> None:
        index = verify(
            index_path=self.root / "audit/bootstrap/index.json",
            normalized_latest=self.root / "audit/bootstrap/latest.json",
            legacy_root=None,
        )
        self.assertEqual(index["legacy"]["commit"], "a95ee7dc6d8add5e5f4b25e7abbb426634fd0dca")
        self.assertEqual(index["legacy"]["tree"], "0d8fea0355141001ca6cb234189b2eb4d5c7f1ac")
        self.assertEqual(
            index["legacy"]["historyTree"],
            "f688ddab98827bf5025beeef4d5c7144a525784e",
        )
        self.assertEqual(len(index["legacy"]["history"]), 11)
        self.assertEqual(
            sum(not item["clientStrict"] for item in index["legacy"]["history"]),
            4,
        )

    def test_bootstrap_classification_cannot_hide_legacy_generator_snapshot(self) -> None:
        index = json.loads((self.root / "audit/bootstrap/index.json").read_text())
        index["clientContract"]["legacyGeneratorSnapshots"].pop()
        with self.assertRaisesRegex(BootstrapError, "classification differs"):
            validate_index(index)

    def test_target_contract_pins_only_independent_mirror_release_ids(self) -> None:
        contract = validate_targets(
            json.loads((self.root / "contracts/protected-audit-targets.json").read_text())
        )
        packages = {item["releaseTag"]: item for item in contract["packages"]}
        self.assertEqual(
            packages["fw-packages-stable-001"]["repository"],
            "squazaryu/tumoflip-fw-packages",
        )
        self.assertNotEqual(
            packages["fw-packages-stable-001"]["tagCommit"],
            packages["fw-packages-stable-001"]["manifestSourceCommit"],
        )
        self.assertEqual(
            {item["repository"] for item in packages.values()},
            {"squazaryu/tumoflip-fw-packages"},
        )
        self.assertEqual(
            {item["githubReleaseId"] for item in packages.values()},
            {369786610, 369803658, 370143096, 370143158, 371276208, 375940307},
        )
        for tag, item in packages.items():
            self.assertNotEqual(item["tagCommit"], item["manifestSourceCommit"])
            if tag == "fw-packages-stable-003":
                self.assertNotIn("migrationProvenance", item["assets"])
                self.assertEqual(
                    item["manifestSourceCommit"],
                    "8ab2ccdf7a34bbf3e07f2d4cbd459de1c6de8758",
                )
            elif tag == "fw-packages-stable-004":
                self.assertIn("catalogProvenance", item["assets"])
                self.assertNotIn("migrationProvenance", item["assets"])
                self.assertEqual(
                    item["manifestSourceCommit"],
                    "eba1cfd8cfb022d788433bd540a82cc2e4e25245",
                )
            else:
                self.assertIn("migrationProvenance", item["assets"])
        self.assertEqual(
            contract["implementations"]["stable"]["commit"],
            "d34ecaf1714ec5b5516eb6619d00d9e0d5462e15",
        )
        self.assertEqual(
            contract["implementations"]["dev"]["commit"],
            "833efd8e474afd1ba75c9fc57da7096bad107495",
        )
        self.assertEqual(contract["implementation"], contract["implementations"]["dev"])
        firmware = {item["releaseTag"]: item for item in contract["firmware"]}
        self.assertEqual(
            firmware["v1.0.6"]["asset"]["sha256"],
            "1c4d89691cd373e3b074cd4fce6a1e4967fb70ecb84faf676b6a510ab5346335",
        )
        self.assertEqual(
            firmware["t-dev-007-001"]["tagCommit"],
            "adf7431acdd735dcd3d714fa339d76cef71df0a3",
        )
        self.assertEqual(
            contract["rollingFirmware"],
            [
                {"repository": "squazaryu/tumoflip", "channel": "stable"},
                {"repository": "squazaryu/tumoflip", "channel": "dev"},
            ],
        )

    def test_target_contract_rejects_duplicate_release_identity(self) -> None:
        contract = json.loads((self.root / "contracts/protected-audit-targets.json").read_text())
        contract["packages"].append(copy.deepcopy(contract["packages"][0]))
        with self.assertRaisesRegex(InputError, "duplicated"):
            validate_targets(contract)

    def test_target_contract_rejects_duplicate_rolling_firmware_channel(self) -> None:
        contract = json.loads((self.root / "contracts/protected-audit-targets.json").read_text())
        contract["rollingFirmware"][1]["channel"] = "stable"
        with self.assertRaisesRegex(InputError, "rolling firmware selector"):
            validate_targets(contract)

    def test_rolling_firmware_selector_pins_latest_exact_dev_release(self) -> None:
        def release(release_id: int, tag: str, published_at: str, prerelease: bool) -> dict[str, Any]:
            return {
                "id": release_id,
                "tag_name": tag,
                "published_at": published_at,
                "draft": False,
                "prerelease": prerelease,
                "assets": [
                    {
                        "id": release_id + 1000,
                        "name": f"flipper-z-f7-update-{tag}.tgz",
                        "size": 123,
                        "digest": "sha256:" + "a" * 64,
                    }
                ],
            }

        selected = select_rolling_firmware_release(
            {"repository": "squazaryu/tumoflip", "channel": "dev"},
            [
                release(1, "t-dev-007-001", "2026-08-20T12:00:00Z", True),
                release(2, "t-dev-008-001", "2026-08-21T12:00:00Z", True),
                release(3, "v1.0.7", "2026-08-22T12:00:00Z", False),
                {"id": 4, "tag_name": "t-dev-009-001", "draft": True, "prerelease": True},
            ],
        )
        self.assertEqual(selected["releaseTag"], "t-dev-008-001")
        self.assertEqual(selected["githubReleaseId"], 2)
        self.assertEqual(selected["asset"]["sha256"], "a" * 64)

    def test_rolling_firmware_selector_rejects_ambiguous_publication_order(self) -> None:
        releases = [
            {
                "id": release_id,
                "tag_name": f"t-dev-007-00{release_id}",
                "published_at": "2026-08-21T12:00:00Z",
                "draft": False,
                "prerelease": True,
                "assets": [
                    {
                        "id": release_id + 1000,
                        "name": f"flipper-z-f7-update-t-dev-007-00{release_id}.tgz",
                        "size": 123,
                        "digest": "sha256:" + "a" * 64,
                    }
                ],
            }
            for release_id in (1, 2)
        ]
        with self.assertRaisesRegex(InputError, "ambiguous"):
            select_rolling_firmware_release(
                {"repository": "squazaryu/tumoflip", "channel": "dev"}, releases
            )

    def test_rolling_firmware_selector_rejects_invalid_latest_release_metadata(self) -> None:
        releases = [
            {
                "id": 1,
                "tag_name": "t-dev-007-001",
                "published_at": "2026-08-20T12:00:00Z",
                "draft": False,
                "prerelease": True,
                "assets": [],
            },
            {
                "id": 2,
                "tag_name": "t-dev-008-001",
                "published_at": None,
                "draft": False,
                "prerelease": True,
                "assets": [],
            },
        ]
        with self.assertRaisesRegex(InputError, "metadata is invalid: t-dev-008-001"):
            select_rolling_firmware_release(
                {"repository": "squazaryu/tumoflip", "channel": "dev"}, releases
            )

    def test_e2e_fixture_is_exact_schema_two_source_input(self) -> None:
        fixture = validate_source_fixture(
            json.loads((self.root / "contracts/protected-audit-e2e-12aug.json").read_text())
        )
        self.assertEqual(fixture["current"]["releaseTag"], "12aug2026")
        self.assertEqual(fixture["current"]["tagCommit"], "585b144ac5b4d9a48a0e5a74570a6584353fdbba")
        self.assertEqual(fixture["canonicalIssue"]["number"], 308)

    def test_release_evidence_accepts_mirror_tag_distinct_from_source(self) -> None:
        evidence = {
            "schema": 1,
            "kind": "protectedAppAuditEvidence",
            "control": {"repository": "squazaryu/tumoflip-fw-packages", "commit": "a" * 40},
            "implementation": {"repository": "squazaryu/tumoflip", "commit": "b" * 40},
            "community": {
                "repository": audit_tool.SOURCE_REPOSITORY,
                "tag": "12aug2026",
                "commit": "c" * 40,
                "archives": {
                    "base": {"fileName": "all-the-apps-base.zip", "sha256": "d" * 64},
                    "extra": {"fileName": "all-the-apps-extra.zip", "sha256": "e" * 64},
                },
            },
            "issue": {"number": 1, "url": "https://github.com/squazaryu/tumoflip-fw-packages/issues/1"},
            "packages": [
                {
                    "repository": "squazaryu/tumoflip-fw-packages",
                    "releaseTag": "fw-packages-stable-001",
                    "githubReleaseId": 10,
                    "tagCommit": "f" * 40,
                    "sourceCommit": "1" * 40,
                    "manifestReleaseId": "2" * 64,
                    "manifestSHA256": "3" * 64,
                    "archiveSHA256": "4" * 64,
                    "packageRelease": {
                        "catalog_release_tag": "fw-packages-stable-001",
                        "source_commit": "1" * 40,
                    },
                }
            ],
            "firmware": [
                {
                    "repository": "squazaryu/tumoflip",
                    "releaseTag": "v1.0.4",
                    "githubReleaseId": 20,
                    "tagCommit": "5" * 40,
                    "updaterSHA256": "6" * 64,
                    "resourceManifestSHA256": "7" * 64,
                    "resourcesSHA256": "8" * 64,
                }
            ],
        }
        validate_evidence(evidence)
        evidence["packages"][0]["packageRelease"]["source_commit"] = "9" * 40
        with self.assertRaisesRegex(AuditReleaseError, "provenance differs"):
            validate_evidence(evidence)

    def _prepared_release(self, temporary: str) -> tuple[Path, str, str]:
        ledger_path = self.root / "audit/bootstrap/latest.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        audit = ledger["audits"][-1]
        targets = json.loads(
            (self.root / "contracts/protected-audit-targets.json").read_text(
                encoding="utf-8"
            )
        )
        packages = []
        for target in targets["packages"]:
            compatible: dict[tuple[str, str, str], dict[str, str]] = {}
            for entry in audit["entries"]:
                for provenance in entry.get("targetProvenance", []):
                    if (
                        provenance["containerKind"] == "fwPackagesCompatibleBuild"
                        and provenance.get("compatibilityCatalogTag")
                        == target["releaseTag"]
                    ):
                        source = {
                            "release_tag": provenance["releaseTag"],
                            "manifest_sha256": provenance["manifestSHA256"],
                            "source_commit": provenance["targetSourceCommit"],
                        }
                        compatible[tuple(source.values())] = source
            packages.append(
                {
                    "repository": target["repository"],
                    "releaseTag": target["releaseTag"],
                    "githubReleaseId": target["githubReleaseId"],
                    "tagCommit": target["tagCommit"],
                    "sourceCommit": target["manifestSourceCommit"],
                    "manifestReleaseId": target["manifestReleaseId"],
                    "manifestSHA256": target["assets"]["manifest"]["sha256"],
                    "archiveSHA256": target["assets"]["archive"]["sha256"],
                    "packageRelease": {
                        "catalog_release_tag": target["releaseTag"],
                        "source_commit": target["manifestSourceCommit"],
                        "compatible_releases": list(compatible.values()),
                    },
                }
            )
        firmware = [
            {
                "repository": target["repository"],
                "releaseTag": target["releaseTag"],
                "githubReleaseId": target["githubReleaseId"],
                "tagCommit": target["tagCommit"],
                "updaterSHA256": target["asset"]["sha256"],
                "resourceManifestSHA256": target["resourceManifestSHA256"],
                "resourcesSHA256": target["resourcesSHA256"],
            }
            for target in targets["firmware"]
        ]
        archives = {item["pack"]: item for item in audit["archives"]}
        repository = "squazaryu/tumoflip-fw-packages"
        commit = "a" * 40
        evidence = {
            "schema": 1,
            "kind": "protectedAppAuditEvidence",
            "control": {"repository": repository, "commit": commit},
            "implementation": targets["implementation"],
            "community": {
                "repository": audit_tool.SOURCE_REPOSITORY,
                "tag": audit["sourceTag"],
                "commit": audit["sourceCommit"],
                "archives": {
                    pack: {
                        "fileName": archives[pack]["fileName"],
                        "sha256": archives[pack]["sha256"],
                    }
                    for pack in ("base", "extra")
                },
            },
            "issue": {
                "number": int(audit["auditIssue"].rsplit("/", 1)[-1]),
                "url": audit["auditIssue"],
            },
            "packages": packages,
            "firmware": firmware,
        }
        root = Path(temporary)
        audit_path = root / "audit.json"
        evidence_path = root / "evidence.json"
        predecessor_path = root / "predecessor.json"
        release_path = root / "release"
        audit_tool.write_json(audit_path, audit)
        audit_tool.write_json(evidence_path, evidence)
        audit_tool.write_json(
            predecessor_path,
            {
                "schema": 1,
                "kind": "bootstrap",
                "indexSHA256": sha256(self.root / "audit/bootstrap/index.json"),
                "ledgerSHA256": sha256(ledger_path),
            },
        )
        tag = "audit-ledger-20260812-001"
        prepare_release(
            ledger_path=ledger_path,
            audit_path=audit_path,
            evidence_path=evidence_path,
            predecessor_path=predecessor_path,
            output=release_path,
            tag=tag,
            publisher_repository=repository,
            publisher_commit=commit,
        )
        return release_path, repository, commit

    @staticmethod
    def _rewrite_provenance(
        root: Path, mutate: Callable[[dict[str, Any]], object]
    ) -> None:
        path = root / PROVENANCE_ASSET
        provenance = json.loads(path.read_text(encoding="utf-8"))
        mutate(provenance)
        provenance["evidenceSHA256"] = canonical_sha256(provenance["evidence"])
        path.write_text(
            json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (root / CHECKSUM_ASSET).write_text(
            f"{sha256(root / LEDGER_ASSET)}  {LEDGER_ASSET}\n"
            f"{sha256(path)}  {PROVENANCE_ASSET}\n",
            encoding="utf-8",
        )

    def test_release_verifier_requires_semantic_audit_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, repository, commit = self._prepared_release(temporary)
            self._rewrite_provenance(
                release, lambda provenance: provenance.pop("auditSemanticSHA256")
            )
            with self.assertRaisesRegex(AuditReleaseError, "semantic SHA-256.*invalid"):
                verify_release(
                    root=release,
                    tag="audit-ledger-20260812-001",
                    publisher_repository=repository,
                    publisher_commit=commit,
                )

    def test_release_verifier_rejects_wrong_semantic_audit_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, repository, commit = self._prepared_release(temporary)
            self._rewrite_provenance(
                release,
                lambda provenance: provenance.__setitem__(
                    "auditSemanticSHA256", "0" * 64
                ),
            )
            with self.assertRaisesRegex(AuditReleaseError, "semantic SHA-256 differs"):
                verify_release(
                    root=release,
                    tag="audit-ledger-20260812-001",
                    publisher_repository=repository,
                    publisher_commit=commit,
                )

    def test_release_verifier_rejects_evidence_for_unrelated_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, repository, commit = self._prepared_release(temporary)
            self._rewrite_provenance(
                release,
                lambda provenance: provenance["evidence"]["community"].__setitem__(
                    "commit", "0" * 40
                ),
            )
            with self.assertRaisesRegex(AuditReleaseError, "exactly one audit bound"):
                verify_release(
                    root=release,
                    tag="audit-ledger-20260812-001",
                    publisher_repository=repository,
                    publisher_commit=commit,
                )

    def test_release_verifier_returns_the_release_bound_audit_and_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, repository, commit = self._prepared_release(temporary)
            verified = verify_release(
                root=release,
                tag="audit-ledger-20260812-001",
                publisher_repository=repository,
                publisher_commit=commit,
            )
            self.assertEqual(verified["audit"]["sourceTag"], "12aug2026")
            self.assertEqual(verified["predecessor"]["kind"], "bootstrap")
            self.assertEqual(verified["publisher"]["commit"], commit)

    def test_release_verifier_rejects_missing_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, repository, commit = self._prepared_release(temporary)
            self._rewrite_provenance(
                release, lambda provenance: provenance.pop("predecessor")
            )
            with self.assertRaisesRegex(AuditReleaseError, "predecessor contract"):
                verify_release(
                    root=release,
                    tag="audit-ledger-20260812-001",
                    publisher_repository=repository,
                    publisher_commit=commit,
                )

    def test_prepare_tree_is_idempotent_for_existing_semantic_audit(self) -> None:
        ledger = json.loads((self.root / "audit/bootstrap/latest.json").read_text())
        audit = ledger["audits"][-1]
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            (tree / "history").mkdir()
            normalized = audit_tool.merge_ledger(ledger, audit)
            (tree / "latest.json").write_text(
                json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (tree / "history/existing.json").write_text(
                json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            audit_path = tree / "audit.json"
            audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
            released = tree / "released.json"
            audit_tool.write_json(released, normalized)
            self.assertEqual(
                prepare_tree(root=tree, audit_path=audit_path, released_ledger=released),
                [],
            )

    def test_prepare_tree_rejects_release_raw_divergence(self) -> None:
        ledger = json.loads((self.root / "audit/bootstrap/latest.json").read_text())
        audit = ledger["audits"][-1]
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            (tree / "history").mkdir()
            audit_tool.write_json(tree / "latest.json", ledger)
            audit_tool.write_json(tree / "audit.json", audit)
            (tree / "released.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(BranchError, "differs"):
                prepare_tree(
                    root=tree,
                    audit_path=tree / "audit.json",
                    released_ledger=tree / "released.json",
                )


if __name__ == "__main__":
    unittest.main()
