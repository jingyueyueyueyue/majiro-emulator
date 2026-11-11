"""Differential numeric stores through actual local and array access machinery."""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import json
import math
from pathlib import Path
import struct
import sys

from lua_bridge import create_lua
from original_opcode import OpcodeOracle, f32_bits, native_number

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def bits(value, tag):
    return f32_bits(value) if tag == 1 else int(value) & 0xFFFFFFFF


def equal(raw, value):
    tag, expected = raw
    if tag == 1 and math.isnan(native_number(tag, expected)):
        return math.isnan(value)
    return bits(value, tag) == expected


def fixtures(name):
    real = name.endswith(".r")
    plain = name.split(".", 1)[1] in ("i", "r")
    integers = [(0, value & 0xFFFFFFFF) for value in
                (-2147483648, -65537, -33, -1, 0, 1, 2, 31, 32, 2147483647)]
    reals = [(1, f32_bits(value)) for value in
             (-100.25, -1.75, -0.0, 0.0, 1.75, 100.25)]
    values = reals if real else integers
    for left, right in itertools.product(values, repeat=2):
        yield left, right
    if plain:
        for right in integers if real else [
                *reals, (1, 0x7F7FFFFF), (1, 0x7F800000), (1, 0x7FC00000)]:
            yield values[0], right


def run():
    dispatch = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))
    profiles = json.loads((ROOT / "config/player_engine_profiles.json").read_text(encoding="utf-8"))
    lua = create_lua()
    module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    opcodes = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf-8"))
    execute = lua.eval("""
    function(mod,ops,opcode,flags,left,right,index,profile,array,operand_type)
      local script={name='storage-fixture',main=0,functions={},
        code={{0,opcode,flags,123,0},{10,0x800,0},{16,0x82b}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=function()error('storage fixture invoked syscall')end,numeric_profile=profile}
      vm:begin(script.name)
      local target=array and{kind='array',dims={3},element_type=math.floor(flags/256)%8,
        values={[0]=left,[1]=left,[2]=left}}or left
      vm:frame().locals[0]=target
      vm:push(right,operand_type)
      if array then vm:push(index)end
      local ok,why=pcall(function()vm:step()end)
      local value=array and target.values[index%3]or vm:frame().locals[0]
      local signs={}
      for i,v in ipairs(vm.stack)do signs[i]=v==0 and 1/v==-math.huge end
      return ok,why,value,vm.stack,value==0 and 1/value==-math.huge,signs
    end
    """)
    report = {"schema": 1, "timestamp": timestamp(), "samples": [], "failures": [],
              "scope": "typed numeric operands, local/one-dimensional array stores, result, stack depth",
              "excluded": "malformed bytecode mixing real operand bits into integer compound operations",
              "vm_sha256": hashlib.sha256((ROOT / "player/runtime/majiro_vm.lua").read_bytes()).hexdigest(),
              "host_calls_replaced": False, "complete_engine_semantics": False}
    for sample in dispatch["samples"]:
        oracle = OpcodeOracle(sample)
        profile = profiles["executables"][sample["exe_sha256"]]["numeric"]
        counts, failures, matched_faults = Counter(), Counter(), Counter()
        for name, row in sorted(oracle.rows.items()):
            if not name.startswith(("st.", "stp.", "stelem.", "stelemp.")):
                continue
            if name.endswith(("arr", ".s")):
                continue
            array = name.startswith("stelem")
            tag = 1 if name.endswith(".r") else 0
            flags = 96 + tag * 256 + (2048 if array else 0)
            for initial, operand in fixtures(name):
                for index in (-7, -1, 0, 2, 3, 7) if array else (0,):
                    left, right = native_number(*initial), native_number(*operand)
                    values = [initial, operand]
                    ptr = oracle.DATA + 0x30000
                    if array:
                        oracle.machine.mem_write(ptr, struct.pack("<8I", 64, 0, 1, 1, 3,
                                                                  *([initial[1]] * 3)))
                        values = [(tag + 3, ptr), operand, (0, index & 0xFFFFFFFF)]
                    counts[name] += 1
                    expected, original_error = None, None
                    try:
                        expected = oracle.execute(name, values, struct.pack("<HIh", flags, 123, 0),
                                                  budget=20000)
                    except Exception as error:
                        original_error = str(error)
                    ok, error, actual_local, actual_stack, sign, signs = execute(
                        module, opcodes, int(row["opcode"], 16), flags,
                        left, right, index, profile, array, operand[0])
                    reason = None
                    if original_error:
                        if (name.endswith((".div.i", ".rem")) and left == -2147483648 and right == -1
                                and "UC_ERR_EXCEPTION" in original_error and not ok
                                and "signed division overflow" in str(error)):
                            matched_faults[name] += 1
                            continue
                        reason = "unexpected original fault: " + original_error
                    elif not ok:
                        reason = str(error)
                    else:
                        if array:
                            raw = (tag, oracle.read_u32(ptr + 20 + (index % 3) * 4))
                        else:
                            raw = expected[0]
                        actual_local = -0.0 if sign else actual_local
                        if not equal(raw, actual_local):
                            reason = f"stored value bits {bits(actual_local, tag)} != {raw}"
                        elif len(actual_stack) != len(expected) - 1:
                            reason = f"stack depth {len(actual_stack)} != {len(expected)-1}"
                        else:
                            for i, raw in enumerate(expected[1:], 1):
                                value = -0.0 if signs[i] else actual_stack[i]
                                if not equal(raw, value):
                                    reason = f"result bits {bits(value, raw[0])} != {raw}"
                                    break
                    if reason:
                        failures[name] += 1
                        if failures[name] <= 8:
                            report["failures"].append({
                                "sample": sample["sample"], "opcode": row["opcode"], "name": name,
                                "initial": initial, "operand": operand, "index": index,
                                "array": array, "reason": reason})
        report["samples"].append({
            "sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
            "numeric_profile": profile, "cases": dict(counts), "failures": dict(failures),
            "matched_original_faults": dict(matched_faults),
            "passed_opcodes": sorted(name for name in counts if not failures[name]),
        })
        print(f"{sample['sample']}: {sum(counts.values())} store comparisons; "
              f"{sum(failures.values())} mismatches", flush=True)
    report["comparisons"] = sum(sum(row["cases"].values()) for row in report["samples"])
    report["mismatches"] = sum(sum(row["failures"].values()) for row in report["samples"])
    report["test_sources"] = {
        name: hashlib.sha256((ROOT / "scripts/analysis" / name).read_bytes()).hexdigest()
        for name in ("original_opcode.py", "test_storage_semantics.py")
    }
    report["status"] = "passed" if not report["mismatches"] else "failed"
    folder = archive_dir("storage_semantics")
    write_json(folder / (folder.name[:8] + "_storage_semantics.json"), report)
    write_json(ROOT / "reports/storage_semantics_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
