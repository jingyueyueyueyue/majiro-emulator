"""Compare numeric load flags, mutations and wrapped multidimensional indexing."""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import struct
import sys

from lua_bridge import create_lua
from original_opcode import OpcodeOracle, f32_bits, native_number
from original_fixture_scope import profile_for, finish_scope
from original_array_layout import legacy_3d_stride
from test_storage_semantics import equal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def run():
    dispatch = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))
    profiles = json.loads((ROOT / "config/player_engine_profiles.json").read_text(encoding="utf-8"))
    lua = create_lua()
    module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    opcodes = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf-8"))
    execute = lua.eval("""
    function(mod,ops,opcode,flags,left,indices,dims,flat,profile,legacy_stride)
      local script={name='load-fixture',main=0,functions={},
        code={{0,opcode,flags,123,0},{10,0x800,0},{16,0x82b}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=function()error('load fixture invoked syscall')end,numeric_profile=profile,
        array_3d_legacy_stride=legacy_stride}
      vm:begin(script.name)
      local target=left
      if #dims>0 then
        target={kind='array',dims=dims,element_type=math.floor(flags/256)%8,values={}}
        local count=1
        for _,dimension in ipairs(dims)do count=count*dimension end
        for i=0,count-1 do target.values[i]=left end
      end
      vm:frame().locals[0]=target
      for _,index in ipairs(indices)do vm:push(index,0)end
      local ok,why=pcall(function()vm:step()end)
      local stored=#dims>0 and target.values[flat]or vm:frame().locals[0]
      local signs={}
      for i,value in ipairs(vm.stack)do signs[i]=value==0 and 1/value==-math.huge end
      return ok,why,stored,vm.stack,stored==0 and 1/stored==-math.huge,signs,vm.stack_types
    end
    """)
    report = {"schema": 1, "timestamp": timestamp(), "samples": [], "failures": [],
              "scope": "integer/real load flags, stored mutations, result types, 1/2/3D wrapped indices",
              "excluded": "reserved modifier values 5/6/7 leave uninitialized original results; VM rejects them",
              "vm_sha256": hashlib.sha256((ROOT / "player/runtime/majiro_vm.lua").read_bytes()).hexdigest(),
              "host_calls_replaced": False, "complete_engine_semantics": False}
    values = [(0, v & 0xFFFFFFFF) for v in (-2147483648, -1, 0, 1, 2147483647)]
    values += [(1, f32_bits(v)) for v in (-1.25, -0.0, 0.0, 1.25, 16777216.0)]
    for sample in dispatch["samples"]:
        oracle = OpcodeOracle(sample)
        profile = profile_for(profiles, sample, report)
        if profile is None:
            continue
        profile = profile["numeric"]
        legacy_stride=legacy_3d_stride(oracle)
        counts, failures = Counter(), Counter()
        for name in ("ld", "ldelem"):
            row = oracle.rows[name]
            for dims in ((),) if name == "ld" else ((3,), (2, 3), (2, 3, 4)):
                coordinate_sets = [()] if not dims else [
                    tuple(-1 for _ in dims), tuple(d for d in dims), tuple(d + 1 for d in dims)]
                for initial, modifier, inversion, indices in itertools.product(
                        values, range(5), range(4), coordinate_sets):
                    tag, payload = initial
                    flags = 96 + tag * 256 + modifier + inversion * 8 + len(dims) * 2048
                    flat, length = 0, 1
                    for dimension, index in zip(dims, indices):
                        flat = flat * dimension + index % dimension
                        length *= dimension
                    if legacy_stride and len(dims)==3:
                        flat=(indices[0]%dims[0])*dims[1]+(indices[1]%dims[1])*dims[2]+indices[2]%dims[2]
                    ptr = oracle.DATA + 0x30000
                    native_values = [initial]
                    if dims:
                        header = struct.pack("<5I", 64, 0, *((1,) * (3-len(dims)) + dims))
                        oracle.machine.mem_write(ptr, header + struct.pack("<I", payload) * length)
                        native_values = [(tag + 3, ptr)] + [(0, i & 0xFFFFFFFF) for i in indices]
                    counts[name] += 1
                    reason = None
                    expected = None
                    try:
                        expected = oracle.execute(name, native_values, struct.pack("<HIh", flags, 123, 0),
                                                  budget=20000)
                        ok, error, local, stack, sign, signs, types = execute(
                            module, opcodes, int(row["opcode"], 16), flags, native_number(*initial),
                            lua.table_from(indices), lua.table_from(dims), flat, profile, legacy_stride)
                        if not ok:
                            reason = str(error)
                        else:
                            raw_local = (tag, oracle.read_u32(ptr + 20 + flat * 4)) if dims else expected[0]
                            local = -0.0 if sign else local
                            if not equal(raw_local, local):
                                reason = f"stored value {local} != {raw_local}"
                            elif len(stack) != len(expected)-1:
                                reason = f"stack depth {len(stack)} != {len(expected)-1}"
                            else:
                                for i, raw in enumerate(expected[1:], 1):
                                    value = -0.0 if signs[i] else stack[i]
                                    if not equal(raw, value) or types[i] != raw[0]:
                                        reason = f"result {value} type {types[i]} != {raw}"
                                        break
                    except Exception as error:
                        reason = repr(error)
                    if reason:
                        failures[name] += 1
                        if failures[name] <= 16:
                            report["failures"].append({
                                "sample": sample["sample"], "name": name, "initial": initial,
                                "modifier": modifier, "inversion": inversion, "dimensions": dims,
                                "indices": indices, "expected": expected, "reason": reason})
        report["samples"].append({
            "sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
            "array_3d_legacy_stride":legacy_stride,
            "cases": dict(counts), "failures": dict(failures),
            "passed_opcodes": sorted(name for name in counts if not failures[name]),
        })
        print(f"{sample['sample']}: {sum(counts.values())} load comparisons; "
              f"{sum(failures.values())} mismatches", flush=True)
    report["comparisons"] = sum(sum(row["cases"].values()) for row in report["samples"])
    report["mismatches"] = sum(sum(row["failures"].values()) for row in report["samples"])
    report["test_sources"] = {
        name: hashlib.sha256((ROOT / "scripts/analysis" / name).read_bytes()).hexdigest()
        for name in ("original_opcode.py", "original_fixture_scope.py",
                     "original_comparison_profiles.py", "original_array_layout.py",
                     "test_storage_semantics.py", "test_load_semantics.py")
    }
    report["status"] = "passed" if not report["mismatches"] else "failed"
    finish_scope(report)
    folder = archive_dir("load_semantics")
    write_json(folder / (folder.name[:8] + "_load_semantics.json"), report)
    write_json(ROOT / "reports/load_semantics_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
