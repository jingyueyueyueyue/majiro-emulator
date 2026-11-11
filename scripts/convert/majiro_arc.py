"""Bounded, read-only Majiro ARC V1/V2/V3 reader. No extraction is implicit."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import struct


@dataclass(frozen=True)
class Member:
    archive: Path
    index: int
    name: str
    raw_name: bytes
    name_hash: int
    offset: int
    size: int

    @property
    def identity(self):
        return self.archive.name + ":" + str(self.index)

    @property
    def extension(self):
        return PurePosixPath(self.name).suffix.lower()

    def read(self, limit=64 * 1024 * 1024):
        if self.size > limit:
            raise ValueError(f"{self.identity}: {self.size} exceeds read limit {limit}")
        with self.archive.open("rb") as stream:
            stream.seek(self.offset)
            data = stream.read(self.size)
        if len(data) != self.size:
            raise ValueError(f"{self.identity}: truncated member")
        return data

    def chunks(self, block=1024 * 1024):
        with self.archive.open("rb") as stream:
            stream.seek(self.offset)
            remaining = self.size
            while remaining:
                data = stream.read(min(block, remaining))
                if not data:
                    raise ValueError(f"{self.identity}: truncated member")
                yield data
                remaining -= len(data)

    def digest(self):
        result = hashlib.sha256()
        for chunk in self.chunks():
            result.update(chunk)
        return result.hexdigest()


class Archive:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        with self.path.open("rb") as stream:
            header = stream.read(28)
            signatures = {
                b"MajiroArcV1.000\0": 1,
                b"MajiroArcV2.000\0": 2,
                b"MajiroArcV3.000\0": 3,
            }
            if len(header) != 28 or header[:16] not in signatures:
                raise ValueError(f"{path.name}: unsupported archive signature")
            self.version = signatures[header[:16]]
            count, names_offset, data_offset = struct.unpack_from("<III", header, 16)
            entry_size = {1: 8, 2: 12, 3: 16}[self.version]
            table_count = count + (1 if self.version == 1 else 0)
            if not (count <= 1_000_000 and names_offset == 28 + table_count * entry_size
                    and names_offset <= data_offset <= self.size
                    and data_offset - names_offset <= 64 * 1024 * 1024):
                raise ValueError(f"{path.name}: invalid index boundaries")
            entries = stream.read(table_count * entry_size)
            names_data = stream.read(data_offset - names_offset)
        if len(entries) != table_count * entry_size or len(names_data) != data_offset - names_offset:
            raise ValueError(f"{path.name}: truncated index")
        if (count and not names_data.endswith(b"\0")) or names_data.count(b"\0") != count:
            raise ValueError(f"{path.name}: filename count mismatch")
        if not count and names_data:
            raise ValueError(f"{path.name}: data in empty filename table")
        if self.version == 1:
            sentinel_hash, sentinel_offset = struct.unpack_from("<II", entries, count * entry_size)
            if sentinel_hash != 0 or sentinel_offset != self.size:
                raise ValueError(f"{path.name}: invalid V1 terminal entry")
        raw_names = names_data.split(b"\0")[:-1] if count else []
        self.members = []
        for index, raw_name in enumerate(raw_names):
            at = index * entry_size
            if self.version == 1:
                name_hash, offset = struct.unpack_from("<II", entries, at)
                next_offset, = struct.unpack_from("<I", entries, at + entry_size + 4)
                size = next_offset - offset
            elif self.version == 2:
                name_hash, offset, size = struct.unpack_from("<III", entries, at)
            else:
                name_hash, offset, size = struct.unpack_from("<QII", entries, at)
            if not (data_offset <= offset <= offset + size <= self.size):
                raise ValueError(f"{path.name}[{index}]: out-of-bounds member")
            name = raw_name.decode("cp932", errors="strict")
            if not name or "\0" in name:
                raise ValueError(f"{path.name}[{index}]: invalid filename")
            self.members.append(Member(self.path, index, name, raw_name, name_hash, offset, size))
        intervals = sorted((m.offset, m.offset + m.size) for m in self.members if m.size)
        for (_, end), (start, _) in zip(intervals, intervals[1:]):
            if end > start:
                raise ValueError(f"{path.name}: overlapping payloads")
        self.index_sha256 = hashlib.sha256(header + entries + names_data).hexdigest()


class Catalogue:
    """Highest lexical archive filename first, pending original EXE validation."""
    def __init__(self, root: Path, include_patch=True):
        self.archives = [Archive(p) for p in sorted(root.glob("*.arc"), key=lambda p: p.name, reverse=True)]
        self.by_name = {}
        self.lookup = {}
        for archive in self.archives:
            for member in archive.members:
                self.by_name.setdefault(member.name, []).append(member)
                self.lookup.setdefault(self.key(member.name), []).append(member)
        self.patch=None
        if include_patch and (root/"localization/patch.lock.json").is_file():
            from majiro_patch import Patch
            self.patch=Patch(root)
            for member in self.patch.members:
                self.by_name.setdefault(member.name,[]).insert(0,member)
                self.lookup.setdefault(self.key(member.name),[]).insert(0,member)

    def all_members(self):
        for archive in self.archives:
            yield from archive.members
        if self.patch:
            yield from self.patch.members

    @staticmethod
    def key(name: str):
        return name.replace("\\", "/").casefold()

    def get(self, name: str) -> Member:
        try:
            return self.lookup[self.key(name)][0]
        except KeyError:
            raise KeyError(f"missing source member: {name}") from None

    def resolve(self, name: str, extensions=()):
        for candidate in (name, *(name + ext for ext in extensions)):
            if self.key(candidate) in self.lookup:
                return self.get(candidate)
        raise KeyError(f"unresolved resource: {name}")
