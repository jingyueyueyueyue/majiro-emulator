"""Compare native system/readmark decoding with bounded original x86 readers."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

import unicorn
from unicorn import x86_const as reg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_native_save import (READMARK_HEADER, STORY_SIGNATURES, SYSTEM_HEADER,
                                SYSTEM_SIGNATURE, identify, parse_readmarks, parse_system,
                                read_native)
from port_common import ROOT, archive_dir, timestamp, write_json
from original_opcode import OpcodeOracle


class NativeSaveOracle(OpcodeOracle):
    """Only memory allocation/copy and in-memory file I/O are host boundaries."""
    STOP = 0x30000000

    def __init__(self, sample):
        super().__init__(sample)
        self.machine.mem_map(self.STOP, 4096)
        self.heap = self.DATA + 0x20000
        self.boundaries = Counter()
        self.file_data = b""
        self.file_cursor = 0
        hooks = {
            0x484ED0: self.allocate_ecx, 0x497CAB: self.allocate_stack,
            0x49C4F0: self.copy, 0x49CBC0: self.fill,
            0x494396: self.open, 0x495739: self.read,
            0x494B54: self.close, 0x494034: self.free,
            0x45E940: self.path,
        }
        for at, fn in hooks.items():
            self.machine.hook_add(unicorn.UC_HOOK_CODE, fn, begin=at, end=at)

    def ret(self, value=0):
        esp = self.machine.reg_read(reg.UC_X86_REG_ESP)
        self.machine.reg_write(reg.UC_X86_REG_EAX, value)
        self.machine.reg_write(reg.UC_X86_REG_EIP, self.read_u32(esp))
        self.machine.reg_write(reg.UC_X86_REG_ESP, esp + 4)

    def argument(self, index):
        return self.read_u32(self.machine.reg_read(reg.UC_X86_REG_ESP) + 4 + index * 4)

    def allocate(self, size):
        if size > 0x40000:
            raise ValueError("original native-save allocation exceeds bound")
        at = (self.heap + 15) & ~15
        self.heap = at + size + 32
        if self.heap >= self.DATA + 0xF0000:
            raise ValueError("original native-save heap exhausted")
        self.machine.mem_write(at, bytes(size + 16))
        self.boundaries["allocate"] += 1
        self.ret(at)

    def allocate_ecx(self, uc, at, size, unused):
        self.allocate(uc.reg_read(reg.UC_X86_REG_ECX))

    def allocate_stack(self, uc, at, size, unused):
        self.allocate(self.argument(0))

    def copy(self, uc, at, size, unused):
        dst, src, length = [self.argument(i) for i in range(3)]
        if length > 0x40000:
            raise ValueError("original native-save copy exceeds bound")
        uc.mem_write(dst, bytes(uc.mem_read(src, length)))
        self.boundaries["copy"] += 1
        self.ret(dst)

    def fill(self, uc, at, size, unused):
        dst, byte, length = [self.argument(i) for i in range(3)]
        if length > 0x40000:
            raise ValueError("original native-save fill exceeds bound")
        uc.mem_write(dst, bytes([byte & 255]) * length)
        self.ret(dst)

    def path(self, uc, at, size, unused):
        self.ret(uc.reg_read(reg.UC_X86_REG_ECX))

    def open(self, uc, at, size, unused):
        self.file_cursor = 0
        self.ret(0x1234)

    def read(self, uc, at, size, unused):
        dst, length, count, handle = [self.argument(i) for i in range(4)]
        if handle != 0x1234 or count != 1 or length > 0x40000:
            raise ValueError("unexpected original native-save fread")
        data = self.file_data[self.file_cursor:self.file_cursor + length]
        self.file_cursor += len(data)
        if data:
            uc.mem_write(dst, data)
        self.boundaries["fread"] += 1
        self.ret(int(len(data) == length))

    def close(self, uc, at, size, unused):
        self.ret(0)

    def free(self, uc, at, size, unused):
        self.ret(0)

    def call(self, address, ecx=0, edx=0, args=()):
        stack = self.STACK + 0xF000
        self.machine.mem_write(stack, struct.pack("<" + "I" * (len(args) + 1), self.STOP, *args))
        self.machine.reg_write(reg.UC_X86_REG_ESP, stack)
        self.machine.reg_write(reg.UC_X86_REG_EBP, 0)
        self.machine.reg_write(reg.UC_X86_REG_ECX, ecx)
        self.machine.reg_write(reg.UC_X86_REG_EDX, edx)
        self.machine.emu_start(address, self.STOP, count=2_000_000)
        if self.machine.reg_read(reg.UC_X86_REG_EIP) != self.STOP:
            raise ValueError("native-save original reader exceeded instruction budget")

    def system_values(self, data):
        parsed = parse_system(data)
        self.heap = self.DATA + 0x20000
        count, size = struct.unpack_from("<II", data, 16)
        pool = self.DATA + 0x4000
        raw_pool = data[SYSTEM_HEADER + count * 16:]
        self.machine.mem_write(pool, raw_pool)
        rows = []
        for index, value in enumerate(parsed["variables"]):
            src, dst = self.DATA + 0x1000, self.DATA + 0x1020
            raw = data[SYSTEM_HEADER + index * 16:SYSTEM_HEADER + (index + 1) * 16]
            self.machine.mem_write(src, raw)
            self.call(0x4511A0, dst, src, (pool, 0))
            hash_, tag, _, payload = struct.unpack("<4I", self.machine.mem_read(dst, 16))
            if tag in (0, 1):
                decoded = payload
                expected = (value["value"] & 0xFFFFFFFF) if tag == 0 else int(value["value"]["float32_bits"], 16)
            elif tag == 2:
                length = self.read_u32(payload + 8)
                decoded = bytes(self.machine.mem_read(payload + 12, length - 1)).hex()
                expected = value["value"]["bytes_hex"]
            else:
                dims = tuple(self.read_u32(payload + offset) for offset in (8, 12, 16))
                count_ = dims[0] * dims[1] * dims[2]
                values = [self.read_u32(payload + 20 + i * 4) for i in range(count_)]
                if tag == 3:
                    decoded = [v if v < 0x80000000 else v - 0x100000000 for v in values]
                    expected = value["value"]["values"]
                elif tag == 4:
                    decoded = [{"float32_bits": f"{v:08x}"} for v in values]
                    expected = value["value"]["values"]
                else:
                    decoded = [bytes(self.machine.mem_read(v + 12, self.read_u32(v + 8) - 1)).hex()
                               for v in values]
                    expected = [v["bytes_hex"] for v in value["value"]["values"]]
                if dims != tuple(value["value"]["dimensions"]):
                    raise AssertionError("native array dimensions disagree")
            assert decoded == expected, (value["hash"], tag, decoded, expected)
            assert hash_ == int(value["hash"], 16)
            rows.append(tag)
        return dict(Counter(rows))

    def readmark_records(self, data):
        self.heap = self.DATA + 0x20000
        self.file_data = data
        self.write_u32(0x4E6820, 0)
        self.write_u32(0x4E6818, 0)
        self.call(0x439800)
        pointer = self.read_u32(0x4E6818)
        rows = []
        while pointer:
            if len(rows) > 1000:
                raise ValueError("native readmark list exceeds fixture bound")
            header = bytes(self.machine.mem_read(pointer, READMARK_HEADER))
            size, line_count = struct.unpack_from("<II", header, 128)
            name = header[:128].split(b"\0", 1)[0]
            bits = bytes(self.machine.mem_read(self.read_u32(pointer + 0x88), size))
            rows.append((name.hex(), line_count, bits.hex()))
            pointer = self.read_u32(pointer + 0x8C)
        return list(reversed(rows))


def expect_invalid(fn, data):
    try:
        fn(data)
    except ValueError:
        return
    raise AssertionError("malformed native file was accepted")


def synthetic_system():
    """Independent serialized shapes exercise types absent from real saves."""
    pool, rows = bytearray(), []

    def string(raw):
        at = len(pool)
        pool.extend(struct.pack("<3I", 1, at, len(raw) + 1) + raw + b"\0")
        return at

    first, second = string(b"first"), string(b"\x82\xa0")
    objects = []
    for tag, dims, values in (
        (3, (2, 1, 1), (0x80000000, 0x7FFFFFFF)),
        (4, (1, 2, 1), (0x80000000, 0x7FC00001)),
        (5, (1, 1, 2), (first, second)),
        (3, (0, 1, 1), ()),
        (4, (2, 0, 9), ()),
        (5, (3, 2, 0), ()),
    ):
        at = len(pool)
        header = struct.pack("<5I", 1, at, *dims)
        payload = struct.pack("<" + "I" * len(values), *values)
        # Native serialization reserves 24+count*4 bytes for every array;
        # numeric payload begins at +20, string references begin at +24.
        pool.extend(header + ((bytes(4) + payload) if tag == 5 else (payload + bytes(4))))
        objects.append((tag, at))
    rows = [(1, 0, 0, 0xFFFFFFFF), (2, 1, 0, 0x80000000),
            (3, 1, 0, 0x7FC00001), (4, 2, first, 0xBAD0BAD0),
            (5, 2, first, 0xDEADBEEF)]
    rows += [(i + 6, tag, offset, 0xDEADBEEF) for i, (tag, offset) in enumerate(objects)]
    header = SYSTEM_SIGNATURE + struct.pack("<II", len(rows), len(pool))
    header += bytes(SYSTEM_HEADER - len(header))
    return header + b"".join(struct.pack("<4I", *row) for row in rows) + pool


def main():
    evidence = archive_dir("native_saves")
    samples = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    sample = next(s for s in samples if s["sample"] == "narci_onetwo")
    oracle = NativeSaveOracle(sample)
    inventory = json.loads((ROOT / "reports/majiro_samples.json").read_text(encoding="utf8"))["games"]
    cases, negatives = [], 0
    for game in inventory:
        for save in game.get("save_signatures", []):
            path = Path(game["directory"]) / save["path"]
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            data = path.read_bytes()
            kind = "readmarks" if "readmark" in save["path"] else "system"
            parsed = read_native(path, kind)
            row = {"sample": Path(game["directory"]).name, "path": save["path"],
                   "sha256": before, "bytes": len(data), "kind": kind}
            if kind == "system":
                row["native_type_counts"] = oracle.system_values(data)
                row["variable_count"] = parsed["variable_count"]
                row["picture_hashes"] = len(parsed["picture_basename_hashes"])
                fn = parse_system
                for mutant in (data[:-1], data + b"\0", data[:20] + b"\xff"*4 + data[24:],
                               data[:SYSTEM_HEADER+4] + b"\x06\0\0\0" + data[SYSTEM_HEADER+8:]):
                    expect_invalid(fn, mutant)
                    negatives += 1
            else:
                expected = [(r["script_bytes_hex"], r["line_count"], r["bits_hex"]) for r in parsed["records"]]
                assert oracle.readmark_records(data) == expected
                row["records"] = parsed["record_count"]
                fn = parse_readmarks
                for mutant in (data[:-1], data + b"\0", b"x"*128 + data[128:],
                               data[:128] + b"\xff"*4 + data[132:]):
                    expect_invalid(fn, mutant)
                    negatives += 1
            assert before == hashlib.sha256(path.read_bytes()).hexdigest(), "source save changed"
            row["status"] = "passed"
            cases.append(row)
    for sig in STORY_SIGNATURES:
        assert identify(sig + bytes(128)) == sig[:15].decode("ascii")
        expect_invalid(parse_system, sig + bytes(SYSTEM_HEADER))
        negatives += 1
    # Empty, truncated and wrong signature inputs must never infer a system file.
    for data in (b"", SYSTEM_SIGNATURE, b"MajiroSysV9.999\0" + bytes(SYSTEM_HEADER)):
        expect_invalid(parse_system, data)
        negatives += 1
    synthetic = synthetic_system()
    synthetic_counts = oracle.system_values(synthetic)
    parsed = parse_system(synthetic)
    values = parsed["variables"]
    assert values[3]["value"] is values[4]["value"], "decoded string alias lost"
    for value in values:
        if isinstance(value["value"], dict) and "dimensions" in value["value"]:
            if 0 in value["value"]["dimensions"]:
                assert value["value"]["values"] == []
    # A duplicate pointer with a different type must not reinterpret an object.
    conflict = bytearray(synthetic)
    struct.pack_into("<I", conflict, SYSTEM_HEADER + 4 * 16 + 4, 3)
    expect_invalid(parse_system, bytes(conflict))
    negatives += 1
    # Corrupt a serialized object's own offset without changing the reference.
    corrupt = bytearray(synthetic)
    pool_start = SYSTEM_HEADER + len(values) * 16
    struct.pack_into("<I", corrupt, pool_start + 4, 8)
    expect_invalid(parse_system, bytes(corrupt))
    negatives += 1
    overlap_pool = bytearray(struct.pack("<6I", 1, 0, 6, 1, 1, 0) + bytes(24))
    struct.pack_into("<6I", overlap_pool, 24, 1, 24, 0, 1, 1, 0)
    overlap_header = SYSTEM_SIGNATURE + struct.pack("<II", 2, len(overlap_pool))
    overlap_header += bytes(SYSTEM_HEADER - len(overlap_header))
    overlap = overlap_header + struct.pack("<8I", 1, 3, 0, 0, 2, 3, 24, 0) + overlap_pool
    expect_invalid(parse_system, overlap)
    negatives += 1
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "cases": cases,
              "test_sources": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ("scripts/analysis/test_native_saves.py",
                                            "scripts/convert/majiro_native_save.py")},
              "native_reference": {"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
                                   "deserializer_va": "004511a0", "readmark_reader_va": "00439800"},
              "negative_cases": negatives, "host_boundaries": dict(oracle.boundaries),
              "synthetic_differential_type_counts": synthetic_counts,
              "scope": "real persistent-value decoding and readmark reader compared to one original EXE",
              "native_story_restore_supported": False, "runtime_import_integrated": False,
              "limitations": ["No real MajiroSavV1.103/V1.105 story-slot fixtures present.",
                              "Decoded values are an export; no partial VM restore is performed.",
                              "Script and translation identity must be verified before mapping line IDs.",
                              "Other EXE builds need their own native reader differential cases."]}
    write_json(evidence / "20261110_native_save_tests.json", report)
    write_json(ROOT / "reports/native_save_format_tests.json", report)
    print(f"native save decoding: {len(cases)} real files, {negatives} rejection cases passed")


if __name__ == "__main__":
    main()
