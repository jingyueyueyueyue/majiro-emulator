"""Recover Happy Princess's initialized reference code without launching it.

The supplied wrapper calls its block initializer with 18 as the key length.
The initializer rejects this AES key size (rounds=0) but retains flags 0x12.
Its block operation consequently XORs the first 16 key bytes and skips rounds.
Native block comparisons validate the recovered development reference page.
The result is analysis evidence only, never a shipped player dependency.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import struct
import sys

import pefile
from unicorn import x86_const as reg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256
from test_new_graphics_abi import FunctionFixture


def invoke(fixture, address, arguments):
    sp = 0x2000f000
    fixture.put(sp, 0x30000000)
    for index, value in enumerate(arguments):
        fixture.put(sp + 4 + index * 4, value)
    fixture.uc.reg_write(reg.UC_X86_REG_ESP, sp)
    fixture.uc.reg_write(reg.UC_X86_REG_EFLAGS, 2)
    fixture.uc.emu_start(address, 0x30000000, count=100000)
    assert fixture.uc.reg_read(reg.UC_X86_REG_EIP) == 0x30000000
    return fixture.uc.reg_read(reg.UC_X86_REG_EAX)


def main():
    manifest = json.loads((ROOT / "config/player_new_samples.json").read_text(encoding="utf8"))
    directory = Path(next(g["directory"] for g in manifest["games"] if g["id"] == "happy_princess"))
    outer = directory / "happyprincess.exe"
    data = outer.read_bytes()
    outer_sha = hashlib.sha256(data).hexdigest()
    assert outer_sha == "4f0838db4c2523d2884bd01a2bf653e642c5fa1de43de300288a6af427f6e50c"
    pe = pefile.PE(data=data)
    image = pe.get_memory_mapped_image()
    pointer = struct.unpack_from("<I", image, 0x46148)[0]
    source = image[pointer - 0x400000:].split(b"\0")[0]
    assert len(source) == 20
    key = base64.b64encode(source)[:16]
    scan = json.loads((ROOT / "reports/new_sample_scan.json").read_text(encoding="utf8"))
    original = next(row for row in scan["opcode_samples"] if row["sample"] == "happy_princess")
    reference = Path(original["path"])
    raw = bytearray(reference.read_bytes())
    assert hashlib.sha256(raw).hexdigest() == "371fc97d0500b2bb20d23a82e604630d52ec9eb994d7bf32aba34eeae08571f1"
    inner = pefile.PE(data=raw)
    section = inner.sections[0]
    assert section.Name.rstrip(b"\0") == b".text" and section.VirtualAddress == 0x1000
    # Exact observed corruption is confined to this 4096-byte code page;
    # later references and the real fetch loop are already plain code.
    page = bytearray(raw[section.PointerToRawData:section.PointerToRawData + 0x1000])
    fixture = FunctionFixture({"path": str(outer), "exe_sha256": outer_sha})
    fixture.uc.mem_write(0x10020000, key + bytes(32))
    initializer_return = invoke(fixture, 0x421d70, [0x10020000, 0x12, 0x44abd0])
    assert initializer_return == 0
    assert fixture.read(0x44abd0 + 0x100) == 0
    assert fixture.read(0x44abd0 + 0x104) == 0x12
    checked = 0
    for offset in range(0, len(page) - 0x14, 0x12):
        source_block = bytes(page[offset:offset + 16])
        fixture.uc.mem_write(0x10030000, source_block)
        fixture.uc.mem_write(0x10040000, bytes(16))
        assert invoke(fixture, 0x423c90, [0x10030000, 0x10040000, 0x44abd0]) == 1
        native = bytes(fixture.uc.mem_read(0x10040000, 16))
        expected = bytes(a ^ b for a, b in zip(source_block, key))
        assert native == expected
        page[offset:offset + 16] = native
        checked += 1
    raw[section.PointerToRawData:section.PointerToRawData + 0x1000] = page
    assert page[0xd20:0xd25] == bytes.fromhex("e81bbc0100")
    folder = archive_dir("happy_initialized_reference")
    normalized = folder / "20261110_happy_initialized_reference.exe"
    normalized.write_bytes(raw)
    recovered_sha = hashlib.sha256(raw).hexdigest()
    # Execute the restored original min wrapper, preserving native argument
    # loads/compare/result push. Only VM argument/result boundaries are fixtures.
    f = FunctionFixture({"path": str(normalized), "exe_sha256": recovered_sha})
    f.hook(0x41d940, lambda sp: f.top)
    f.hook(0x41d960, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
    cases = []
    for values in ([7, -4], [-2147483648, 2147483647], [3, 9, -100]):
        f.call(0x401d20, values)
        expected = min(values[:2]) & 0xffffffff
        assert f.result == expected
        cases.append({"arguments": values, "result_u32": f.result, "status": "passed"})
    result = {
        "schema": 1, "timestamp": timestamp(), "status": "passed",
        "original_container": str(outer), "container_sha256": outer_sha,
        "raw_embedded_reference": str(reference), "raw_embedded_sha256": original["exe_sha256"],
        "initialized_reference": str(normalized), "initialized_reference_sha256": recovered_sha,
        "key_sha256": hashlib.sha256(key).hexdigest(), "native_block_comparisons": checked,
        "native_minimum_cases": cases,
        "modified_file_offset": section.PointerToRawData, "modified_virtual_address": 0x401000,
        "modified_bytes": 4096, "key_initializer": "00421d70", "block_operation": "00423c90",
        "loader_model": {
            "unsupported_key_size": 18, "initializer_returns": 0, "round_count": 0,
            "flags": 18, "stride_bytes": 18, "transformed_bytes_per_stride": 16,
            "tail_condition": "offset < page_length - 20",
            "key_origin": "first 16 bytes of wrapper base64-encoded 20-byte source at pointer 00446148",
        },
        "limits": [
            "Initialized copy is development evidence; the Android player must not load or distribute this EXE.",
            "Only the observed first .text page was restored. This does not assert all protection or startup paths are recovered.",
            "Original input and container files remain untouched. All derived PE output is in archive.",
            "Profile evidence must retain container/raw embedded identities as aliases and the initialized reference hash separately.",
        ],
        "sources_sha256": {"script": sha256(Path(__file__)),
                           "fixture": sha256(ROOT / "scripts/analysis/test_new_graphics_abi.py")},
    }
    write_json(ROOT / "reports/happy_wrapper_recovery.json", result)
    write_json(folder / "20261110_recovery.json", result)
    print("Happy initialized reference:", normalized)
    print("SHA-256:", recovered_sha, "native blocks:", checked, "min cases:", len(cases))


if __name__ == "__main__":
    main()
