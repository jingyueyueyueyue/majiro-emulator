"""Independent binary fixtures for corruption, decoding, branching and extraction bounds."""
from __future__ import annotations
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_arc import Archive
from majiro_mjo import Script, crypt
from port_common import ROOT, archive_dir, write_json


def mjo(code, main=0, entries=((0x1D128F30, 0),), encrypted=True):
    signature = b"MajiroObjX1.000\0" if encrypted else b"MajiroObjV1.000\0"
    return (struct.pack("<16sIII", signature, main, 0, len(entries))
            + b"".join(struct.pack("<II", *entry) for entry in entries)
            + struct.pack("<I", len(code)) + (crypt(code) if encrypted else code))


def arc(version, members):
    """Independent fixture writer; deliberately not shared with the reader."""
    stride = {1: 8, 2: 12, 3: 16}[version]
    names = b"".join(name + b"\0" for name, _ in members)
    names_offset = 28 + (len(members) + (version == 1)) * stride
    data_offset = names_offset + len(names)
    cursor = data_offset
    entries = []
    for name, payload in members:
        hash_value = zlib.crc32(name)
        if version == 1:
            entries.append(struct.pack("<II", hash_value, cursor))
        elif version == 2:
            entries.append(struct.pack("<III", hash_value, cursor, len(payload)))
        else:
            entries.append(struct.pack("<QII", hash_value | (1 << 40), cursor, len(payload)))
        cursor += len(payload)
    if version == 1:
        entries.append(struct.pack("<II", 0, cursor))
    return (struct.pack("<16sIII", f"MajiroArcV{version}.000".encode() + b"\0",
                        len(members), names_offset, data_offset)
            + b"".join(entries) + names + b"".join(value for _, value in members))


