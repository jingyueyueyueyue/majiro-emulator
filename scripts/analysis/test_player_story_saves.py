"""Compare shared C++ native story parsing against independent Python fields."""
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
from majiro_story_save import parse_story_save
from port_common import ROOT, archive_dir, timestamp, write_json
from test_native_saves import synthetic_system

SOURCES = ("player/core/majiro_story_save.hpp", "player/core/majiro_story_save.cpp",
           "player/core/majiro_save.hpp", "player/core/majiro_save.cpp",
           "scripts/convert/majiro_story_save.py", "scripts/convert/majiro_native_save.py",
           "scripts/analysis/player_story_save_probe.cpp",
           "scripts/analysis/test_player_story_saves.py")


def identity():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def fixtures():
    report = json.loads((ROOT / "reports/story_save_layout_tests.json").read_text(encoding="utf8"))
    for case in report["cases"]:
        yield case["name"], ROOT / case["slot_file"], case["sha256"]
        rewritten = case.get("original_second_write_and_read")
        if rewritten:
            yield case["name"] + "_rewritten", ROOT / rewritten["slot_file"], rewritten["sha256"]


def malformed(raw):
    fields = 0x90 if raw[:16] == b"MajiroSavV1.103\0" else 0x210
    header = 0xA3E0 if fields == 0x90 else 0xA560
    parsed = parse_story_save(raw)
    thumb_bytes = len(bytes.fromhex(parsed["thumbnail"]["bytes_hex"]))
    frame_start = header + thumb_bytes
    frames = len(parsed["frames"])
    value_start = frame_start + frames * 168
    count = sum(group["common_value_view"]["variable_count"] for group in parsed["groups"])
    pool_start = value_start + count * 16
    result = [("empty", b""), ("header_only", raw[:header-1]),
              ("trailing", raw+b"\0"), ("truncated", raw[:-1]),
              ("unsupported_signature", b"MajiroSavV9.999\0\0"+raw[16:]),
              ("system_signature", b"MajiroSysV1.100\0"+raw[16:])]

    def changed(name, offset, value):
        data = bytearray(raw)
        struct.pack_into("<I", data, offset, value)
        result.append((name, bytes(data)))

    changed("depth_overflow", fields, 0xFFFFFFFF)
    data = bytearray(raw)
    struct.pack_into("<2I", data, fields+4, 0xFFFFFFFF, 1)
    result.append(("width_overflow", bytes(data)))
    data = bytearray(raw)
    struct.pack_into("<2I", data, fields+4, 1, 0xFFFFFFFF)
    result.append(("height_overflow", bytes(data)))
    for i in range(3):
        changed(f"group_{i}_count_overflow", fields+12+i*4, 0xFFFFFFFF)
    changed("pool_length_overflow", fields+24, 0xFFFFFFFF)
    changed("unknown_value_tag", value_start+4, 6)
    changed("object_offset_outside_pool", value_start+16+8, 0xFFFFFFFF)
    changed("object_offset_mismatch", pool_start+4, 16)
    changed("string_length_overflow", pool_start+8, 0xFFFFFFFF)
    data = bytearray(raw)
    data[16:fields] = b"x"*(fields-16)
    result.append(("unterminated_label", bytes(data)))
    data = bytearray(raw)
    data[frame_start:frame_start+128] = b"x"*128
    result.append(("unterminated_frame_name", bytes(data)))
    data = bytearray(raw)
    data[frame_start] = 0
    result.append(("empty_frame_name", bytes(data)))
    swapped = b"MajiroSavV1.105\0" if fields == 0x90 else b"MajiroSavV1.103\0"
    result.append(("cross_version_header_mismatch", swapped+raw[16:]))
    return result


