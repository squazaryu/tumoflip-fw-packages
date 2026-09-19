import tempfile
import hashlib
import struct
import contextlib
import io
import json
from unittest.mock import patch
import unittest
import zipfile
from pathlib import Path

from tools.community_abi_audit import (
    AbiAuditError,
    audit_archives,
    parse_api_symbols,
    parse_undefined_symbols,
    parse_defined_symbols,
)


def elf_fixture(assets=None, extra_sections=0):
    names = b"\0.shstrtab\0.fapmeta\0.fapassets\0"
    meta = struct.pack("<IIHHH", 0x52474448, 1, 10, 88, 7) + bytes(71)
    sections = [(0, b"", 0), (1, names, 3), (11, meta, 1)]
    sections.extend([(0, b"", 0)] * extra_sections)
    if assets is not None:
        sections.append((20, assets, 1))
    offset = 52 + len(sections) * 40
    headers, payload = [], b""
    for name, data, kind in sections:
        headers.append(struct.pack("<10I", name, kind, 0, 0, offset, len(data), 0, 0, 1, 0))
        payload += data
        offset += len(data)
    ident = b"\x7fELF\x01\x01\x01" + bytes(9)
    header = ident + struct.pack("<HHIIIIIHHHHHH", 1, 40, 1, 0, 0, 52, 0, 52, 0, 0, 40, len(sections), 1)
    return header + b"".join(headers) + payload


def asset_fixture(files):
    payload = b""
    checksum = hashlib.md5()
    for name, data in files:
        encoded = name.encode() + b"\0"
        payload += struct.pack("<I", len(encoded)) + encoded + struct.pack("<I", len(data)) + data
        checksum.update(encoded)
        checksum.update(data)
    return struct.pack("<5I", 0x4F4C5A44, 1, 0, len(files), 16) + checksum.digest() + payload


