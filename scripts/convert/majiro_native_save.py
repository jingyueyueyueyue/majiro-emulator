"""Strict, read-only native Majiro system/readmark decoding.

This module exports portable data; it does not claim native story-slot restore.
Strings retain their byte values. Serialized pointers and linked-list addresses
are evidence only and are never dereferenced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

SYSTEM_SIGNATURE = b"MajiroSysV1.100\0"
STORY_SIGNATURES = (b"MajiroSavV1.103\0", b"MajiroSavV1.105\0")
SYSTEM_HEADER = 0x9C58
READMARK_HEADER = 0x94
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 1_000_000


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _u32(data, at):
    _require(0 <= at <= len(data) - 4, "truncated native save field")
    return struct.unpack_from("<I", data, at)[0]


def _signed(value):
    return value if value < 0x80000000 else value - 0x100000000


def identify(data: bytes):
    """Identify supported signatures without inferring a headerless file type."""
    if data.startswith(SYSTEM_SIGNATURE):
        return "system"
    if data[:16] in STORY_SIGNATURES:
        return data[:15].decode("ascii")
    return "unknown"


class _Pool:
    def __init__(self, raw):
        self.raw = raw
        self.objects = {}
        self.kinds = {}
        self.spans = {}

    def reserve(self, offset, length, kind):
        _require(_u32(self.raw, offset + 4) == offset,
                 "native object serialization offset disagrees with reference")
        _require(not any(offset < end and begin < offset + length
                         for begin, end in self.spans.values()),
                 "overlapping native serialized objects")
        self.spans[offset] = (offset, offset + length)
        self.kinds[offset] = kind

    def string(self, offset):
        _require(0 <= offset <= len(self.raw) - 12, "string offset outside native object pool")
        if offset in self.objects:
            _require(self.kinds[offset] == "string", "native object alias has conflicting types")
            return self.objects[offset]
        length = _u32(self.raw, offset + 8)
        _require(1 <= length <= len(self.raw) - offset - 12, "invalid native string length")
        payload = self.raw[offset + 12:offset + 12 + length]
        _require(payload.endswith(b"\0") and b"\0" not in payload[:-1],
                 "native string length disagrees with C string")
        self.reserve(offset, 12 + length, "string")
        value = {"kind": "string", "object_offset": offset, "bytes_hex": payload[:-1].hex()}
        self.objects[offset] = value
        self.kinds[offset] = "string"
        return value

    def array(self, offset, tag):
        _require(0 <= offset <= len(self.raw) - 24, "array offset outside native object pool")
        kind = ("int_array", "float_array", "string_array")[tag - 3]
        if offset in self.objects:
            _require(self.kinds[offset] == kind, "native object alias has conflicting types")
            return self.objects[offset]
        dims = struct.unpack_from("<3I", self.raw, offset + 8)
        _require(all(d <= MAX_RECORDS for d in dims), "native array dimension exceeds bound")
        count = dims[0] * dims[1] * dims[2]
        _require(count <= MAX_RECORDS and 24 + 4 * count <= len(self.raw) - offset,
                 "native array size outside object pool")
        self.reserve(offset, 24 + 4 * count, kind)
        values = []
        value = {"kind": kind, "object_offset": offset, "dimensions": list(dims), "values": values}
        self.objects[offset], self.kinds[offset] = value, kind
        start = offset + (24 if tag == 5 else 20)
        for at in range(start, start + count * 4, 4):
            bits = _u32(self.raw, at)
            if tag == 3:
                values.append(_signed(bits))
            elif tag == 4:
                # Preserve NaN payloads and signed zero without nonstandard JSON.
                values.append({"float32_bits": f"{bits:08x}"})
            elif bits == 0xFFFFFFFF:
                values.append({"kind": "string", "bytes_hex": "", "null_native_string": True})
            else:
                values.append(self.string(bits))
        return value


def parse_system(data: bytes):
    _require(len(data) <= MAX_FILE_BYTES, "native save exceeds size bound")
    _require(data[:16] == SYSTEM_SIGNATURE, "unsupported native system-save signature")
    _require(len(data) >= SYSTEM_HEADER, "truncated native system-save header")
    count, pool_size = struct.unpack_from("<II", data, 16)
    _require(count <= MAX_RECORDS, "native variable count exceeds bound")
    _require(SYSTEM_HEADER + count * 16 + pool_size == len(data),
             "native system-save size mismatch or trailing data")
    pool = _Pool(data[SYSTEM_HEADER + count * 16:])
    variables = []
    hashes = set()
    for at in range(SYSTEM_HEADER, SYSTEM_HEADER + count * 16, 16):
        hash_, tag, offset, payload = struct.unpack_from("<4I", data, at)
        _require(hash_ not in hashes, "duplicate native persistent-variable hash")
        _require(tag <= 5, "unsupported native persistent-variable tag")
        hashes.add(hash_)
        if tag == 0:
            value = _signed(payload)
        elif tag == 1:
            value = {"float32_bits": f"{payload:08x}"}
        elif tag == 2:
            value = pool.string(offset)
        else:
            value = pool.array(offset, tag)
        variables.append({"hash": f"{hash_:08x}", "native_type": tag, "value": value})
    pictures = struct.unpack_from("<10000I", data, 24)
    # The source uses an open-address-free list terminated by the first zero.
    used = pictures.index(0) if 0 in pictures else len(pictures)
    _require(not any(pictures[used:]), "nonzero native picture hashes after terminator")
    return {
        "schema": 1, "format": "MajiroSysV1.100", "source_sha256": hashlib.sha256(data).hexdigest(),
        "native_story_restore_supported": False, "variable_count": count,
        "object_pool_bytes": pool_size, "variables": variables,
        "picture_basename_hashes": [f"{v:08x}" for v in pictures[:used]],
        "scope": "decoded native persistent values and picture-name hashes; no script-version mapping",
    }


def parse_readmarks(data: bytes, encoding="cp932"):
    """Explicitly parse the signatureless readmark format, never auto-detect it."""
    _require(len(data) <= MAX_FILE_BYTES, "native readmarks exceed size bound")
    records = []
    seen = set()
    cursor = 0
    while cursor < len(data):
        _require(len(records) < MAX_RECORDS, "native readmark count exceeds bound")
        _require(cursor + READMARK_HEADER <= len(data), "truncated native readmark header")
        header = data[cursor:cursor + READMARK_HEADER]
        name = header[:128]
        _require(b"\0" in name, "unterminated native readmark script name")
        raw_name, padding = name.split(b"\0", 1)
        _require(raw_name and not any(padding), "invalid native readmark script-name padding")
        try:
            script = raw_name.decode(encoding, errors="strict")
        except UnicodeError as error:
            raise ValueError("native readmark name cannot be decoded") from error
        _require(script.lower().endswith(".mjo") and not any(c in script for c in ("/", "\\", ":")),
                 "invalid native readmark script name")
        _require(script.casefold() not in seen, "duplicate native readmark script name")
        seen.add(script.casefold())
        size, line_count = struct.unpack_from("<II", header, 128)
        _require(line_count <= 0x7FFFFFFF and size == (line_count + 7) // 8 + 16,
                 "native readmark bitset length mismatch")
        begin, end = cursor + READMARK_HEADER, cursor + READMARK_HEADER + size
        _require(end <= len(data), "truncated native readmark bitset")
        bits = data[begin:end]
        # Signed line opcode IDs are used directly as byte/bit indexes. Export
        # the whole bitset; only an exact matching script may map them to PCs.
        records.append({"script": script, "script_bytes_hex": raw_name.hex(),
                        "line_count": line_count, "bits_hex": bits.hex(),
                        "marked_line_ids": [line for line in range(line_count + 1)
                                            if bits[line >> 3] & (1 << (line & 7))],
                        "source_offset": cursor})
        cursor = end
    return {"schema": 1, "format": "Majiro native readmarks", "record_count": len(records),
            "source_sha256": hashlib.sha256(data).hexdigest(), "records": records,
            "native_story_restore_supported": False,
            "scope": "native line-id bitsets; target script identity still requires explicit verification"}


def read_native(path: Path, kind=None):
    _require(path.stat().st_size <= MAX_FILE_BYTES, "native save exceeds size bound")
    data = path.read_bytes()
    detected = identify(data)
    if kind == "readmarks":
        return parse_readmarks(data)
    if kind in (None, "system") and detected == "system":
        return parse_system(data)
    if detected.startswith("MajiroSav"):
        raise ValueError(f"{detected} story-slot import is not implemented; refusing partial restore")
    raise ValueError("unknown native save; signatureless readmarks require explicit --kind readmarks")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--kind", choices=("system", "readmarks"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = read_native(args.source, args.kind)
    _require(args.source.resolve() != args.output.resolve(), "refusing to overwrite native source")
    _require(not args.output.exists(), "refusing to overwrite existing export")
    with args.output.open("x", encoding="utf8") as stream:
        stream.write(json.dumps(result, ensure_ascii=True, indent=2) + "\n")
    print(f"decoded {result['format']}; portable export: {args.output}")


if __name__ == "__main__":
    main()