def common_type_fixture(story):
    parsed = parse_story_save(story)
    header_size = len(bytes.fromhex(parsed["header_bytes_hex"]))
    fields = parsed["count_fields_offset"]
    thumbnail = bytes.fromhex(parsed["thumbnail"]["bytes_hex"])
    frame_data = b"".join(bytes.fromhex(row["serialized_hex"]) for row in parsed["frames"])
    native = synthetic_system()
    count, pool_bytes = struct.unpack_from("<II", native, 16)
    header = bytearray(story[:header_size])
    struct.pack_into("<4I", header, fields+12, count, 0, 0, pool_bytes)
    return bytes(header) + thumbnail + frame_data + native[0x9C58:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-compile", action="store_true")
    args = parser.parse_args()
    evidence = archive_dir("player_story_save_tests")
    before = identity()
    env = msvc_environment()
    compiler = Path(env["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    executable = evidence / "player_story_save_probe.exe"
    command = [str(compiler), "/nologo", "/O2", "/EHsc", "/std:c++20", "/MT", "/utf-8", "/W4", "/WX",
               str(ROOT / "player/core/majiro_save.cpp"), str(ROOT / "player/core/majiro_story_save.cpp"),
               str(ROOT / "scripts/analysis/player_story_save_probe.cpp"),
               "/Fo"+str(evidence)+os.sep, "/Fe"+str(executable)]
    process = subprocess.run(command, cwd=evidence, env=env, capture_output=True)
    (evidence / "20261110_compile.txt").write_bytes(process.stdout+process.stderr)
    if process.returncode:
        raise RuntimeError("native story reader compilation failed: "+str(evidence))
    limit = subprocess.run([str(executable), "--limits"], capture_output=True, check=True)
    assert json.loads(limit.stdout)["oversized_input_rejected_before_read"]
    cases, probes, negatives = [], 1, []
    originals = list(fixtures())
    assert len(originals) == 12, "native writer evidence must contain all 12 generated slots"

    def compare(name, path, expected_hash=None, provenance="original_writer_fixture"):
        nonlocal probes
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if expected_hash:
            assert sha == expected_hash, "original writer fixture changed: "+name
        reference = parse_story_save(raw)
        for mode in ("memory", "readat"):
            process = subprocess.run([str(executable), mode, str(path)], capture_output=True)
            if process.returncode:
                raise RuntimeError(name+": "+process.stderr.decode("utf8", errors="replace"))
            assert json.loads(process.stdout) == reference, (name, mode)
            probes += 1
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
        cases.append({"name": name, "file": str(path.relative_to(ROOT)), "sha256": sha,
                      "format": reference["format"], "bytes": len(raw), "status": "passed",
                      "provenance": provenance, "frame_count": len(reference["frames"]),
                      "value_counts": [group["common_value_view"]["variable_count"]
                                       for group in reference["groups"]]})
        return raw

    first_of_version = {}
    for name, path, sha in originals:
        raw = compare(name, path, sha)
        first_of_version.setdefault(raw[:16], raw)
    for raw in first_of_version.values():
        fmt = raw[:15].decode("ascii")
        all_types = common_type_fixture(raw)
        path = evidence / ("20261110_"+fmt+"_all_types.sav")
        path.write_bytes(all_types)
        compare(fmt+"_common_value_types", path, provenance="independent_all_type_fixture")
        for name, data in malformed(raw):
            key = fmt+"_"+name
            path = evidence / ("20261110_reject_"+key+".sav")
            path.write_bytes(data)
            try:
                parse_story_save(data)
            except ValueError:
                pass
            else:
                raise AssertionError("Python accepted malformed story fixture: "+key)
            for mode in ("memory", "readat"):
                process = subprocess.run([str(executable), mode, str(path)], capture_output=True)
                assert process.returncode == 1 and process.stderr, (key, mode)
                probes += 1
            negatives.append(key)
    android = "not_requested"
    if args.android_compile:
        toolchain = json.loads((ROOT / "reports/android_toolchain.json").read_text(encoding="utf8"))
        clang = Path(toolchain["sdk"]) / "ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"
        command = [str(clang), "--target=aarch64-linux-android26", "-std=c++20", "-O2", "-fPIC",
                   "-Wall", "-Wextra", "-Werror", "-c", str(ROOT / "player/core/majiro_story_save.cpp"),
                   "-o", str(evidence / "20261110_majiro_story_save_android.o")]
        process = subprocess.run(command, cwd=evidence, capture_output=True)
        (evidence / "20261110_android_compile.txt").write_bytes(process.stdout+process.stderr)
        if process.returncode:
            raise RuntimeError("native Android story parser compilation failed: "+str(evidence))
        android = "arm64_api26_object_cross_compiled"
    assert before == identity(), "shared story parser changed during tests"
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "source_sha256": before,
              "source_changed_during_test": False, "original_writer_manifest_sha256": hashlib.sha256(
                  (ROOT / "reports/story_save_layout_tests.json").read_bytes()).hexdigest(),
              "cases": cases, "negative_cases": negatives, "probe_checks": probes,
              "android_compile": android, "evidence": str(evidence.relative_to(ROOT)),
              "native_story_restore_supported": False, "runtime_import_integrated": False,
              "script_identity_verified": False, "device_tests_executed": False,
              "scope": "V1.103/V1.105 raw header/frame/thumbnail/value/pool parity, aliases and requirements",
              "limitations": ["No native gameplay saves; 12 inputs come from bounded original x86 writers.",
                              "Unknown resource header bytes remain opaque.",
                              "Script requirements are unverified inputs to future identity/boundary validation.",
                              "No Android VM importer or partial restore API exists."]}
    write_json(ROOT / "reports/player_story_save_tests.json", report)
    write_json(evidence / "20261110_player_story_save_tests.json", report)
    print(f"portable story parser: {len(cases)} inputs, {len(negatives)} rejection cases, "
          f"{probes} probe checks passed")


if __name__ == "__main__":
    main()
