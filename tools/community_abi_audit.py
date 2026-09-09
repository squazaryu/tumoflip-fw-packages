#!/usr/bin/env python3
"""Audit Community Pack ELF imports against the exact Tumoflip F7 API.

The Community Pack is built by a different firmware tree and may advertise a
newer API minor than Tumoflip.  The Flipper loader only gates the API major, so
this control-plane check inspects the actual undefined ELF imports as well.  A
standalone FAP may only import the firmware API; a FAL is allowed to import a
symbol exported by an FAP in the same archive because FALs are host plugins.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable


class AbiAuditError(ValueError):
    """Raised when an archive or symbol table cannot be audited safely."""


NMOutput = Callable[[Path], str]
_API_VERSION = re.compile(r"^Version,\+,([0-9]+\.[0-9]+),,$")
_DEFINED_TYPES = set("ABCDGRSTVWabcdefghorstuvw")
_PACKS = ("base", "extra")
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_BINARY_COUNT = 1024


def parse_api_symbols(text: str) -> set[str]:
    """Return exported Function/Variable names from an API CSV document."""

    rows = csv.reader(StringIO(text))
    try:
        header = next(rows)
    except StopIteration as error:
        raise AbiAuditError("API symbol table is empty") from error
    if header[:4] != ["entry", "status", "name", "type"]:
        raise AbiAuditError("API symbol table header is invalid")

    symbols: set[str] = set()
    for row in rows:
        if len(row) < 3:
            raise AbiAuditError("API symbol table contains a short row")
        if row[0] in {"Function", "Variable"} and row[1] == "+":
            name = row[2].strip()
            if not name or any(char.isspace() for char in name):
                raise AbiAuditError("API symbol table contains an invalid symbol")
            symbols.add(name)
    return symbols


def parse_api_version(text: str) -> str:
    for line in text.splitlines():
        match = _API_VERSION.fullmatch(line.strip())
        if match:
            return match.group(1)
    raise AbiAuditError("API symbol table version row is missing")


def parse_undefined_symbols(output: str) -> set[str]:
    """Parse GNU/LLVM nm output and return symbols marked ``U``."""

    symbols: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        for index, field in enumerate(fields[:-1]):
            if field.upper() == "U":
                symbols.add(fields[index + 1])
                break
    return symbols


def parse_defined_symbols(output: str) -> set[str]:
    """Parse globally named definitions for FAL host-dependency resolution."""

    symbols: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        symbol_type_index = next(
            (index for index, field in enumerate(fields) if len(field) == 1 and field in _DEFINED_TYPES),
            None,
        )
        if symbol_type_index is not None and symbol_type_index + 1 < len(fields):
            symbols.add(fields[symbol_type_index + 1])
    return symbols


def _archive_member(member: str, pack: str) -> tuple[str, str] | None:
    pack_root = f"{pack}_pack_build/"
    prefix = f"{pack}_pack_build/artifacts-{pack}/"
    if member.endswith("/") and member.startswith(pack_root):
        relative_directory = member[len(pack_root) :].rstrip("/")
        path = PurePosixPath(relative_directory)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise AbiAuditError(f"unsafe archive member: {member}")
        return None
    if member.startswith(prefix):
        relative = member[len(prefix) :]
    elif member.startswith(f"{pack_root}apps_data/"):
        # Some Community Pack host plugins (currently TOTP FALs) live under
        # apps_data instead of the artifact directory.
        relative = member[len(pack_root) :]
    else:
        raise AbiAuditError(f"unsafe archive member: {member}")
    path = PurePosixPath(relative)
    if (
        not relative
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise AbiAuditError(f"unsafe archive member: {member}")
    suffix = path.suffix.lower()
    if suffix not in {".fap", ".fal"}:
        return None
    return relative, suffix[1:]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_archives(
    archives: Iterable[tuple[str, Path]],
    *,
    firmware_symbols: set[str],
    nm_runner: NMOutput,
) -> dict[str, object]:
    """Inspect all FAP/FAL members in the supplied base and extra archives."""

    archive_reports: list[dict[str, object]] = []
    binaries: list[tuple[str, str, str, bytes]] = []
    seen_packs: set[str] = set()
    for pack, archive_path in archives:
        if pack not in _PACKS or pack in seen_packs:
            raise AbiAuditError(f"archive pack is duplicated or invalid: {pack}")
        seen_packs.add(pack)
        if not archive_path.is_file():
            raise AbiAuditError(f"archive is missing: {archive_path}")
        if archive_path.stat().st_size > _MAX_ARCHIVE_BYTES:
            raise AbiAuditError(f"archive is too large: {pack}")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise AbiAuditError(f"archive contains duplicate members: {pack}")
                for member in names:
                    parsed = _archive_member(member, pack)
                    if parsed is None:
                        continue
                    info = archive.getinfo(member)
                    if info.file_size > _MAX_MEMBER_BYTES:
                        raise AbiAuditError(f"binary member is too large: {member}")
                    _relative, kind = parsed
                    data = archive.read(member)
                    if not data:
                        raise AbiAuditError(f"empty binary member: {member}")
                    binaries.append((pack, member, kind, data))
                    if len(binaries) > _MAX_BINARY_COUNT:
                        raise AbiAuditError("archive contains too many binaries")
        except zipfile.BadZipFile as error:
            raise AbiAuditError(f"invalid {pack} archive: {error}") from error
        archive_reports.append(
            {
                "pack": pack,
                "path": str(archive_path),
                "bytes": archive_path.stat().st_size,
                "sha256": _sha256(archive_path),
            }
        )

    if seen_packs != set(_PACKS):
        raise AbiAuditError("both base and extra archives are required")

    with tempfile.TemporaryDirectory(prefix="community-abi-") as temporary:
        root = Path(temporary)
        symbol_outputs: dict[tuple[str, str], str] = {}
        for index, (pack, member, kind, data) in enumerate(binaries):
            path = root / f"{index}-{Path(member).name}"
            path.write_bytes(data)
            symbol_outputs[(pack, member)] = nm_runner(path)

        host_exports: dict[str, set[str]] = {pack: set() for pack in _PACKS}
        for pack, member, kind, _data in binaries:
            if kind == "fap":
                host_exports[pack].update(
                    parse_defined_symbols(symbol_outputs[(pack, member)])
                )

        findings: list[dict[str, object]] = []
        compatible = 0
        for pack, member, kind, _data in binaries:
            imports = parse_undefined_symbols(symbol_outputs[(pack, member)])
            allowed = (
                firmware_symbols
                if kind == "fap"
                else firmware_symbols | host_exports[pack]
            )
            missing = sorted(imports - allowed)
            if missing:
                findings.append(
                    {
                        "pack": pack,
                        "archive_member": member,
                        "kind": kind,
                        "missing_symbols": missing,
                    }
                )
            else:
                compatible += 1

    fap_count = sum(kind == "fap" for _pack, _member, kind, _data in binaries)
    fal_count = sum(kind == "fal" for _pack, _member, kind, _data in binaries)
    return {
        "schema": 1,
        "kind": "communityPackAbiAudit",
        "status": "needsReview" if findings else "verified",
        "summary": {
            "binaries": len(binaries),
            "fap": fap_count,
            "fal": fal_count,
            "compatible": compatible,
            "needs_review": len(findings),
        },
        "archives": archive_reports,
        "findings": findings,
    }


def _default_nm_runner(command: str) -> NMOutput:
    def run(path: Path) -> str:
        result = subprocess.run(
            [command, "-g", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit {result.returncode}"
            raise AbiAuditError(f"nm failed for {path.name}: {detail}")
        return result.stdout

    return run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-archive", type=Path, required=True)
    parser.add_argument("--extra-archive", type=Path, required=True)
    parser.add_argument("--api-symbols", type=Path, required=True)
    parser.add_argument("--nm", dest="nm_command")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        api_text = args.api_symbols.read_text(encoding="utf-8")
        report = audit_archives(
            [("base", args.base_archive), ("extra", args.extra_archive)],
            firmware_symbols=parse_api_symbols(api_text),
            nm_runner=_default_nm_runner(
                args.nm_command
                or shutil.which("arm-none-eabi-nm")
                or shutil.which("llvm-nm")
                or shutil.which("nm")
                or "nm"
            ),
        )
        report["api"] = parse_api_version(api_text)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {"status": report["status"], "summary": report["summary"]},
                sort_keys=True,
            )
        )
        return 0 if report["status"] == "verified" else 1
    except (AbiAuditError, OSError, ValueError) as error:
        print(f"error: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
