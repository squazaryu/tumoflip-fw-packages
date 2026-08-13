from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.catalog_contract import ContractError, manifest_release_id, sha256
from tools.native_release import (
    build_native_release,
    finalize_native_release,
    load_native_plan,
    verify_native_release,
)


class NativeReleaseTests(unittest.TestCase):
    source_commit = "a6bb38f027f5f17f2752d5dfca157478472b5c10"
    publisher_commit = "b" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = Path(__file__).resolve().parents[1]
        self.fixture = self.repository / "tests/fixtures/native"
        self.plan = load_native_plan(
            self.repository,
            "dev",
            9,
            self.source_commit,
            self.publisher_commit,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _source_output(self, name: str = "release") -> Path:
        output = self.root / name
        output.mkdir()
        shutil.copy2(self.fixture / "legacy-manifest.json", output / "tumoflip-packages.json")
        resources = self.fixture / "resources"
        manifest_path = output / "tumoflip-packages.json"
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
        with zipfile.ZipFile(output / "tumoflip-packages.zip", "w") as archive:
            for entries in manifest["packages"].values():
                for entry in entries:
                    archive.write(resources / entry["source"], entry["source"])
        return output

    def _base_output(self) -> tuple[Path, dict[str, object]]:
        base = self._source_output("base")
        manifest_path = base / "tumoflip-packages.json"
        manifest = json.loads(manifest_path.read_text())
        release = manifest["package_release"]
        release.update(
            {
                "catalog_channel": "dev",
                "catalog_revision": 8,
                "catalog_release_tag": "fw-packages-dev-008",
            }
        )
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        checksum = base / "fw-packages-dev-008-SHA256SUMS"
        checksum.write_text(
            "".join(
                f"{sha256(base / name)}  {name}\n"
                for name in ("tumoflip-packages.json", "tumoflip-packages.zip")
            )
        )
        contract: dict[str, object] = {
            "tag": "fw-packages-dev-008",
            "revision": 8,
            "prerelease": True,
            "releaseId": manifest["release_id"],
            "tagCommit": "c" * 40,
            "sourceCommit": "c" * 40,
            "targetFirmwareTag": "t-dev-004-015",
            "targetFirmwareCommit": "d" * 40,
            "assets": {
                checksum.name: sha256(checksum),
                "tumoflip-packages.json": sha256(manifest_path),
                "tumoflip-packages.zip": sha256(base / "tumoflip-packages.zip"),
            },
        }
        return base, contract

    def test_legacy_composition_is_preserved_and_only_identity_changes(self) -> None:
        output = self._source_output()
        before = json.loads((output / "tumoflip-packages.json").read_text())
        with zipfile.ZipFile(output / "tumoflip-packages.zip") as archive:
            payloads_before = {
                name: archive.read(name) for name in sorted(archive.namelist())
            }

        finalize_native_release(output, self.plan)

        after = json.loads((output / "tumoflip-packages.json").read_text())
        self.assertEqual(after["firmware"], before["firmware"])
        self.assertEqual(after["packages"], before["packages"])
        self.assertEqual(after["cleanup"], before["cleanup"])
        self.assertEqual(after["artifacts"], {})
        with zipfile.ZipFile(output / "tumoflip-packages.zip") as archive:
            payloads_after = {
                name: archive.read(name) for name in sorted(archive.namelist())
            }
            self.assertTrue(
                all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
            )
        self.assertEqual(payloads_after, payloads_before)
        for key, value in before["package_release"].items():
            self.assertEqual(after["package_release"][key], value)
        self.assertEqual(after["package_release"]["catalog_channel"], "dev")
        self.assertEqual(after["package_release"]["catalog_revision"], 9)
        self.assertEqual(after["package_release"]["catalog_release_tag"], "fw-packages-dev-009")
        self.assertEqual(
            after["package_release"]["source_firmware_version"], "t-dev-004-015"
        )
        verify_native_release(output, self.plan)

    def test_zip_is_reproducible_across_source_order_and_timestamp(self) -> None:
        first = self._source_output()
        second = self.root / "second"
        second.mkdir()
        shutil.copy2(first / "tumoflip-packages.json", second / "tumoflip-packages.json")
        manifest = json.loads((second / "tumoflip-packages.json").read_text())
        resources = self.fixture / "resources"
        entries = [entry for group in manifest["packages"].values() for entry in group]
        with zipfile.ZipFile(second / "tumoflip-packages.zip", "w") as archive:
            for entry in reversed(entries):
                info = zipfile.ZipInfo(entry["source"], date_time=(2026, 8, 13, 12, 34, 56))
                archive.writestr(info, (resources / entry["source"]).read_bytes())

        finalize_native_release(first, self.plan)
        finalize_native_release(second, self.plan)

        self.assertEqual(
            sha256(first / "tumoflip-packages.zip"),
            sha256(second / "tumoflip-packages.zip"),
        )

    def test_target_firmware_mismatch_is_terminal_without_sidecars(self) -> None:
        output = self._source_output()
        manifest_path = output / "tumoflip-packages.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["firmware"]["api"] = "99.0"
        unsigned = copy.deepcopy(manifest)
        unsigned.pop("release_id")
        from tools.catalog_contract import manifest_release_id

        manifest["release_id"] = manifest_release_id(unsigned)
        manifest_path.write_text(json.dumps(manifest))

        with self.assertRaisesRegex(ContractError, "firmware.api differs"):
            finalize_native_release(output, self.plan)
        self.assertFalse((output / "catalog-provenance.json").exists())
        self.assertFalse((output / "fw-packages-dev-009-SHA256SUMS").exists())

    def test_plan_rejects_wrong_next_revision_and_parallelism_drift(self) -> None:
        with self.assertRaisesRegex(ContractError, "not the next contracted release"):
            load_native_plan(
                self.repository, "dev", 10, self.source_commit, self.publisher_commit
            )

        control = self.root / "control"
        shutil.copytree(self.repository / "contracts", control / "contracts")
        path = control / "contracts/source-checkouts.json"
        value = json.loads(path.read_text())
        value["buildParallelism"] = 3
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContractError, "exactly 2"):
            load_native_plan(control, "dev", 9, self.source_commit, self.publisher_commit)

    def test_stable_plan_reserves_revision_two_against_v1_0_4(self) -> None:
        with self.assertRaisesRegex(ContractError, "no exact non-empty overlay plan"):
            load_native_plan(
                self.repository,
                "stable",
                2,
                self.source_commit,
                self.publisher_commit,
            )

    def test_unapproved_source_commit_is_terminal(self) -> None:
        with self.assertRaisesRegex(ContractError, "not authorized"):
            load_native_plan(
                self.repository,
                "dev",
                9,
                "c" * 40,
                self.publisher_commit,
            )

    def test_catalog_and_firmware_release_ids_are_distinct(self) -> None:
        self.assertNotEqual(
            self.plan["baseRelease"]["releaseId"],
            self.plan["targetFirmware"]["releaseId"],
        )
        self.assertEqual(
            self.plan["targetFirmware"]["releaseId"],
            "5799604a854b34c1bb67c67e63733e9c396af1167dfe829c2b144791c07a2ebf",
        )

    def test_provenance_tamper_is_terminal(self) -> None:
        output = self._source_output()
        finalize_native_release(output, self.plan)
        provenance_path = output / "catalog-provenance.json"
        provenance = json.loads(provenance_path.read_text())
        provenance["firmwareSource"]["commit"] = "c" * 40
        provenance_path.write_text(json.dumps(provenance))
        with self.assertRaisesRegex(ContractError, "firmwareSource differs"):
            verify_native_release(output, self.plan)

    def test_build_uses_source_owned_builder_and_atomic_output(self) -> None:
        source = self.root / "source"
        source.mkdir()
        source_contract = source / "tools/tumoflip/validate_release.py"
        source_contract.parent.mkdir(parents=True)
        source_contract.write_text(
            '''
PACKAGE_RELEASE_OVERLAY_FILES = {"apps/Module One/fixture.fap"}
def package_extapp_exports():
    return {"fixture.fap": "apps/Module One/fixture.fap"}
'''
        )
        build_artifact = source / "build/f7-firmware-C/.extapps/fixture.fap"
        build_artifact.parent.mkdir(parents=True)
        build_artifact.write_bytes(b"changed fixture payload")
        output = self.root / "native"
        base, base_contract = self._base_output()
        plan = copy.deepcopy(self.plan)
        plan["baseRelease"] = base_contract
        plan["selectedOverlays"] = {"fixture": "apps/Module One/fixture.fap"}
        plan["overlayTargets"] = ["apps/Module One/fixture.fap"]
        plan["maxChangedTargets"] = 1
        commands: list[tuple[str, ...]] = []

        def runner(command: tuple[str, ...] | list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
            command = tuple(command)
            commands.append(command)
            if command[:3] == ("git", "rev-parse", "HEAD"):
                return subprocess.CompletedProcess(command, 0, self.source_commit + "\n", "")
            if command[:3] == ("git", "status", "--porcelain"):
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ("git", "submodule", "status"):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    " 0123456789abcdef0123456789abcdef01234567 lib/example\n",
                    "",
                )
            raise AssertionError(command)

        build_native_release(source, base, output, plan, runner=runner)
        self.assertTrue(output.is_dir())
        verify_native_release(output, plan)
        self.assertEqual(
            json.loads((output / "catalog-provenance.json").read_text())[
                "changedTargets"
            ],
            ["apps/Module One/fixture.fap"],
        )
        self.assertEqual(
            sum(command[:3] == ("git", "rev-parse", "HEAD") for command in commands),
            2,
        )
        self.assertEqual(
            sum(command[:3] == ("git", "status", "--porcelain") for command in commands),
            2,
        )
        self.assertEqual(
            sum(command[:3] == ("git", "submodule", "status") for command in commands),
            2,
        )
        self.assertFalse(any(path.name.startswith(".native.") for path in self.root.iterdir()))

    def test_build_failure_never_exposes_partial_output(self) -> None:
        source = self.root / "source"
        source.mkdir()
        source_contract = source / "tools/tumoflip/validate_release.py"
        source_contract.parent.mkdir(parents=True)
        source_contract.write_text(
            '''
PACKAGE_RELEASE_OVERLAY_FILES = {"apps/Module One/fixture.fap"}
def package_extapp_exports():
    return {"fixture.fap": "apps/Module One/fixture.fap"}
'''
        )
        output = self.root / "native"
        base, base_contract = self._base_output()
        plan = copy.deepcopy(self.plan)
        plan["baseRelease"] = base_contract
        plan["selectedOverlays"] = {"fixture": "apps/Module One/fixture.fap"}
        plan["overlayTargets"] = ["apps/Module One/fixture.fap"]
        plan["maxChangedTargets"] = 1

        def runner(command: tuple[str, ...] | list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
            command = tuple(command)
            if command[:3] == ("git", "rev-parse", "HEAD"):
                return subprocess.CompletedProcess(command, 0, self.source_commit + "\n", "")
            if command[:3] == ("git", "status", "--porcelain"):
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ("git", "submodule", "status"):
                return subprocess.CompletedProcess(command, 0, "", "")
            raise AssertionError(command)

        with self.assertRaisesRegex(ContractError, "build artifact is missing"):
            build_native_release(source, base, output, plan, runner=runner)
        self.assertFalse(output.exists())

    def test_selected_overlay_noop_is_terminal(self) -> None:
        source = self.root / "source-noop"
        source.mkdir()
        source_contract = source / "tools/tumoflip/validate_release.py"
        source_contract.parent.mkdir(parents=True)
        source_contract.write_text(
            '''
PACKAGE_RELEASE_OVERLAY_FILES = {"apps/Module One/fixture.fap"}
def package_extapp_exports():
    return {"fixture.fap": "apps/Module One/fixture.fap"}
'''
        )
        base, base_contract = self._base_output()
        artifact = source / "build/f7-firmware-C/.extapps/fixture.fap"
        artifact.parent.mkdir(parents=True)
        with zipfile.ZipFile(base / "tumoflip-packages.zip") as archive:
            artifact.write_bytes(archive.read("apps/Module One/fixture.fap"))
        plan = copy.deepcopy(self.plan)
        plan["baseRelease"] = base_contract
        plan["selectedOverlays"] = {"fixture": "apps/Module One/fixture.fap"}
        plan["overlayTargets"] = ["apps/Module One/fixture.fap"]
        plan["maxChangedTargets"] = 1

        def runner(command, *, cwd):
            command = tuple(command)
            if command[:3] == ("git", "rev-parse", "HEAD"):
                return subprocess.CompletedProcess(command, 0, self.source_commit + "\n", "")
            if command[:3] == ("git", "status", "--porcelain"):
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ("git", "submodule", "status"):
                return subprocess.CompletedProcess(command, 0, "", "")
            raise AssertionError(command)

        with self.assertRaisesRegex(ContractError, "selected overlay is unchanged"):
            build_native_release(source, base, self.root / "noop", plan, runner=runner)

    def test_source_commit_mismatch_is_terminal(self) -> None:
        output = self._source_output()
        manifest_path = output / "tumoflip-packages.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["package_release"]["source_commit"] = "c" * 40
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ContractError, "source_commit differs"):
            finalize_native_release(output, self.plan)


if __name__ == "__main__":
    unittest.main()
