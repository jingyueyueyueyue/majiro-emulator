"""Generate archived native story slots using original bounded x86 writers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json
from native_function import NativeFunctions
from original_story_save import LegacyStoryWriter, StorySaveOracle, inspect_story_layout


def assert_values(layout):
    groups = layout["groups"]
    assert groups["globals"][0]["value"] == 0xFFFFFFFE
    assert groups["globals"][1]["value"]["bytes_hex"] == b"story-byte".hex()
    assert groups["locals"][0]["value"] == 0x80000000
    assert groups["locals"][1]["value"]["dimensions"] == [2, 1, 1]
    assert groups["locals"][1]["value"]["values"] == [-2147483648, 7]
    assert groups["stack"][0]["value"] == 42
    assert groups["stack"][1]["value"]["bytes_hex"] == b"story-byte".hex()
    assert groups["globals"][1]["value"] is groups["stack"][1]["value"]


def assert_destination(expected, actual):
    assert actual["frames"] == [row["frame_fields"] for row in expected["scripts"]]
    assert [row["value"] for row in actual["global_values"]] == expected["global_values"]
    assert actual["local_values"][0]["value"] == expected["local_float_bits"]
    assert actual["local_values"][1]["value"] == expected["local_array"]
    assert [row["value"] for row in actual["stack_values"]] == expected["stack_values"]
    assert [row["scope"] for row in actual["global_values"]] == [1, 1]
    assert [row["scope"] for row in actual["local_values"]] == [2, 2]
    assert [row["scope"] for row in actual["stack_values"]] == [3, 3]


def reject(data):
    try:
        inspect_story_layout(data)
    except ValueError:
        return
    raise AssertionError("malformed research story slot accepted")


def main():
    evidence = archive_dir("story_save_layout_tests")
    sources = ("scripts/analysis/original_story_save.py", "scripts/analysis/test_story_save_layout.py",
               "scripts/analysis/test_native_saves.py", "scripts/convert/majiro_native_save.py")
    before = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    samples = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    cases, rejections, native_references = [], 0, []
    for label, cls in (("narci_onetwo", StorySaveOracle), ("narci_himeko", LegacyStoryWriter)):
        sample = next(s for s in samples if s["sample"] == label)
        native_references.append({"sample": label, "exe_sha256": sample["exe_sha256"],
                                  "writer_va": f"{cls.WRITER:08x}",
                                  "capture_and_reader_executed": cls is StorySaveOracle})
        for depth in (0, 1):
            for thumbnail in (False, True):
                oracle = cls(sample)
                expected = oracle.fixture(depth, thumbnail)
                data = oracle.capture_and_write()
                layout = inspect_story_layout(data)
                assert_values(layout)
                assert layout["frame_depth"] == depth
                assert layout["label_bytes_hex"] == b"fixture-label".hex()
                write_ops = list(oracle.operations)
                active_sections = [section for section in layout["sections"] if section["bytes"]]
                assert [(row["file_offset"], row["bytes"]) for row in write_ops] == [
                    (row["offset"], row["bytes"]) for row in active_sections]
                for index, frame in enumerate(layout["frames"]):
                    assert frame["fields"]["80"] == 0x10 + index * 2
                    assert frame["fields"]["88"] == 2
                    for offset, value in (("94", 0x20), ("98", 0x30), ("9c", 0x40),
                                          ("a0", 0x50), ("a4", 0x60)):
                        assert frame["fields"][offset] == value
                destination = None
                second_write = None
                if cls is StorySaveOracle:
                    destination = oracle.load(data)
                    assert_destination(expected, destination)
                    assert oracle.file_cursor == len(data), "original reader did not consume the exact slot"
                    read_ops = list(oracle.operations)
                    rewritten = oracle.capture_and_write()
                    second_layout = inspect_story_layout(rewritten)
                    # Native reload may materialize two pointers from one pool
                    # offset; writer size/offsets can consequently change. Test
                    # logical values and frames, not byte-for-byte equivalence.
                    assert second_layout["frames"] == layout["frames"]
                    assert second_layout["groups"]["globals"][0]["value"] == 0xFFFFFFFE
                    assert second_layout["groups"]["globals"][1]["value"]["bytes_hex"] == b"story-byte".hex()
                    assert second_layout["groups"]["stack"][1]["value"]["bytes_hex"] == b"story-byte".hex()
                    assert second_layout["groups"]["locals"][1]["value"]["values"] == [-2147483648, 7]
                    second_destination = oracle.load(rewritten)
                    assert_destination(expected, second_destination)
                    second_path = evidence / ("20261110_" + label + f"_depth{depth}_thumbnail{int(thumbnail)}_rewritten.sav")
                    second_path.write_bytes(rewritten)
                    second_write = {"slot_file": str(second_path.relative_to(ROOT)),
                                    "bytes": len(rewritten), "sha256": hashlib.sha256(rewritten).hexdigest(),
                                    "object_pool_bytes": second_layout["sections"][-1]["bytes"],
                                    "logical_values_and_frames_restored": True,
                                    "byte_equivalent_to_first_write": rewritten == data}
                tag = f"{label}_depth{depth}_thumbnail{int(thumbnail)}"
                path = evidence / ("20261110_" + tag + ".sav")
                path.write_bytes(data)
                functions = NativeFunctions(oracle)
                addresses = [cls.WRITER]
                if cls is StorySaveOracle:
                    addresses += [cls.CAPTURE, cls.READER, 0x450F70, 0x451030, 0x4511A0, 0x474DC0]
                disassembly = []
                for address in addresses:
                    disassembly.append(f"function {address:08x}")
                    disassembly += [f"{ins.address:08x} {ins.mnemonic:8s} {ins.op_str}"
                                    for ins in functions.body(address, maximum_bytes=12000,
                                                              maximum_instructions=3000)]
                (evidence / ("20261110_" + tag + "_original.txt")).write_text(
                    "\n".join(disassembly) + "\n", encoding="utf8")
                cases.append({"name": tag, "sample": label, "exe_sha256": sample["exe_sha256"],
                              "status": "passed", "signature": layout["signature"],
                              "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                              "slot_file": str(path.relative_to(ROOT)), "layout": layout,
                              "fixture_source": expected, "write_operations": write_ops,
                              "original_reader_result": destination,
                              "read_operations": read_ops if destination is not None else [],
                              "original_second_write_and_read": second_write,
                              "host_boundary_counts": dict(oracle.boundary_counts),
                              "allocator_copy_counts": dict(oracle.boundaries),
                              "real_gameplay_save": False})
                for bad in (data[:-1], data + b"\0", data[:15], b"MajiroSavV9.999\0\0" + data[16:]):
                    reject(bad)
                    rejections += 1
                for delta in (0, 12, 24):
                    mutant = bytearray(data)
                    struct.pack_into("<I", mutant, layout["count_fields_offset"] + delta, 0xFFFFFFFF)
                    reject(bytes(mutant))
                    rejections += 1
                frames_section = next(row for row in layout["sections"] if row["name"] == "frames")
                mutant = bytearray(data)
                mutant[frames_section["offset"]:frames_section["offset"]+128] = b"x" * 128
                reject(bytes(mutant))
                rejections += 1
    after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    assert before == after
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed",
              "source_sha256": before, "source_changed_during_test": False,
              "native_references": native_references, "cases": cases, "rejection_cases": rejections,
              "writer_generated_slots": len(cases) + 4, "original_reader_roundtrips": 8,
              "device_tests_executed": False, "native_game_process_launched": False,
              "runtime_import_integrated": False, "complete_story_restore_verified": False,
              "scope": "original x86 writer-generated synthetic states; onetwo also captures and reads unchanged",
              "substituted_boundaries": [
                  "Bounded guest allocations, memcpy and memset; original serialization instructions execute.",
                  "fopen/fread/fwrite/fclose redirect to an in-memory file, never host game paths.",
                  "Save-write guard is forced permissive for the fixture; CreateDirectoryA is a no-op success.",
                  "Writer's automatic system-save side effect is excluded and counted.",
                  "Onetwo script loader resolves only the fixture's named code records.",
                  "Onetwo post-load resource refresh is excluded; no media or scene restoration is asserted."],
              "limitations": [
                  "All slots are synthetic original-writer output, not real gameplay saves.",
                  "Himeko V1.103 starts from authored captured-state records; no native reader roundtrip.",
                  "Persistent system globals and other threads are not present in these main-context slots.",
                  "Frame header fields without recovered semantics stay numeric; media header remains partly opaque.",
                  "Script-version identities, real code validation and Android VM state import remain unimplemented."],
              "evidence": str(evidence.relative_to(ROOT))}
    write_json(ROOT / "reports/story_save_layout_tests.json", report)
    write_json(evidence / "20261110_story_save_layout_tests.json", report)
    print(f"native story layout: {len(cases) + 4} writer-generated slots, 8 original reader roundtrips, "
          f"{rejections} structural rejection checks passed")


if __name__ == "__main__":
    main()
