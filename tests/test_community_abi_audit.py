import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.community_abi_audit import (
    AbiAuditError,
    audit_archives,
    parse_api_symbols,
    parse_undefined_symbols,
)


class CommunityAbiAuditTests(unittest.TestCase):
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
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "base_pack_build/artifacts-base/Tools/sample.fap", b"fap"
                )

            def nm(_path: Path) -> str:
                return "         U gps_request_stream\n"

            report = audit_archives(
                [("base", archive)],
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
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "base_pack_build/artifacts-base/Tools/host.fap", b"host"
                )
                zf.writestr(
                    "base_pack_build/artifacts-base/apps/plugin.fal", b"plugin"
                )

            def nm(path: Path) -> str:
                if path.name == "host.fap":
                    return "00000000 T host_export\n"
                return "         U host_export\n"

            report = audit_archives(
                [("base", archive)],
                firmware_symbols=set(),
                nm_runner=nm,
            )

        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["summary"]["needs_review"], 0)
        self.assertEqual(report["summary"]["fal"], 1)

    def test_archive_path_traversal_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("base_pack_build/artifacts-base/../escape.fap", b"x")
            with self.assertRaisesRegex(AbiAuditError, "unsafe archive member"):
                audit_archives(
                    [("base", archive)], firmware_symbols=set(), nm_runner=lambda _: ""
                )


if __name__ == "__main__":
    unittest.main()
