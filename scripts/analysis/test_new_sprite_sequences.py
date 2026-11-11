"""Recover variable-length native sprite tables from newly observed ABI forms."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import capstone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256
from test_new_graphics_abi import FunctionFixture
from native_function import NativeFunctions
from audit_player_semantics import registered_providers


def main():
    scan = json.loads((ROOT / "reports/new_sample_scan.json").read_text(encoding="utf8"))
    rows = []
    targets = [(name, "$sprite_alfa_define", 13, "anti")
               for name in ("kizoku", "magical_kanon2", "oh_pants", "reconquista")]
    targets += [("idol_revolution", name, argc, field)
                for name, field in (("$sprite_xmodify_define", "xscale"), ("$sprite_ymodify_define", "yscale"))
                for argc in (23, 25)]
    for label, name, argc, field in targets:
        sample = next(s for s in scan["opcode_samples"] if s["sample"] == label)
        entry = next(e for s in scan["syscall_samples"] if s["sample"] == label
                     for e in s["entries"] if e["name"] == name)
        native = FunctionFixture(sample)
        native.machine = native.uc
        functions = NativeFunctions(native)
        root = functions.syscall_helper(entry)
        body = functions.body(root, maximum_bytes=600, maximum_instructions=400)
        calls = [r.operands[0].imm for r in body if r.mnemonic == "call"
                 and r.operands[0].type == capstone.CS_OP_IMM]
        assert len(calls) in (3, 5, 6), (label, name, calls)
        if len(calls) == 3:
            accessor, timer = calls[0], calls[-1]
            result = next(r.operands[0].imm for r in body if r.mnemonic == "jmp"
                          and r.operands[0].type == capstone.CS_OP_IMM
                          and r.operands[0].imm < root)
        else:
            accessor, timer, result = calls[0], calls[-2], calls[-1]
        sprite = 0x10050000
        native.uc.mem_write(sprite, bytes(4096))
        # Old V1 validates with a helper; later builds traverse a linked list.
        if len(calls) == 6:
            native.hook(calls[1], lambda sp: 0)
        else:
            absolute = [r.operands[1].mem.disp for r in body
                        if r.mnemonic == "mov" and len(r.operands) == 2
                        and r.operands[1].type == capstone.CS_OP_MEM
                        and not r.operands[1].mem.base and not r.operands[1].mem.index]
            assert len(set(absolute)) == 1, (label, name, absolute)
            native.put(absolute[0], sprite)
        native.hook(accessor, lambda sp: native.top)
        native.hook(timer, lambda sp: 1000)
        native.hook(result, lambda sp: setattr(native, "result", native.read(sp + 4)) or 0)
        # Native table initializer: lea <reg>,[esi + pair_base + 4].
        table_offsets = [r.operands[1].mem.disp - 4 for r in body
                         if r.mnemonic == "lea" and r.operands[1].type == capstone.CS_OP_MEM
                         and functions.md.reg_name(r.operands[1].mem.base) == "esi"
                         and r.operands[1].mem.disp > 0x100]
        assert len(table_offsets) == 1, (label, name, table_offsets)
        pair_base = table_offsets[0]
        # "from" is copied into pair_base - 8 immediately before activating.
        from_reads = [r.operands[1].mem.disp for r in body
                      if r.mnemonic == "mov" and len(r.operands) == 2
                      and r.operands[1].type == capstone.CS_OP_MEM
                      and functions.md.reg_name(r.operands[1].mem.base) == "esi"
                      and not r.operands[1].mem.index
                      and r.operands[1].mem.disp not in (pair_base - 4, pair_base)]
        assert from_reads, (label, name)
        previous = 77 if field == "anti" else 0x3f800000
        native.put(sprite + from_reads[-1], previous)
        args = [sprite]
        for i in range((argc - 1) // 2):
            args += [(i * 31) % 256 if field == "anti" else 0x3f000000 + i * 0x80000, 10 + i]
        native.call(root, args)
        actual_count = native.read(sprite + pair_base - 4)
        expected_count = (argc - 1) // 2
        assert actual_count == expected_count, (label, name, actual_count)
        points = [(native.read(sprite + pair_base + i * 8), native.read(sprite + pair_base + i * 8 + 4))
                  for i in range(expected_count)]
        assert points == list(zip(args[1::2], args[2::2]))
        assert native.result == 0
        lua, _, _, system = registered_providers()
        state = system["state"]
        state["pages"][800] = lua.table_from({"width": 64, "height": 48, "depth": 32})
        state["pages"][900] = lua.table_from({"width": 64, "height": 48, "depth": 32})
        state["frontbuffer"] = 900
        vm = lua.table()
        vm["frame"] = lambda self: None
        identifier = system["providers"]["$sprite_create"](vm, lua.table_from([800]))
        portable = state["sprites"][identifier]
        portable["anti"] = 77
        import struct
        lua_args = [identifier] + [struct.unpack("<f", struct.pack("<I", v))[0]
                                  if field != "anti" and i % 2 == 1 else v
                                  for i, v in enumerate(args[1:], 1)]
        assert system["providers"][name](vm, lua.table_from(lua_args)) == 0
        sequence = portable["alpha_sequence"] if field == "anti" else portable["transform_sequences"][field]
        recovered_points = list(sequence["points"].values())
        assert len(recovered_points) == expected_count
        for i, point in enumerate(recovered_points):
            assert point["value"] == lua_args[1 + 2 * i]
            assert point["duration"] == lua_args[2 + 2 * i]
        rows.append({"sample": label, "name": name, "argc": argc, "points": expected_count,
                     "native_table_offset": pair_base, "original_exe_sha256": sample["exe_sha256"],
                     "status": "passed"})
        print(label, name, argc, expected_count, "native point pairs passed", flush=True)
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "cases": rows,
              "scope": "variable-length wrapper point counts/value-duration pairs and return; interpolation is independently validated",
              "isolated_boundaries": ["VM argument/result stack", "valid sprite lookup", "clock"],
              "runtime_sha256": sha256(ROOT / "player/runtime/majiro_sprites.lua")}
    folder = archive_dir("new_sprite_sequences")
    write_json(folder / "20261110_new_sprite_sequences.json", report)
    write_json(ROOT / "reports/new_sprite_sequence_tests.json", report)


if __name__ == "__main__":
    main()
