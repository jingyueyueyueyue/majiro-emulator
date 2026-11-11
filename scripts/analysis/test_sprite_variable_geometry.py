"""Original absolute animation phases and optional continuous rotation."""
from __future__ import annotations
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256
from audit_player_semantics import registered_providers
from test_new_graphics_abi import FunctionFixture
from recover_happy_wrapper import invoke


def bits(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


def floating(value):
    return struct.unpack("<f", struct.pack("<I", value))[0]


def main():
    samples = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    sample = next(s for s in samples if s["sample"] == "RURINOKASANE")
    rows = []
    for flag in (0, 1, -1):
        now = {"value": 1000}
        f = FunctionFixture(sample)
        sprite = 0x10050000
        f.put(0x5bab28, sprite)
        f.put(sprite + 0x3cc, bits(10))
        f.hook(0x44ec90, lambda sp: f.top)
        f.hook(0x44eab0, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
        f.hook(0x43a770, lambda sp: now["value"])
        f.call(0x4732f0, [sprite, bits(90), 100, flag])
        lua, _, _, system = registered_providers(overrides={"now": lambda: now["value"]})
        state = system["state"]
        state["pages"][800] = lua.table_from({"width": 64, "height": 48, "depth": 32})
        state["pages"][900] = lua.table_from({"width": 64, "height": 48, "depth": 32})
        state["frontbuffer"] = 900
        vm = lua.table();vm["frame"] = lambda self: None
        identifier = system["providers"]["$sprite_create"](vm, lua.table_from([800]))
        portable = state["sprites"][identifier];portable["rotate"] = 10
        assert system["providers"]["$sprite_rotate"](vm, lua.table_from([identifier, 90, 100, flag])) == 0
        expected = []
        for elapsed in (0, 50, 100, 150, 250):
            now["value"] = 1000 + elapsed
            invoke(f, 0x471030, [sprite])
            system["sprites"]["update"]()
            angle = floating(f.read(sprite + 0x3cc))
            assert abs(portable["rotate"] - angle) <= 1e-6
            expected.append(angle)
        rows.append({"name": "$sprite_rotate", "argc": 4, "flag": flag, "status": "passed",
                     "native_angles": expected, "scope": "optional mode selects capped or continuous interpolation"})
    for name, address, absolute, counts in (
        ("$sprite_animate_define", 0x471ae0, False, list(range(1, 36, 2))),
        ("$sprite_animate_define_abs", 0x471c00, True, [5, 7, 9, 11, 13])):
        for argc in counts:
            now = {"value": 1152}
            f = FunctionFixture(sample)
            sprite = 0x10050000
            f.put(0x5bab28, sprite)
            f.hook(0x44ec90, lambda sp: f.top)
            f.hook(0x44eab0, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
            f.hook(0x43a770, lambda sp: now["value"])
            f.hook(0x4710e0, lambda sp: 0)
            args = [sprite]
            for i in range((argc - 1) // 2):
                args += [i, 20 + i]
            f.call(address, args)
            lua, _, _, system = registered_providers(overrides={"now": lambda: now["value"]})
            state = system["state"]
            state["pages"][800] = lua.table_from({"width": 256, "height": 256, "depth": 32})
            state["pages"][900] = lua.table_from({"width": 64, "height": 48, "depth": 32})
            state["frontbuffer"] = 900
            vm = lua.table();vm["frame"] = lambda self: None
            identifier = system["providers"]["$sprite_create"](vm, lua.table_from([800, 0, 0, 8, 8]))
            assert system["providers"][name](vm, lua.table_from([identifier, *args[1:]])) == 0
            animation = state["sprites"][identifier]["animation"]
            count = f.read(sprite + 0x6c)
            assert count == (argc - 1) // 2
            if argc == 1:
                assert animation is None
            else:
                assert len(animation["frames"]) == count
                assert animation["began"] == f.read(sprite + 0x60)
                for i in range(count):
                    assert animation["frames"][i + 1]["frame"] == f.read(sprite + 0x74 + i * 8)
                    assert animation["frames"][i + 1]["duration"] == f.read(sprite + 0x78 + i * 8)
            rows.append({"name": name, "argc": argc, "status": "passed", "native_frames": count,
                         "native_began": f.read(sprite + 0x60),
                         "scope": "observed frame-duration table size and absolute/nonabsolute start phase"})
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "cases": rows,
              "original_exe_sha256": sample["exe_sha256"],
              "runtime_sha256": sha256(ROOT / "player/runtime/majiro_sprites.lua"),
              "scope": "optional continuous rotate parameter and every observed animation arg count through 35",
              "limits": "same ABI across other builds and transformed pixel geometry remain separately scoped evidence"}
    folder = archive_dir("sprite_variable_geometry")
    write_json(folder / "20261110_sprite_variable_geometry.json", report)
    write_json(ROOT / "reports/sprite_variable_geometry_tests.json", report)
    print("variable sprite geometry:", len(rows), "original state/phase cases passed")


if __name__ == "__main__":
    main()
