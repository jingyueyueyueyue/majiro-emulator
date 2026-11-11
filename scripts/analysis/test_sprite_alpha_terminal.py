"""Original alpha terminals, shared runtimes, and an opaque-overlay negative control."""
from pathlib import Path
import argparse
import json
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts" / n) for n in ("analysis", "convert", "build")]
from original_x86 import Original
from test_new_graphics_abi import FunctionFixture
from lua_bridge import create_lua
from port_common import archive_dir, read_json, write_json, timestamp, sha256
from native_pixels import Pixels
from PIL import Image
from unicorn import x86_const as reg

CASES = (
    ("source_black_overlay", [0, 0, 255, -100001, 255, -1]),
    ("first_terminal", [128, -1]),
    ("other_negative_terminal", [0, 0, 192, -2]),
    ("terminal_ignores_tail", [0, 0, 128, -1, 255, 0]),
    ("different_timed_terminal", [0, 0, 100, 250, 240, -1]),
    ("existing_positive_fade", [255, 0, 0, 250, 0, -1]),
)
TIMES = (0, 1, 50, 125, 249, 250, 251, 1000)
OLD = "if point.duration<0 then s.anti=from;complete=false;break end"
FIXED = "if point.duration<0 then s.anti=point.value;break end"
MAGICAL_HASH = "139d1559bd83da7fa54cc8c4d30ccd45401bfba5e542c4762a5cf0e1e3b539a0"


def native_values(sample, points):
    if sample == "ruri":
        native = Original()
        pointer = native.memory(bytes(0x630))
        for address, value in ((pointer + 0x2c, 77), (pointer + 0x38, 1),
                               (0x5bab28, pointer), (0x4dc36c, 1000)):
            native.machine.mem_write(address, struct.pack("<I", value))
        native.syscall(0x4715f0, [pointer, *points])
        rows = []
        for elapsed in TIMES:
            native.machine.mem_write(0x4dc36c, struct.pack("<I", 1000 + elapsed))
            native.call(0x4701c0, [pointer])
            rows.append({"anti": struct.unpack("<I", native.read(pointer + 0x2c, 4))[0],
                         "active": struct.unpack("<I", native.read(pointer + 0x27c, 4))[0]})
        return rows, Original.IDENTITY
    scan = read_json(ROOT / "reports/new_sample_scan.json")
    declaration = next(s for s in scan["opcode_samples"] if s["sample"] == "magical_kanon2")
    assert declaration["exe_sha256"] == MAGICAL_HASH, "affected original engine identity changed"
    native = FunctionFixture(declaration)
    pointer = 0x10050000
    native.uc.mem_write(pointer, bytes(4096))
    native.put(0x5d0060, pointer)
    native.put(pointer + 0x2c, 77)
    native.put(pointer + 0x38, 1)
    native.hook(0x463820, lambda sp: native.top)
    native.hook(0x463640, lambda sp: 0)
    clock = [1000]
    native.hook(0x44f550, lambda sp: clock[0])
    native.call(0x485900, [pointer, *points])
    rows = []
    for elapsed in TIMES:
        clock[0] = 1000 + elapsed
        stack = 0x2000f000
        native.put(stack, 0x30000000)
        native.put(stack + 4, pointer)
        native.uc.reg_write(reg.UC_X86_REG_ESP, stack)
        native.uc.reg_write(reg.UC_X86_REG_EFLAGS, 2)
        native.uc.emu_start(0x4844e0, 0x30000000, count=100000)
        assert native.uc.reg_read(reg.UC_X86_REG_EIP) == 0x30000000, "original updater exceeded bound"
        rows.append({"anti": native.read(pointer + 0x2c),
                     "active": native.read(pointer + 0x27c)})
    return rows, MAGICAL_HASH