class CommunityAbiAuditTests(unittest.TestCase):
    def test_weak_undefined_symbols_are_not_host_definitions(self):
        self.assertEqual(parse_defined_symbols("w optional_function\nv optional_object\n00000000 W present\n"), {"present"})

    def test_cli_writes_report_and_fails_closed_on_bad_inputs(self):
        from tools import community_abi_audit as audit
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api.csv"
            api.write_text("entry,status,name,type,params\nVersion,+,88.10,,\n")
            contract = root / "hosts.json"
            contract.write_text('{"hosts":{},"externalPlugins":{}}')
            for pack in ("base", "extra"):
                with zipfile.ZipFile(root / f"{pack}.zip", "w") as z:
                    if pack == "base":
                        z.writestr("base_pack_build/artifacts-base/Tools/test.fap", elf_fixture())
            argv = ["audit", "--base-archive", str(root / "base.zip"), "--extra-archive", str(root / "extra.zip"),
                    "--api-symbols", str(api), "--host-contract", str(contract), "--output", str(root / "out.json")]
            for symbols, expected in (("", 0), ("U absent", 1)):
                with patch("sys.argv", argv), patch.object(audit, "_default_nm_runner", return_value=lambda _: symbols), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(audit.main(), expected)
                self.assertEqual(json.loads((root / "out.json").read_text())["api"], "88.10")
            api.write_text("bad input")
            with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(audit.main(), 2)

    def test_asset_and_elf_input_boundaries(self):
        from tools.community_elf import bundled_files, elf_sections, ElfAuditError
        valid = elf_fixture()
        for size in (0, 7, 51):
            with self.assertRaises(ElfAuditError):
                elf_sections(valid[:size])
        for offset in (5, 18, 40, 46, 50):
            data = bytearray(valid)
            data[offset] = 0xff
            with self.assertRaises(ElfAuditError):
                elf_sections(bytes(data))
        for files in ((('../bad.fal', b'x'),), (('a.fal', b'x'), ('a.fal', b'y'))):
            with self.assertRaises(ElfAuditError):
                bundled_files(asset_fixture(files))
        bundle = asset_fixture([("plugins/a.fal", b"a")])
        self.assertEqual(bundled_files(bundle), {"plugins/a.fal": b"a"})
        for data in (bundle[:20], bundle[:-1], bundle+b"junk"):
            with self.assertRaises(ElfAuditError):
                bundled_files(data)

    def test_large_but_bounded_real_section_tables(self):
        from tools.community_elf import elf_sections, ElfAuditError
        self.assertEqual(elf_sections(elf_fixture(extra_sections=512))[1], (88, 10, 7))
        with self.assertRaisesRegex(ElfAuditError, "section table"):
            elf_sections(elf_fixture(extra_sections=4096))

    def test_ordinary_duplicate_section_names_are_valid_elf(self):
        from tools.community_elf import elf_sections, ElfAuditError
        data = bytearray(elf_fixture())
        struct.pack_into("<I", data, 52, 1)
        self.assertEqual(elf_sections(bytes(data))[1], (88, 10, 7))
        struct.pack_into("<I", data, 52, 11)
        with self.assertRaisesRegex(ElfAuditError, "duplicate"):
            elf_sections(bytes(data))

    def test_api_parser_keeps_only_exported_function_and_variable_symbols(self):
        api = """entry,status,name,type,params
Version,+,88.6,,
Function,+,canvas_clear,void,Canvas*
Function,-,private_helper,void,
Header,+,lib/example.h,,
Variable,+,usb_cdc_dual,FuriHalUsbInterface,
"""
        self.assertEqual(
            parse_api_symbols(api), {"canvas_clear", "usb_cdc_dual"}
        )

    def test_nm_parser_accepts_common_nm_shapes_and_ignores_defined_symbols(self):
        output = """
                 U canvas_clear
00000000 T local_function
         U gps_request_stream
"""
        self.assertEqual(
            parse_undefined_symbols(output),
            {"canvas_clear", "gps_request_stream"},
        )

    def test_fap_missing_firmware_import_is_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "base.zip"
            extra = root / "extra.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "base_pack_build/artifacts-base/Tools/sample.fap", elf_fixture()
                )
            with zipfile.ZipFile(extra, "w"):
                pass

            def nm(_path: Path) -> str:
                return "         U gps_request_stream\n"

            report = audit_archives(
                [("base", archive), ("extra", extra)],
                firmware_symbols={"canvas_clear"},
                nm_runner=nm,
            )

        self.assertEqual(report["status"], "needsReview")
        self.assertEqual(report["summary"]["needs_review"], 1)
        self.assertEqual(
            report["findings"][0]["missing_symbols"], ["gps_request_stream"]
        )

    def test_fal_host_exports_are_not_mistaken_for_firmware_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "base.zip"
            extra = root / "extra.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "base_pack_build/artifacts-base/Tools/host.fap", elf_fixture()
                )
                zf.writestr(
                    "base_pack_build/artifacts-base/apps/plugin.fal", elf_fixture()
                )
            with zipfile.ZipFile(extra, "w"):
                pass

            def nm(path: Path) -> str:
                if path.name.endswith("-host.fap"):
                    return "00000000 T host_export\n"
                return "         U host_export\n"

            report = audit_archives(
                [("base", archive), ("extra", extra)],
                firmware_symbols=set(),
                nm_runner=nm,
                host_contract={
                    "hosts": {"base/Tools/host.fap": {"sha256": hashlib.sha256(elf_fixture()).hexdigest(), "exports": ["host_export"]}},
                    "externalPlugins": {"base/apps/plugin.fal": "base/Tools/host.fap"},
                },
            )

        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["summary"]["needs_review"], 0)
        self.assertEqual(report["summary"]["fal"], 1)

    def test_unrelated_host_cannot_supply_plugin_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / "base.zip", "w") as archive:
                archive.writestr("base_pack_build/artifacts-base/Tools/other.fap", elf_fixture())
                archive.writestr("base_pack_build/artifacts-base/Tools/owner.fap", elf_fixture(asset_fixture([
                    ("plugins/child.fal", elf_fixture())])))
            with zipfile.ZipFile(root / "extra.zip", "w"):
                pass
            report = audit_archives([(p, root / f"{p}.zip") for p in ("base", "extra")],
                firmware_symbols=set(), nm_runner=lambda p: "0000 T wrong_export" if "other.fap" in p.name else ("U wrong_export" if "child.fal" in p.name else ""))
        self.assertEqual(report["status"], "needsReview")
        self.assertEqual(report["summary"]["embedded"], 1)
        self.assertEqual(report["findings"][0]["missing_symbols"], ["wrong_export"])

    def test_embedded_missing_import_and_checksum_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for broken in (False, True):
                bundle = asset_fixture([("plugins/child.fal", elf_fixture())])
                if broken:
                    bundle = bundle[:20] + bytes(16) + bundle[36:]
                with zipfile.ZipFile(root / "base.zip", "w") as archive:
                    archive.writestr("base_pack_build/artifacts-base/Tools/owner.fap", elf_fixture(bundle))
                with zipfile.ZipFile(root / "extra.zip", "w"):
                    pass
                call = lambda: audit_archives([(p, root / f"{p}.zip") for p in ("base", "extra")],
                    firmware_symbols=set(), nm_runner=lambda p: "U missing_api" if "child.fal" in p.name else "")
                if broken:
                    with self.assertRaisesRegex(AbiAuditError, "checksum"):
                        call()
                else:
                    report = call()
                    self.assertEqual(report["summary"]["needs_review"], 1)
                    self.assertIn("child.fal", report["findings"][0]["archive_member"])

    def test_archive_path_traversal_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            extra = Path(directory) / "extra.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("base_pack_build/artifacts-base/../escape.fap", b"x")
            with zipfile.ZipFile(extra, "w"):
                pass
            with self.assertRaisesRegex(AbiAuditError, "unsafe archive member"):
                audit_archives(
                    [("base", archive), ("extra", extra)],
                    firmware_symbols=set(),
                    nm_runner=lambda _: "",
                )

    def test_oversized_binary_member_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "base.zip"
            extra = root / "extra.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "base_pack_build/artifacts-base/Tools/large.fap",
                    b"x" * (16 * 1024 * 1024 + 1),
                )
            with zipfile.ZipFile(extra, "w"):
                pass
            with self.assertRaisesRegex(AbiAuditError, "too large"):
                audit_archives(
                    [("base", archive), ("extra", extra)],
                    firmware_symbols=set(),
                    nm_runner=lambda _: "",
                )


if __name__ == "__main__":
    unittest.main()
