from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.catalog_contract import ContractError
from tools.source_build_targets import main as targets_main
from tools.source_build_targets import source_build_targets


class SourceBuildTargetsTests(unittest.TestCase):
    def _source(self, body: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        module = root / "tools/tumoflip/validate_release.py"
        module.parent.mkdir(parents=True)
        module.write_text(body, encoding="utf-8")
        return root

    def test_older_source_is_terminal_for_a_native_delta(self) -> None:
        root = self._source("LEGACY = True\n")
        with self.assertRaisesRegex(ContractError, "predates selective"):
            source_build_targets(root, {"esp": "apps/esp.fap"})

    def test_overlay_targets_come_from_source_owned_mapping(self) -> None:
        root = self._source(
            """
PACKAGE_RELEASE_OVERLAY_FILES = {
    "apps/ARF Tools/edit.fap",
    "apps_data/totp/plugins/add.fal",
}
def package_extapp_exports():
    return {
        "edit.fap": "apps/ARF Tools/edit.fap",
        "add.fal": "apps_data/totp/plugins/add.fal",
        "unrelated.fap": "apps/Tools/unrelated.fap",
    }
"""
        )
        self.assertEqual(
            source_build_targets(
                root, {"add": "apps_data/totp/plugins/add.fal"}
            ),
            ("fap_add",),
        )

    def test_ambiguous_export_is_terminal(self) -> None:
        root = self._source(
            """
PACKAGE_RELEASE_OVERLAY_FILES = {"apps/edit.fap"}
def package_extapp_exports():
    return {"edit.fap": "apps/edit.fap", "alias.fap": "apps/edit.fap"}
"""
        )
        with self.assertRaisesRegex(ContractError, "one canonical export"):
            source_build_targets(root, {"edit": "apps/edit.fap"})

    def test_shell_format_emits_valid_single_space_tokens(self) -> None:
        import sys
        from unittest import mock

        root = self._source(
            '''
PACKAGE_RELEASE_OVERLAY_FILES = {"apps/esp.fap"}
def package_extapp_exports():
    return {"esp.fap": "apps/esp.fap"}
'''
        )
        control = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(control))
        (control / "contracts").mkdir()
        (control / "contracts/native-build-policy.json").write_text(
            '{"allowedOverlays":{"esp":"apps/esp.fap"},'
            '"releasePlans":{"fw-packages-dev-009":'
            '{"selectedOverlays":["esp"]}}}'
        )
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "source_build_targets.py",
                    "--source-root",
                    str(root),
                    "--control-root",
                    str(control),
                    "--channel",
                    "dev",
                    "--revision",
                    "9",
                    "--format",
                    "shell",
                ],
            ),
            mock.patch("builtins.print") as output,
        ):
            self.assertEqual(targets_main(), 0)
        output.assert_called_once_with("fap_esp")

    def test_data_only_plan_emits_no_build_targets(self) -> None:
        import json
        import sys
        from unittest import mock

        control = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(control))
        (control / "contracts").mkdir()
        (control / "contracts/native-build-policy.json").write_text(
            json.dumps(
                {
                    "allowedOverlays": {"esp": "apps/esp.fap"},
                    "releasePlans": {
                        "fw-packages-dev-009": {
                            "mode": "data",
                            "selectedOverlays": [],
                            "selectedDataOverlays": ["uids"],
                        }
                    },
                }
            )
        )
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "source_build_targets.py",
                    "--source-root",
                    str(control),
                    "--control-root",
                    str(control),
                    "--channel",
                    "dev",
                    "--revision",
                    "9",
                    "--format",
                    "shell",
                ],
            ),
            mock.patch("builtins.print") as output,
        ):
            self.assertEqual(targets_main(), 0)
        output.assert_called_once_with("")


if __name__ == "__main__":
    unittest.main()
