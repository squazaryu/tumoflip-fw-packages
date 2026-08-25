from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.catalog_index import ContractError, build_from_current, validate_index, write_index


class CatalogIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_repository_index_is_valid(self) -> None:
        index = json.loads((self.root / "catalog-index.json").read_text(encoding="utf-8"))
        validate_index(index)

    def test_index_is_derived_from_current_contract(self) -> None:
        index = build_from_current(self.root / "contracts/current-releases.json", generated_at="test")
        self.assertEqual(index["channels"]["stable"]["current_revision"], 4)
        self.assertEqual(index["channels"]["dev"]["current_revision"], 8)
        self.assertEqual(index["channels"]["stable"]["releases"][0]["tag"], "fw-packages-stable-004")

    def test_build_preserves_historical_revisions(self) -> None:
        index = build_from_current(
            self.root / "contracts/current-releases.json",
            generated_at="test",
            existing=self.root / "catalog-index.json",
        )
        self.assertEqual(
            [item["revision"] for item in index["channels"]["stable"]["releases"]],
            [1, 4],
        )
        self.assertEqual(
            [item["revision"] for item in index["channels"]["dev"]["releases"]],
            [1, 2, 3, 4, 5, 7, 8],
        )

    def test_unchanged_index_preserves_existing_bytes(self) -> None:
        source = self.root / "catalog-index.json"
        document = build_from_current(
            self.root / "contracts/current-releases.json",
            existing=source,
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "catalog-index.json"
            original = source.read_bytes()
            destination.write_bytes(original)
            write_index(destination, document)
            self.assertEqual(destination.read_bytes(), original)

    def test_current_revision_cannot_be_withdrawn(self) -> None:
        index = json.loads((self.root / "catalog-index.json").read_text(encoding="utf-8"))
        current = index["channels"]["stable"]["current_revision"]
        next(item for item in index["channels"]["stable"]["releases"] if item["revision"] == current)["state"] = "withdrawn"
        with self.assertRaisesRegex(ContractError, "current_revision must be active"):
            validate_index(index)

    def test_revision_and_tag_are_bound(self) -> None:
        index = json.loads((self.root / "catalog-index.json").read_text(encoding="utf-8"))
        index["channels"]["dev"]["releases"][0]["revision"] = 7
        with self.assertRaisesRegex(ContractError, "tag does not match"):
            validate_index(index)