def runtime_values(path, points, candidate=False, negative=False):
    source = path.read_text(encoding="utf8")
    if candidate:
        assert source.count(OLD) == 1, "candidate predecessor changed"
        source = source.replace(OLD, FIXED)
    if negative:
        assert source.count(FIXED) == 1, "negative-control patch point changed"
        source = source.replace(FIXED, OLD)
    lua = create_lua()
    module = lua.execute(source)
    state, providers = lua.table(), lua.table()
    clock = [1000]
    define = lua.eval("function(p)return function(n,f)p[n]=f end end")(providers)
    api = module["install"](define, state,
                            lua.table_from({"width": 8, "height": 8, "now": lambda: clock[0]}),
                            lambda *args: "OK", providers)
    state["pages"] = lua.table_from({1: {"width": 8, "height": 8},
                                    2: {"width": 8, "height": 8}}, recursive=True)
    state["frontbuffer"] = 2
    vm = lua.table()
    vm["frame"] = lambda self: None
    handle = providers["$sprite_create"](vm, lua.table_from([1]))
    sprite = state["sprites"][handle]
    sprite["anti"] = 77
    providers["$sprite_alfa_define"](vm, lua.table_from([handle, *points]))
    values = []
    for elapsed in TIMES:
        clock[0] = 1000 + elapsed
        api["update"]()
        values.append({"anti": sprite["anti"], "active": sprite["alpha_sequence"] is not None})
    return values


def overlay_pixels(folder, label, anti):
    pixels = Pixels()
    for command in ("create 1 8 8 0", "fill 1 0 0 8 8 65280",
                    "create 2 8 8 0", "fill 2 0 0 8 8 0"):
        assert pixels(command) == "OK", command
    target = folder / (label + ".png")
    assert pixels(f"render 1 {target}\n2 0 0 8 8 0 0 1 1 0 {anti} 0") == "OK"
    with Image.open(target) as image:
        return image.convert("RGB").getpixel((4, 4))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="store_true",
                        help="explicit in-memory candidate; never report as a production regression")
    args = parser.parse_args()
    folder = archive_dir("sprite_alpha_terminal")
    rows, original_rows = [], []
    for label, points in CASES:
        ruri, ruri_hash = native_values("ruri", points)
        magical, magical_hash = native_values("magical_kanon2", points)
        assert ruri == magical, (label, "original versions disagree", ruri, magical)
        original_rows.append({"case": label, "points": points, "samples": magical,
                              "exe_sha256": [ruri_hash, magical_hash]})
        for relative in ("player/runtime/majiro_sprites.lua", "runtime/majiro_sprites.lua"):
            path = ROOT / relative
            actual = runtime_values(path, points, candidate=args.candidate)
            for elapsed, expected, got in zip(TIMES, magical, actual):
                assert got["anti"] == expected["anti"], (relative, label, elapsed, got, expected)
                assert bool(got["active"]) == bool(expected["active"]), (
                    relative, label, elapsed, "terminal did not release", got, expected)
            rows.append({"case": label, "runtime": relative, "status": "passed",
                         "samples": actual, "runtime_sha256": sha256(path)})
    negative_controls = []
    for relative in ("player/runtime/majiro_sprites.lua", "runtime/majiro_sprites.lua"):
        bad = runtime_values(ROOT / relative, CASES[0][1],
                             candidate=args.candidate, negative=True)
        good = runtime_values(ROOT / relative, CASES[0][1], candidate=args.candidate)
        assert bad[0]["anti"] == 0 and good[0]["anti"] == 255, "old overlay mismatch did not reproduce"
        old_pixel = overlay_pixels(folder, relative.replace("/", "_") + "_old", bad[0]["anti"])
        fixed_pixel = overlay_pixels(folder, relative.replace("/", "_") + "_fixed", good[0]["anti"])
        assert old_pixel == (0, 0, 0), "old negative-control overlay was not black"
        assert fixed_pixel == (0, 255, 0), "terminal did not expose the underlying native pixels"
        negative_controls.append({"runtime": relative, "old_pixel": old_pixel,
                                  "fixed_pixel": fixed_pixel, "old_error_rejected": True})
    report = {"status": "passed", "timestamp": timestamp(),
              "candidate_only": args.candidate, "production_integrated": not args.candidate,
              "original_samples": len(CASES) * len(TIMES) * 2,
              "runtime_comparisons": len(rows) * len(TIMES),
              "original": original_rows, "runtime": rows,
              "negative_controls": negative_controls, "device_accessed": False,
              "scope": "two actual original alpha initializers/updaters and both runtime copies; synthetic native pixels, no actual game session",
              "evidence": str(folder.relative_to(ROOT))}
    write_json(folder / "report.json", report)
    write_json(ROOT / ("reports/sprite_alpha_terminal_candidate.json" if args.candidate
                       else "reports/sprite_alpha_terminal.json"), report)
    print(json.dumps({key: report[key] for key in (
        "status", "candidate_only", "original_samples", "runtime_comparisons", "evidence")}))


if __name__ == "__main__":
    main()
