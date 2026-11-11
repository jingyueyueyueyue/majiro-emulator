"""Compile the shared C++ save reader and compare every field with Python."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
from build_player_core import msvc_environment
from majiro_native_save import (STORY_SIGNATURES, SYSTEM_HEADER, SYSTEM_SIGNATURE,
                                parse_readmarks, parse_system)
from port_common import ROOT, archive_dir, timestamp, write_json
from test_native_saves import synthetic_system


SOURCES = ("player/core/majiro_save.hpp", "player/core/majiro_save.cpp",
           "scripts/analysis/player_native_save_probe.cpp",
           "scripts/analysis/test_player_native_saves.py",
           "scripts/convert/majiro_native_save.py")


def source_identity():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def normalize(parsed):
    parsed = dict(parsed)
    for name in ("source_sha256", "scope"):
        parsed.pop(name, None)
    if "records" in parsed:
        parsed["records"] = [{k: v for k, v in record.items() if k != "script"}
                             for record in parsed["records"]]
    return parsed


def readmark(name=b"SCENE.MJO", count=16, bits=None):
    bits = bits if bits is not None else bytes([0x81, 0x40, 1]) + bytes(15)
    header = name + bytes(128 - len(name))
    header += struct.pack("<5I", len(bits), count, 0xDEADBEEF, 0xBAD0C0DE, 0)
    return header + bits


def rejected_fixtures():
    system = synthetic_system()
    rows = []

    def changed(name, offset, value):
        raw = bytearray(system)
        struct.pack_into("<I", raw, offset, value)
        rows.append((name, "system", bytes(raw)))

    rows += [("system_truncated", "system", system[:-1]),
             ("system_trailing", "system", system + b"\0"),
             ("system_header_only", "system", SYSTEM_SIGNATURE),
             ("system_wrong_signature", "system", b"MajiroSysV9.999\0" + system[16:])]
    changed("variable_count_overflow", 16, 0xFFFFFFFF)
    changed("pool_size_overflow", 20, 0xFFFFFFFF)
    changed("unknown_type", SYSTEM_HEADER + 4, 6)
    changed("duplicate_hash", SYSTEM_HEADER + 16, 1)
    changed("invalid_pool_offset", SYSTEM_HEADER + 3 * 16 + 8, 0xFFFFFFFF)
    changed("alias_type_conflict", SYSTEM_HEADER + 4 * 16 + 4, 3)
    changed("picture_hash_after_terminator", 28, 1)
    count = struct.unpack_from("<I", system, 16)[0]
    start = SYSTEM_HEADER + count * 16
    changed("object_offset_mismatch", start + 4, 8)
    changed("string_length_overflow", start + 8, 0xFFFFFFFF)
    bad_string = bytearray(system)
    bad_string[start + 12 + 5] = 7
    rows.append(("string_missing_terminator", "system", bytes(bad_string)))
    # Array location comes from the independently built synthetic table.
    array_offset = struct.unpack_from("<I", system, SYSTEM_HEADER + 5 * 16 + 8)[0]
    changed("array_dimension_overflow", start + array_offset + 8, 0xFFFFFFFF)
    changed("array_product_overflow", start + array_offset + 8, 1000000)
    pool = bytearray(struct.pack("<6I", 1, 0, 6, 1, 1, 0) + bytes(24))
    struct.pack_into("<6I", pool, 24, 1, 24, 0, 1, 1, 0)
    header = SYSTEM_SIGNATURE + struct.pack("<II", 2, len(pool))
    header += bytes(SYSTEM_HEADER - len(header))
    rows.append(("overlapping_objects", "system",
                 header + struct.pack("<8I", 1, 3, 0, 0, 2, 3, 24, 0) + pool))
    for signature in STORY_SIGNATURES:
        rows.append((signature[:15].decode("ascii"), "system", signature + bytes(256)))
    marks = readmark()
    rows += [("readmark_truncated", "readmarks", marks[:-1]),
             ("readmark_trailing", "readmarks", marks + b"\0"),
             ("readmark_unterminated_name", "readmarks", b"x"*128 + marks[128:]),
             ("readmark_empty_name", "readmarks", bytes(128) + marks[128:]),
             ("readmark_path_name", "readmarks", readmark(b"../SCENE.MJO")),
             ("readmark_wrong_extension", "readmarks", readmark(b"SCENE.EXE")),
             ("readmark_invalid_padding", "readmarks", marks[:20] + b"x" + marks[21:]),
             ("readmark_duplicate_name", "readmarks", marks + readmark(b"scene.mjo")),
             ("readmark_invalid_cp932", "readmarks", readmark(b"\x81.MJO"))]
    invalid_size = bytearray(marks)
    struct.pack_into("<I", invalid_size, 128, 0xFFFFFFFF)
    rows.append(("readmark_size_overflow", "readmarks", bytes(invalid_size)))
    invalid_count = bytearray(marks)
    struct.pack_into("<I", invalid_count, 132, 0xFFFFFFFF)
    rows.append(("readmark_count_overflow", "readmarks", bytes(invalid_count)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-compile", action="store_true")
    args = parser.parse_args()
    evidence = archive_dir("player_native_save_tests")
    before = source_identity()
    env = msvc_environment()
    compiler = Path(env["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    executable = evidence / "player_native_save_probe.exe"
    command = [str(compiler), "/nologo", "/O2", "/EHsc", "/std:c++20", "/MT", "/utf-8",
               "/W4", "/WX", "/I" + str(ROOT / "player/core"),
               str(ROOT / "player/core/majiro_save.cpp"),
               str(ROOT / "scripts/analysis/player_native_save_probe.cpp"),
               "/Fo" + str(evidence) + os.sep, "/Fe" + str(executable)]
    result = subprocess.run(command, cwd=evidence, env=env, capture_output=True)
    (evidence / "20261110_compile.txt").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("shared save parser compile failed: " + str(evidence / "20261110_compile.txt"))
    limits = subprocess.run([str(executable), "--limits"], capture_output=True, check=True)
    assert json.loads(limits.stdout)["oversized_input_rejected_before_read"]
    cases, rejections, checks = [], [], 0
    inventory = json.loads((ROOT / "reports/majiro_samples.json").read_text(encoding="utf8"))
    for game in inventory["games"]:
        for save in game.get("save_signatures", []):
            path = Path(game["directory"]) / save["path"]
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            kind = "readmarks" if "readmark" in save["path"] else "system"
            python = (parse_readmarks if kind == "readmarks" else parse_system)(raw)
            for mode in ("memory", "readat"):
                process = subprocess.run([str(executable), kind, mode, str(path)], capture_output=True)
                if process.returncode:
                    raise RuntimeError(process.stderr.decode("utf8", errors="replace"))
                assert json.loads(process.stdout) == normalize(python), (path, kind, mode)
                checks += 1
            assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
            cases.append({"sample": Path(game["directory"]).name, "file": save["path"],
                          "kind": kind, "sha256": sha, "bytes": len(raw), "status": "passed"})
    positive = [("all_value_types_empty_arrays_aliases", "system", synthetic_system()),
                ("readmark_line_bit_boundaries", "readmarks", readmark()),
                ("readmark_cp932_trail_backslash", "readmarks", readmark(b"\x83\x5c.MJO")),
                ("readmark_empty", "readmarks", b""),
                ("readmark_multiple", "readmarks", readmark() + readmark(b"NEXT.MJO"))]
    null_string = bytearray(synthetic_system())
    variables = struct.unpack_from("<I", null_string, 16)[0]
    object_start = SYSTEM_HEADER + variables * 16
    string_array_offset = struct.unpack_from("<I", null_string, SYSTEM_HEADER + 7 * 16 + 8)[0]
    struct.pack_into("<I", null_string, object_start + string_array_offset + 24, 0xffffffff)
    positive.append(("native_null_string_reference", "system", bytes(null_string)))
    for name, kind, raw in positive:
        path = evidence / ("20261110_" + name + ".mss")
        path.write_bytes(raw)
        parsed = (parse_readmarks if kind == "readmarks" else parse_system)(raw)
        for mode in ("memory", "readat"):
            process = subprocess.run([str(executable), kind, mode, str(path)], capture_output=True)
            if process.returncode:
                raise RuntimeError(name + ": " + process.stderr.decode("utf8", errors="replace"))
            assert json.loads(process.stdout) == normalize(parsed), (name, mode)
            checks += 1
    for name, kind, raw in rejected_fixtures():
        path = evidence / ("20261110_reject_" + name + ".mss")
        path.write_bytes(raw)
        fn = parse_readmarks if kind == "readmarks" else parse_system
        try:
            fn(raw)
        except ValueError:
            pass
        else:
            raise AssertionError("Python reference accepted malformed fixture: " + name)
        for mode in ("memory", "readat"):
            process = subprocess.run([str(executable), kind, mode, str(path)], capture_output=True)
            assert process.returncode == 1 and process.stderr, (name, mode)
            checks += 1
        rejections.append(name)
    # A small valid file can alias a large string many times. JSON inspection
    # must stop before expanding those aliases into an unbounded text result.
    large_string = b"x" * 80000
    pool = struct.pack("<3I", 1, 0, len(large_string) + 1) + large_string + b"\0"
    aliases = 1024
    header = SYSTEM_SIGNATURE + struct.pack("<II", aliases, len(pool))
    header += bytes(SYSTEM_HEADER - len(header))
    raw = header + b"".join(struct.pack("<4I", i, 2, 0, 0) for i in range(aliases)) + pool
    alias_path = evidence / "20261110_json_expansion_bound.mss"
    alias_path.write_bytes(raw)
    assert len(parse_system(raw)["variables"]) == aliases
    for mode in ("memory", "readat"):
        process = subprocess.run([str(executable), "system", mode, str(alias_path)], capture_output=True)
        assert process.returncode == 1 and b"JSON exceeds size bound" in process.stderr
        checks += 1
    android = "not_requested"
    if args.android_compile:
        toolchain = json.loads((ROOT / "reports/android_toolchain.json").read_text(encoding="utf8"))
        clang = Path(toolchain["sdk"]) / "ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"
        output = evidence / "20261110_majiro_save_android.o"
        command = [str(clang), "--target=aarch64-linux-android26", "-std=c++20", "-O2", "-fPIC",
                   "-Wall", "-Wextra", "-Werror", "-c", str(ROOT / "player/core/majiro_save.cpp"),
                   "-o", str(output)]
        result = subprocess.run(command, cwd=evidence, capture_output=True)
        (evidence / "20261110_android_compile.txt").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError("Android save parser compile failed: " + str(evidence))
        android = "arm64_api26_object_cross_compiled"
    after = source_identity()
    assert before == after, "native save parser changed during checks"
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed",
              "source_sha256": before, "source_changed_during_test": False,
              "real_files": cases, "synthetic_positive_cases": [x[0] for x in positive],
              "rejection_cases": rejections, "probe_checks": checks + 1,
              "platform": "Windows compilation and format-only probe",
              "target_core": "shared portable C++20 for Android",
              "android_compile": android, "json_expansion_limit_bytes": 128 * 1024 * 1024,
              "device_tests_executed": False, "runtime_import_integrated": False,
              "native_story_restore_supported": False,
              "evidence": str(evidence.relative_to(ROOT)),
              "scope": "field-by-field Python/C++ parity, byte and float preservation, aliases, memory and ReadAt",
              "limitations": ["No script identity mapping or gameplay restore.",
                              "C++ preserves raw CP932 names without Unicode conversion.",
                              "Python original-code evidence uses onetwo only, not all EXE builds."]}
    write_json(ROOT / "reports/player_native_save_tests.json", report)
    write_json(evidence / "20261110_player_native_save_tests.json", report)
    print(f"portable native saves: {len(cases)} real files, {len(positive)} synthetic positives, "
          f"{len(rejections)} rejection cases, {checks + 1} probe checks passed")


if __name__ == "__main__":
    main()
