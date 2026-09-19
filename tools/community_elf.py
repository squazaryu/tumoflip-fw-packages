"""Bounded ELF32/F7 and embedded-asset reading; never loads executable code."""
import hashlib
import struct
from pathlib import PurePosixPath

MAX_BYTES = 16 * 1024 * 1024


class ElfAuditError(ValueError):
    pass


def safe_path(name):
    parts = name.split("/")
    if not name or "\\" in name or any(p in ("", ".", "..") for p in parts):
        raise ElfAuditError(f"unsafe asset path: {name!r}")
    if PurePosixPath(name).is_absolute() or any(ord(c) < 32 for c in name):
        raise ElfAuditError(f"unsafe asset path: {name!r}")
    return name


def elf_sections(blob):
    if not 52 <= len(blob) <= MAX_BYTES or blob[:7] != b"\x7fELF\x01\x01\x01":
        raise ElfAuditError("invalid ELF32 header")
    kind, machine, version = struct.unpack_from("<HHI", blob, 16)
    table = struct.unpack_from("<I", blob, 32)[0]
    header_size, _, _, entry_size, count, names_index = struct.unpack_from("<6H", blob, 40)
    if (kind, machine, version, header_size, entry_size) != (1, 40, 1, 52, 40):
        raise ElfAuditError("unsupported ELF target/header")
    if not 0 < count <= 4096 or names_index >= count or table + count * 40 > len(blob):
        raise ElfAuditError("invalid ELF section table")
    headers = [struct.unpack_from("<10I", blob, table + i * 40) for i in range(count)]

    def contents(section):
        offset, size = section[4:6]
        if offset + size > len(blob):
            raise ElfAuditError("ELF section is outside file")
        return blob[offset:offset + size]

    if headers[names_index][1] != 3:
        raise ElfAuditError("invalid ELF string table")
    strings = contents(headers[names_index])
    result = {}
    for section in headers:
        name_offset = section[0]
        if name_offset >= len(strings):
            raise ElfAuditError("invalid ELF section name")
        end = strings.find(b"\0", name_offset, name_offset + 256)
        if end < 0:
            raise ElfAuditError("unterminated ELF section name")
        try:
            name = strings[name_offset:end].decode("ascii")
        except UnicodeDecodeError as error:
            raise ElfAuditError("invalid ELF section name") from error
        if not name:
            continue
        if name in result and name in (".fapmeta", ".fapassets"):
            raise ElfAuditError("duplicate ELF section name")
        result[name] = b"" if section[1] == 8 else contents(section)
    manifest = result.get(".fapmeta", b"")
    if len(manifest) != 85 or struct.unpack_from("<II", manifest) != (0x52474448, 1):
        raise ElfAuditError("invalid FAP manifest")
    minor, major, target = struct.unpack_from("<HHH", manifest, 8)
    return result, (major, minor, target)


def bundled_files(blob):
    if not 36 <= len(blob) <= MAX_BYTES:
        raise ElfAuditError("invalid asset bundle size")
    magic, version, dirs, files, signature = struct.unpack_from("<5I", blob)
    if (magic, version, signature) != (0x4F4C5A44, 1, 16) or dirs + files > 4096:
        raise ElfAuditError("invalid asset bundle header")
    offset = 36
    digest = hashlib.md5()
    seen = set()

    def take(size):
        nonlocal offset
        if size > MAX_BYTES or offset + size > len(blob):
            raise ElfAuditError("truncated asset bundle")
        data = blob[offset:offset + size]
        offset += size
        return data

    def number():
        return struct.unpack("<I", take(4))[0]

    def name():
        size = number()
        if not 1 < size <= 4096:
            raise ElfAuditError("invalid asset name size")
        data = take(size)
        if data[-1:] != b"\0" or b"\0" in data[:-1]:
            raise ElfAuditError("invalid asset name")
        try:
            path = safe_path(data[:-1].decode("ascii"))
        except UnicodeDecodeError as error:
            raise ElfAuditError("invalid asset name encoding") from error
        if path in seen:
            raise ElfAuditError("duplicate asset path")
        seen.add(path)
        digest.update(data)
        return path

    for _ in range(dirs):
        name()
    result = {}
    for _ in range(files):
        path = name()
        content = take(number())
        digest.update(content)
        result[path] = content
    if offset != len(blob) or digest.digest() != blob[20:36]:
        raise ElfAuditError("asset bundle checksum/trailing data mismatch")
    return result
