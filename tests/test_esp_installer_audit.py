from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import esp_installer_audit as audit


SOURCE = "a" * 40


def _segment(role: str, file_name: str, offset: int, payload: bytes) -> dict[str, object]:
    return {
        "role": role,
        "offset": offset,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "fileName": file_name,
    }


def _manifest(*, duplicate: bool = False, overlap: bool = False) -> tuple[bytes, dict[str, bytes]]:
    payloads = {
        "boot.bin": b"boot-loader",
        "part.bin": b"partition-table",
        "ota.bin": b"ota-data",
        "app.bin": b"application-image",
    }
    segments = [
        _segment("bootloader", "boot.bin", 0x2000, payloads["boot.bin"]),
        _segment("partition-table", "part.bin", 0x8000, payloads["part.bin"]),
        _segment("ota-data", "ota.bin", 0xE000, payloads["ota.bin"]),
        _segment("application", "app.bin", 0x10000, payloads["app.bin"]),
    ]
    if duplicate:
        segments[2]["role"] = "application"
    if overlap:
        segments[1]["offset"] = 0x2001
    manifest = {
        "schemaVersion": 1,
        "kind": "esp32-marauder-installer-release",
        "metadataStatus": "authoritative",
        "sourceRepository": audit.UPSTREAM_REPOSITORY,
        "sourceCommit": SOURCE,
        "channel": "stable",
        "version": "v1.15.1",
        "targets": [
            {
                "id": "marauder-v6-1",
                "displayName": "Marauder v6.1",
                "chipFamily": "ESP32",
                "flash": {"factory": {"segments": segments}},
            }
        ],
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    return manifest_bytes, payloads


class EspInstallerAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.contract = audit.validate_contract(json.loads((root / "contracts/esp-installer-audit.json").read_text()))
        self.baseline = audit.validate_baseline(json.loads((root / "contracts/esp-installer-baseline.json").read_text()))

    def test_checked_in_contract_pins_the_hardware_acceptance_issue(self) -> None:
        self.assertEqual(self.contract["schema"], 2)
        self.assertEqual(
            self.contract["trackingIssue"],
            {"repository": "squazaryu/tumoflip-fw-packages", "number": 29},
        )

    def test_manifest_rejects_an_unsafe_board_identifier(self) -> None:
        manifest, members = _manifest()
        document = json.loads(manifest)
        document["targets"][0]["id"] = "marauder-v6-1`\n@everyone"
        unsafe_manifest = json.dumps(document, sort_keys=True).encode()
        with self.assertRaisesRegex(audit.AuditError, "safe board identifier"):
            audit.build_report(
                self.contract,
                upstream={
                    "repository": audit.UPSTREAM_REPOSITORY,
                    "releaseId": 1,
                    "tag": "v1.15.1",
                    "sourceCommit": SOURCE,
                },
                carrier={
                    "kind": audit.ZIP_NAME,
                    "assetName": audit.ZIP_NAME,
                    "sha256": "b" * 64,
                    "bytes": 1,
                },
                manifest_bytes=unsafe_manifest,
                members={audit.MANIFEST_NAME: unsafe_manifest, **members},
            )

    def test_rejected_report_has_stable_markdown(self) -> None:
        report = {
            "schema": 1,
            "kind": "espInstallerAuditReport",
            "status": "rejected",
            "automaticFlashPackageAuthorization": False,
            "error": "unsafe `manifest`\nvalue",
        }
        markdown = audit.render_markdown(report)
        self.assertTrue(markdown.startswith(audit.ISSUE_MARKER))
        self.assertIn("Status: **rejected**", markdown)
        self.assertIn("Error: `unsafe manifest value`", markdown)

    def _carrier(self, manifest_bytes: bytes, members: dict[str, bytes]) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="esp-audit-test-"))
        path = directory / audit.ZIP_NAME
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(audit.MANIFEST_NAME, manifest_bytes)
            for name, payload in members.items():
                archive.writestr(name, payload)
        return path

    def test_zip_recipe_is_verified_but_not_authorized_without_hardware_gate(self) -> None:
        manifest, members = _manifest()
        report = audit.build_report(
            self.contract,
            upstream={"repository": audit.UPSTREAM_REPOSITORY, "releaseId": 1, "tag": "v1.15.1", "sourceCommit": SOURCE},
            carrier={"kind": audit.ZIP_NAME, "assetName": audit.ZIP_NAME, "sha256": "b" * 64, "bytes": 1},
            manifest_bytes=manifest,
            members={audit.MANIFEST_NAME: manifest, **members},
            generated_at="2026-08-25T12:00:00Z",
        )
        self.assertEqual(report["status"], "needsReview")
        self.assertFalse(report["automaticFlashPackageAuthorization"])
        self.assertEqual(report["candidates"][0]["decision"], "needsReview")
        self.assertIn("policy status is needsReview", report["candidates"][0]["reasons"])

    def test_direct_manifest_is_supported_but_requires_carrier_bytes(self) -> None:
        manifest, _ = _manifest()
        report = audit.build_report(
            self.contract,
            upstream={"repository": audit.UPSTREAM_REPOSITORY, "releaseId": 2, "tag": "v1.15.1", "sourceCommit": SOURCE},
            carrier={"kind": audit.MANIFEST_NAME, "assetName": audit.MANIFEST_NAME, "sha256": "c" * 64, "bytes": len(manifest)},
            manifest_bytes=manifest,
            members={audit.MANIFEST_NAME: manifest},
        )
        self.assertEqual(report["status"], "needsReview")
        self.assertTrue(any("carrier does not include segment bytes" in reason for reason in report["candidates"][0]["reasons"]))

    def test_duplicate_role_is_rejected(self) -> None:
        manifest, members = _manifest(duplicate=True)
        with self.assertRaises(audit.AuditError):
            audit.build_report(
                self.contract,
                upstream={"repository": audit.UPSTREAM_REPOSITORY, "releaseId": 3, "tag": "v1.15.1", "sourceCommit": SOURCE},
                carrier={"kind": audit.ZIP_NAME, "assetName": audit.ZIP_NAME, "sha256": "d" * 64, "bytes": 1},
                manifest_bytes=manifest,
                members={audit.MANIFEST_NAME: manifest, **members},
            )

    def test_overlap_is_rejected(self) -> None:
        manifest, members = _manifest(overlap=True)
        with self.assertRaises(audit.AuditError):
            audit.build_report(
                self.contract,
                upstream={"repository": audit.UPSTREAM_REPOSITORY, "releaseId": 4, "tag": "v1.15.1", "sourceCommit": SOURCE},
                carrier={"kind": audit.ZIP_NAME, "assetName": audit.ZIP_NAME, "sha256": "e" * 64, "bytes": 1},
                manifest_bytes=manifest,
                members={audit.MANIFEST_NAME: manifest, **members},
            )

    def test_release_identity_changes_fingerprint(self) -> None:
        manifest, members = _manifest()
        common = {
            "carrier": {"kind": audit.ZIP_NAME, "assetName": audit.ZIP_NAME, "sha256": "f" * 64, "bytes": 1},
            "manifest_bytes": manifest,
            "members": {audit.MANIFEST_NAME: manifest, **members},
        }
        first = audit.build_report(self.contract, upstream={"repository": audit.UPSTREAM_REPOSITORY, "releaseId": 5, "tag": "v1.15.1", "sourceCommit": SOURCE}, **common)
        second = audit.build_report(self.contract, upstream={"repository": audit.UPSTREAM_REPOSITORY, "releaseId": 6, "tag": "v1.15.1", "sourceCommit": SOURCE}, **common)
        self.assertNotEqual(first["candidates"][0]["fingerprint"], second["candidates"][0]["fingerprint"])

    def test_baseline_detects_mutated_release_identity(self) -> None:
        manifest, members = _manifest()
        upstream = {"repository": audit.UPSTREAM_REPOSITORY, "releaseId": 375460837, "tag": "v1.15.1", "sourceCommit": SOURCE}
        carrier = {"kind": audit.ZIP_NAME, "assetName": audit.ZIP_NAME, "sha256": "f" * 64, "bytes": 1}
        message = audit._identity_change(self.baseline, upstream, carrier, audit._hash_bytes(manifest))
        self.assertIsNotNone(message)
        report = audit.build_report(
            self.contract,
            upstream=upstream,
            carrier=carrier,
            manifest_bytes=manifest,
            members={audit.MANIFEST_NAME: manifest, **members},
            identity_change=message,
        )
        self.assertEqual(report["status"], "needsReview")
        self.assertIn("release identity changed", report["identityChange"])
        self.assertTrue(any("checked-in baseline" in reason for reason in report["candidates"][0]["reasons"]))

    def test_zip_rejects_traversal(self) -> None:
        manifest, _ = _manifest()
        directory = Path(tempfile.mkdtemp(prefix="esp-audit-test-"))
        path = directory / audit.ZIP_NAME
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(audit.MANIFEST_NAME, manifest)
            archive.writestr("../escape.bin", b"unsafe")
        with self.assertRaises(audit.AuditError):
            audit._carrier(path)


if __name__ == "__main__":
    unittest.main()
