"""Compare screen home, cursor mapping and frame pixels with ten original EXEs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys

from PIL import Image

from audit_player_semantics import registered_providers, source_identity
from native_pixels import Pixels
from original_screen import ScreenOracle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
from build_player_core import msvc_environment
from generate_portable_pixels import generate
from port_common import ROOT, archive_dir, player_pixels_library as native_pixels_library, sha256, timestamp, write_json


class Portable:
    def __init__(self, folder):
        generated = folder / "portable_pixels.cpp"
        generate(generated)
        env = msvc_environment()
        compiler = Path(env["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
        self.binary = folder / "portable_pixels_probe.exe"
        command = [str(compiler), "/nologo", "/O2", "/EHsc", "/std:c++20", "/MT", "/utf-8",
                   "/I" + str(ROOT / "player/core"), str(generated),
                   str(ROOT / "scripts/analysis/portable_pixels_probe.cpp"),
                   "/Fo" + str(folder) + os.sep, "/Fe" + str(self.binary)]
        result = subprocess.run(command, cwd=folder, env=env, capture_output=True)
        (folder / (folder.name[:8] + "_compile.txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError("portable pixel test compilation failed: " + str(folder))
        self.generated_sha256 = sha256(generated)
        self.process = subprocess.Popen([str(self.binary)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def __call__(self, request):
        raw = request.encode("utf-8")
        self.process.stdin.write(struct.pack("<I", len(raw)) + raw)
        self.process.stdin.flush()

        def read(length):
            value = self.process.stdout.read(length)
            if len(value) != length:
                raise RuntimeError("portable pixel test pipe closed")
            return value

        length, = struct.unpack("<I", read(4))
        response = read(length).decode("utf-8")
        width, height, size = struct.unpack("<III", read(12))
        pixels = read(size)
        if size != width * height * 4:
            raise ValueError("portable pixel fixture returned invalid frame")
        if not response.startswith("OK"):
            raise ValueError(response)
        return response, pixels

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        errors = self.process.stderr.read()
        self.process.stdout.close(); self.process.stderr.close()
        if self.process.returncode or errors:
            raise RuntimeError("portable pixel fixture failed: " + repr(errors))
        self.binary.unlink()
        for path in self.binary.parent.glob("*.obj"):
            path.unlink()


def run():
    before = source_identity()
    folder = archive_dir("screen_semantics")
    samples = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch = {row["sample"]: row["entries"] for row in
                json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]}
    native = Pixels()
    portable = Portable(folder)
    report = {"schema": 1, "timestamp": timestamp(), "source_identity": before,
              "scope": "real syscall stack results, 1:1 emulated viewport home offsets, cursor conversion, "
                       "and original GDI-boundary frame effects versus desktop and Android pixel providers",
              "limits": ["OS invalidation scheduling and device aspect fit are outside this fixture",
                         "nonzero original window baseline is normalized to the emulated client origin",
                         "Windows GDI FillRect/StretchDIBits execute in a bounded pixel fixture"],
              "samples": [], "failures": [], "complete_engine_semantics": False,
              "native_library_sha256": sha256(native_pixels_library()),
              "portable_generated_sha256": portable.generated_sha256}
    rng = random.Random(171027)
    homes = [(0, 0), (1, 2), (-1, -2), (7, 5), (-7, -5), (8, 6), (-8, -6),
             (9, 7), (-9, -7), (23, -19), (-23, 19),
             (2147483647, -2147483648), (-2147483648, 2147483647)]
    homes += [(rng.randint(-32, 32), rng.randint(-24, 24)) for _ in range(30)]
    try:
        for sample in samples:
            oracle = ScreenOracle(sample, dispatch[sample["sample"]])
            profile = oracle.profile()
            lua, _, _, system = registered_providers(overrides={"screen_controls": profile})
            providers, screen = system["providers"], system["screen"]
            vm, empty = lua.table(), lua.table()
            kind = screen["kind"]()
            calls = {name: {"name": name, "hash": entry["hash"], "cases": 0,
                           "argument_counts": [0] if name in("$curpos_x","$curpos_y") else [2],
                           "discard_modes": [False, True], "failures": 0, "status": "passed"}
                     for name in ("$grp_home", "$curpos_x", "$curpos_y","b84c9953")
                     for entry in dispatch[sample["sample"]] if
                     (entry["hash"] if name=="b84c9953" else entry["name"])==name}
            width, height = 8, 6
            native("reset"); portable("reset")
            native(f"create 1 {width} {height} 0"); portable(f"create 1 {width} {height} 0")
            source = bytearray()
            for at in range(width * height):
                rgb = tuple(rng.randrange(1, 256) for _ in range(3))
                source.extend((*rgb, 255))
                command = f"set 1 {at % width} {at // width} {rgb[0] + rgb[1]*256 + rgb[2]*65536}"
                assert native(command) == "OK"; portable(command)
            previous = None; last_portable = None; frame_cases = 0; cursor_cases = 0
            for index, home in enumerate(homes):
                if index==17:
                    # Loading a slot rebuilds pages while the actual client
                    # retains pixels. This distinction matters in early EXEs.
                    assert native("reset_pages")=="OK"; portable("reset_pages")
                    native(f"create 1 {width} {height} 0"); portable(f"create 1 {width} {height} 0")
                    for at in range(width*height):
                        rgb=source[at*4:at*4+3]
                        command=f"set 1 {at%width} {at//width} {rgb[0]+rgb[1]*256+rgb[2]*65536}"
                        assert native(command)=="OK"; portable(command)
                providers["$grp_home"](vm, lua.table_from(home))
                for discard in (False, True):
                    expected = oracle.call_home(*home, discard=discard)
                    assert expected["home"] == list(home), (sample["sample"], home, expected)
                    assert expected["stack"] == ([] if discard else [(0, 0)])
                    assert any(row["rect"] is None and row["erase"] == 0 for row in expected["invalidations"])
                    calls["$grp_home"]["cases"] += 1
                expected_frame = oracle.paint_frame(source, width, height, previous)
                path = folder / (folder.name[:8] + "_frame.png")
                request = screen["present"](1, str(path), "")
                response = native(request)
                assert response.startswith("OK"), response
                actual = Image.open(path).convert("RGBA").tobytes()
                response, frame = portable(screen["present"](1, "screen", ""))
                if frame: last_portable = frame
                assert actual == expected_frame, (sample["sample"], home, "desktop frame differs")
                assert last_portable == expected_frame, (sample["sample"], home, "portable frame differs")
                previous = expected_frame; frame_cases += 2
                for point in ((0, 0), (7, 5), (-9, 17), (2147483647, -2147483648)):
                    expected = oracle.client_to_game(*point)
                    actual = list(screen["point"](*point))
                    assert actual == expected, (sample["sample"], home, point, actual, expected)
                    system["input"]["sample"](lua.table_from({"x": point[0], "y": point[1]}))
                    oracle.sample_cursor(*point)
                    assert oracle.cursor()==expected
                    assert oracle.cursor(discard=True)==[]
                    for axis, name in enumerate(("$curpos_x", "$curpos_y")):
                        assert providers[name](vm, empty) == expected[axis]
                        calls[name]["cases"]+=2
                    cursor_cases += 1
                for point in ((2, 3), (-9, 11), (-10000, 0)):
                    oracle.sample_cursor(7, 5)
                    system["input"]["sample"](lua.table_from({"x": 7, "y": 5}))
                    for discard in(False,True):
                        expected = oracle.virtual_cursor(*point,discard=discard)
                        result = providers[0xb84c9953](vm, lua.table_from(point))
                        assert expected == ([]if discard else[(0, result)])
                        actual = [providers[name](vm, empty) for name in ("$curpos_x", "$curpos_y")]
                        assert actual == oracle.cursor(), (sample["sample"], home, point, actual, oracle.cursor())
                        calls["b84c9953"]["cases"]+=1
                    cursor_cases += 1
            for at in range(width*height):
                actual=tuple(map(int,native(f"pixel 1 {at%width} {at//width}").split()[1:]))
                assert actual==tuple(source[at*4:at*4+4]),"screen presentation mutated the source page"
            # Test all three conversion profiles with nonunit viewport scale.
            for center, scale in (((13, -7), (1.5, .75)), ((-17, 9), (.333, 2.25))):
                screen["configure"](*center, *scale)
                for home in ((0, 0), (3, -2), (-21, 19)):
                    # Legacy fixed-point builds store the negated client
                    # origin; the portable API uses a viewport origin.
                    native_center = tuple(-value for value in center) if kind == 1 else center
                    oracle.call_home(*home, center=native_center, scale=scale)
                    providers["$grp_home"](vm, lua.table_from(home))
                    for point in ((0, 0), (63, -17), (2147483, -2147483)):
                        expected = oracle.client_to_game(*point)
                        actual = list(screen["point"](*point))
                        assert actual == expected, (sample["sample"], center, scale, home, point, actual, expected)
                        cursor_cases += 1
            report["samples"].append({"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
                                      "calls": list(calls.values()), "profile": profile,
                                      "frame_comparisons": frame_cases, "cursor_comparisons": cursor_cases})
            print(f"{sample['sample']}: {calls['$grp_home']['cases']} dispatch, "
                  f"{frame_cases} frame and {cursor_cases} cursor comparisons passed", flush=True)
    finally:
        portable.close()
    report["comparisons"] = sum(sum(call["cases"] for call in row["calls"]) +
                                 row["frame_comparisons"] + row["cursor_comparisons"]
                                 for row in report["samples"])
    report["mismatches"] = 0
    report["source_changed_during_test"] = source_identity() != before
    report["test_sources"] = {name: sha256(ROOT / "scripts/analysis" / name) for name in
                              ("original_opcode.py", "original_strings.py", "original_calls.py",
                               "original_screen.py", "native_function.py", "native_references.py",
                               "test_screen_semantics.py", "portable_pixels_probe.cpp")}
    report["status"] = "passed" if not report["source_changed_during_test"] else "inconclusive"
    write_json(folder / (folder.name[:8] + "_screen_semantics.json"), report)
    write_json(ROOT / "reports/screen_semantics_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
