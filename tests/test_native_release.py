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
        self.control = self.root / "control"
        shutil.copytree(self.repository / "contracts", self.control / "contracts")
        current_path = self.control / "contracts/current-releases.json"
        current = json.loads(current_path.read_text())
        current["channels"]["dev"]["tag"] = "fw-packages-dev-008"
        current["channels"]["dev"]["revision"] = 8
        current_path.write_text(json.dumps(current))
        lineage_path = self.control / "contracts/catalog-lineage.json"
        lineage = json.loads(lineage_path.read_text())
        lineage["channels"]["dev"].update(
            {
                "currentTag": "fw-packages-dev-008",
                "currentRevision": 8,
                "nextNativeRevision": 9,
                "nextNativeTag": "fw-packages-dev-009",
                "seededFromLegacy": True,
            }
        )
        lineage_path.write_text(json.dumps(lineage))
        policy_path = self.control / "contracts/native-build-policy.json"
        policy = json.loads(policy_path.read_text())
        # The production policy may contain a plan for a later revision. Tests
        # replace the channel lineage with a dev-009 fixture, so isolate the
        # fixture from that live plan before adding its own exact plan.
        policy["releasePlans"] = {}
        policy["releasePlans"]["fw-packages-dev-009"] = {
            "sourceCommit": self.source_commit,
            "selectedOverlays": ["esp_flasher"],
        }
        policy_path.write_text(json.dumps(policy))
        self.plan = load_native_plan(
            self.control,
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
        manifest["package_release"]["target_source_commit"] = self.plan[
            "targetFirmware"
        ]["commit"]
        manifest["package_release"]["overlay_targets"] = self.plan["overlayTargets"]
        manifest["package_release"]["synced_extapps"] = [
            {
                "source": ".extapps/esp_flasher.fap",
                "target": target,
                "bytes": 1,
                "sha256": "0" * 64,
                "md5": "0" * 32,
            }
            for target in self.plan["overlayTargets"]
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
            if key == "id":
                continue
            self.assertEqual(after["package_release"][key], value)
        self.assertEqual(after["package_release"]["id"], "fw-packages-dev-009")
        self.assertEqual(after["package_release"]["catalog_channel"], "dev")
        self.assertEqual(after["package_release"]["catalog_revision"], 9)
        self.assertEqual(after["package_release"]["catalog_release_tag"], "fw-packages-dev-009")
        self.assertEqual(after["package_release"]["catalog_install_scope"], "delta")
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
                self.repository, "dev", 11, self.source_commit, self.publisher_commit
            )

        control = self.root / "parallelism-control"
        shutil.copytree(self.control / "contracts", control / "contracts")
        path = control / "contracts/source-checkouts.json"
        value = json.loads(path.read_text())
        value["buildParallelism"] = 3
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContractError, "exactly 2"):
            load_native_plan(control, "dev", 9, self.source_commit, self.publisher_commit)

    def test_repository_retains_morse_overlay_allowlist_after_publication(self) -> None:
        policy = json.loads(
            (self.repository / "contracts/native-build-policy.json").read_text()
        )
        self.assertNotIn("fw-packages-dev-009", policy["releasePlans"])
        self.assertEqual(
            policy["allowedOverlays"]["morse_player"],
            "apps/Tools/morse_player.fap",
        )
        self.assertEqual(policy["overlayGroups"]["morse_player"], "base")
        with self.assertRaisesRegex(ContractError, "not the next contracted release"):
            load_native_plan(
                self.repository,
                "stable",
                3,
                "8ab2ccdf7a34bbf3e07f2d4cbd459de1c6de8758",
                self.publisher_commit,
            )

    def test_unapproved_source_commit_is_terminal(self) -> None:
        with self.assertRaisesRegex(ContractError, "not authorized"):
            load_native_plan(
                self.control,
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
        plan["overlayGroups"] = {"apps/Module One/fixture.fap": "module_one"}
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
        provenance = json.loads((output / "catalog-provenance.json").read_text())
        self.assertEqual(
            provenance["buildEnvironment"],
            {"runner": "ubuntu-24.04", "toolchainVersion": "39"},
        )
        self.assertEqual(
            provenance["sourceBuiltOverlays"][0]["sha256"],
            __import__("hashlib").sha256(b"changed fixture payload").hexdigest(),
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

    def test_build_adds_a_new_allowlisted_overlay_entry(self) -> None:
        source = self.root / "source-addition"
        source.mkdir()
        source_contract = source / "tools/tumoflip/validate_release.py"
        source_contract.parent.mkdir(parents=True)
        source_contract.write_text(
            '''
PACKAGE_RELEASE_OVERLAY_FILES = {"apps/Tools/morse_player.fap"}
def package_extapp_exports():
    return {"morse_player.fap": "apps/Tools/morse_player.fap"}
'''
        )
        artifact = source / "build/f7-firmware-C/.extapps/morse_player.fap"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"new Morse Player payload")
        base, base_contract = self._base_output()
        plan = copy.deepcopy(self.plan)
        plan["selectedOverlays"] = {"morse_player": "apps/Tools/morse_player.fap"}
        plan["overlayTargets"] = ["apps/Tools/morse_player.fap"]
        plan["overlayGroups"] = {"apps/Tools/morse_player.fap": "base"}
        plan["maxChangedTargets"] = 1
        plan["baseRelease"] = base_contract

        def runner(command, *, cwd):
            command = tuple(command)
            if command[:3] == ("git", "rev-parse", "HEAD"):
                return subprocess.CompletedProcess(command, 0, self.source_commit + "\n", "")
            if command[:3] == ("git", "status", "--porcelain"):
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ("git", "submodule", "status"):
                return subprocess.CompletedProcess(command, 0, "", "")
            raise AssertionError(command)

        output = self.root / "addition"
        build_native_release(source, base, output, plan, runner=runner)
        manifest = json.loads((output / "tumoflip-packages.json").read_text())
        entries = {
            entry["source"]: entry
            for group in manifest["packages"].values()
            for entry in group
        }
        self.assertIn("apps/Tools/morse_player.fap", entries)
        self.assertEqual(entries["apps/Tools/morse_player.fap"]["target"], "/ext/apps/Tools/morse_player.fap")
        with zipfile.ZipFile(output / "tumoflip-packages.zip") as archive:
            self.assertEqual(archive.read("apps/Tools/morse_player.fap"), b"new Morse Player payload")
        verify_native_release(output, plan, base)

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
        plan["overlayGroups"] = {"apps/Module One/fixture.fap": "module_one"}
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
        plan["overlayGroups"] = {"apps/Module One/fixture.fap": "module_one"}
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

        with self.assertRaisesRegex(ContractError, "no runtime change"):
            build_native_release(source, base, self.root / "noop", plan, runner=runner)

    def test_debuglink_crc_only_change_is_runtime_noop(self) -> None:
        import struct

        source = self.root / "source-debuglink"
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

        def elf(crc: bytes) -> bytes:
            data = bytearray(200)
            data[:16] = b"\x7fELF\x01\x01\x01" + b"\0" * 9
            struct.pack_into("<I", data, 32, 52)
            struct.pack_into("<HHH", data, 46, 40, 3, 2)
            names = b"\0.gnu_debuglink\0.shstrtab\0"
            data[160 : 160 + len(names)] = names
            struct.pack_into("<I12xII", data, 92, 1, 188, 12)
            struct.pack_into("<I12xII", data, 132, 16, 160, len(names))
            data[188:200] = b"x.elf\0\0\0" + crc
            return bytes(data)

        previous = elf(b"\x01\x02\x03\x04")
        candidate = elf(b"\x05\x06\x07\x08")
        manifest_path = base / "tumoflip-packages.json"
        manifest = json.loads(manifest_path.read_text())
        entry = manifest["packages"]["module_one"][0]
        entry.update(
            bytes=len(previous),
            sha256=__import__("hashlib").sha256(previous).hexdigest(),
            md5=__import__("hashlib").md5(previous).hexdigest(),
        )
        manifest["release_id"] = manifest_release_id(manifest)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        resources = self.fixture / "resources"
        with zipfile.ZipFile(base / "tumoflip-packages.zip", "w") as archive:
            archive.writestr(entry["source"], previous)
            archive.write(resources / "apps/Tools/fixture.fap", "apps/Tools/fixture.fap")
        checksum = base / "fw-packages-dev-008-SHA256SUMS"
        checksum.write_text(
            "".join(
                f"{sha256(base / name)}  {name}\n"
                for name in ("tumoflip-packages.json", "tumoflip-packages.zip")
            )
        )
        base_contract["releaseId"] = manifest["release_id"]
        base_contract["assets"] = {
            checksum.name: sha256(checksum),
            "tumoflip-packages.json": sha256(manifest_path),
            "tumoflip-packages.zip": sha256(base / "tumoflip-packages.zip"),
        }
        artifact = source / "build/f7-firmware-C/.extapps/fixture.fap"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(candidate)
        plan = copy.deepcopy(self.plan)
        plan["baseRelease"] = base_contract
        plan["selectedOverlays"] = {"fixture": entry["source"]}
        plan["overlayTargets"] = [entry["source"]]
        plan["overlayGroups"] = {entry["source"]: "module_one"}
        plan["maxChangedTargets"] = 1

        def runner(command, *, cwd):
            command = tuple(command)
            if command[:3] == ("git", "rev-parse", "HEAD"):
                return subprocess.CompletedProcess(command, 0, self.source_commit + "\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with self.assertRaisesRegex(ContractError, "no runtime change"):
            build_native_release(source, base, self.root / "debuglink", plan, runner=runner)

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