class FormatTests(unittest.TestCase):
    def test_independent_arithmetic_fixture(self):
        code = struct.pack("<HiHiHH", 0x800, 12, 0x800, -3, 0x100, 0x82B)
        parsed = Script("fixture.mjo", mjo(code))
        self.assertEqual([x.name for x in parsed.instructions], ["ldc.i", "ldc.i", "mul.i", "ret"])
        self.assertEqual(parsed.instructions[1].operands, (-3,))
        self.assertEqual(parsed.code, code)
        self.assertEqual(crypt(crypt(code)), code)
        self.assertEqual(parsed.code, Script("plain.mjo", mjo(code, encrypted=False)).code)

    def test_relative_jump_resolves_after_operand(self):
        code = struct.pack("<HiHH", 0x82C, 2, 0x191, 0x82B)
        self.assertEqual(Script("jump.mjo", mjo(code)).instructions[0].targets, (8,))
        bad = struct.pack("<HiHH", 0x82C, 1, 0x191, 0x82B)
        with self.assertRaisesRegex(ValueError, "instruction boundary"):
            Script("bad_jump.mjo", mjo(bad))

    def test_function_only_script_preserves_absent_entrypoint(self):
        code = struct.pack("<HiH", 0x800, 0, 0x82B)
        parsed = Script("library.mjo", mjo(code, main=0xFFFFFFFF))
        self.assertFalse(parsed.has_main)
        self.assertEqual(parsed.main, 0xFFFFFFFF)
        self.assertEqual(parsed.functions, [(0x1D128F30, 0)])
        self.assertEqual(parsed.runtime_data()["main"], 0xFFFFFFFF)
        with self.assertRaisesRegex(ValueError, "main/function entry"):
            Script("invalid_entry.mjo", mjo(code, main=0xFFFFFFFE))
        with self.assertRaisesRegex(ValueError, "main/function entry"):
            Script("invalid_function.mjo", mjo(code, main=0xFFFFFFFF, entries=((1, 1),)))

    def test_switch_offsets_relative_to_each_table_entry(self):
        code = struct.pack("<HHiiHH", 0x850, 2, 4, 2, 0x191, 0x82B)
        self.assertEqual(Script("switch.mjo", mjo(code)).instructions[0].targets, (12, 14))

    def test_cp932_keeps_original_control_and_unicode(self):
        source = "\u65e5\u672c\\n\x01".encode("cp932") + b"\0"
        code = struct.pack("<HH", 0x840, len(source)) + source + struct.pack("<H", 0x82B)
        self.assertEqual(Script("text.mjo", mjo(code)).instructions[0].operands[0], "\u65e5\u672c\\n\x01")

    def test_truncation_and_unknown_opcode_stop(self):
        valid = mjo(struct.pack("<HiH", 0x800, 4, 0x82B))
        for bad in (valid[:-1], valid + b"x", valid[:29], mjo(struct.pack("<H", 0xFFFF))):
            with self.assertRaises(ValueError):
                Script("invalid.mjo", bad)
        with self.assertRaisesRegex(ValueError, "placeholder"):
            Script("call.mjo", mjo(struct.pack("<HIIHH", 0x80F, 1, 1, 0, 0x82B)))

    def test_archive_member_range_and_payload_limit(self):
        parent = ROOT / "archive" / "2026-11"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="20261108_format_fixture_", dir=parent) as td:
            path = Path(td) / "fixture.arc"
            name = b"test.mjo\0"
            offset = 28 + 16 + len(name)
            payload = b"test-payload"
            data = (struct.pack("<16sIII", b"MajiroArcV3.000\0", 1, 44, offset)
                    + struct.pack("<QII", 0, offset, len(payload)) + name + payload)
            path.write_bytes(data)
            member = Archive(path).members[0]
            self.assertEqual(member.read(), payload)
            self.assertEqual(b"".join(member.chunks(3)), payload)
            with self.assertRaises(ValueError):
                member.read(limit=1)
            path.write_bytes(data[:-1])
            with self.assertRaises(ValueError):
                Archive(path)

    def test_all_archive_versions_keep_bytes_and_names(self):
        parent = archive_dir("arc_version_fixtures")
        members = [(b"start.mjo", b"script\0payload"),
                   ("\u80cc\u666f.rct".encode("cp932"), b""),
                   (b"last.bin", bytes(range(256)))]
        for version in (1, 2, 3):
            with self.subTest(version=version):
                path = parent / f"{parent.name[:8]}_v{version}.arc"
                path.write_bytes(arc(version, members))
                parsed = Archive(path)
                self.assertEqual(parsed.version, version)
                self.assertEqual([m.raw_name for m in parsed.members], [m[0] for m in members])
                self.assertEqual([m.read() for m in parsed.members], [m[1] for m in members])
                self.assertEqual(b"".join(parsed.members[-1].chunks(17)), members[-1][1])
                empty = parent / f"{parent.name[:8]}_empty_v{version}.arc"
                empty.write_bytes(arc(version, []))
                self.assertEqual(Archive(empty).members, [])

    def test_v1_terminal_entry_is_validated(self):
        parent = archive_dir("arc_corruption_fixtures")
        source = arc(1, [(b"a", b"123"), (b"b", b"456")])
        for index, (offset, value) in enumerate(((44, 1), (48, len(source) - 1),
                                               (40, 0), (20, 28))):
            with self.subTest(offset=offset):
                damaged = bytearray(source)
                struct.pack_into("<I", damaged, offset, value)
                path = parent / f"{parent.name[:8]}_bad_v1_{index}.arc"
                path.write_bytes(damaged)
                with self.assertRaises(ValueError):
                    Archive(path)

    def test_v2_sizes_cannot_escape_or_overlap(self):
        parent = archive_dir("arc_v2_corruption_fixtures")
        source = arc(2, [(b"a", b"123"), (b"b", b"456")])
        for index, (offset, value) in enumerate(((36, 0xFFFFFFFF), (44, 56), (20, 0))):
            with self.subTest(offset=offset):
                damaged = bytearray(source)
                struct.pack_into("<I", damaged, offset, value)
                path = parent / f"{parent.name[:8]}_bad_v2_{index}.arc"
                path.write_bytes(damaged)
                with self.assertRaises(ValueError):
                    Archive(path)


def run():
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(FormatTests))
    print(stream.getvalue())
    report = {"status": "passed" if result.wasSuccessful() else "failed",
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors)}
    write_json(ROOT / "reports/format_tests.json", report)
    if not result.wasSuccessful():
        evidence = archive_dir("format_failures")
        (evidence / "test_output.txt").write_text(stream.getvalue(), encoding="utf-8")
        raise RuntimeError("format tests failed")
    return report


if __name__ == "__main__":
    run()
