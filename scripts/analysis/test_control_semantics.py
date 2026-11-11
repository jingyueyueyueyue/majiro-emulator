"""Original fetch/dispatch comparisons for branches, constants and event slots."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

from lua_bridge import create_lua, table_value
from original_opcode import OpcodeOracle, f32_bits, native_number
from original_fixture_scope import profile_for, finish_scope
from test_storage_semantics import equal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json

EVENT_SLOTS = {0x14: "left", 0x18: "right", 0x1C: "middle", 0x24: "destructor"}


def fixtures(oracle):
    ints = [(0, n & 0xFFFFFFFF) for n in (-2147483648, -1, 0, 1, 2147483647)]
    floats = [(1, bits) for bits in (0, 0x80000000, 1, 0x3F800000, 0xBF800000,
                                     0x7F800000, 0xFF800000, 0x7FC00000)]
    for name in ("nop.191", "nop.1a8", "nop.1a9"):
        yield {"name": name, "values": [(0, 77), (1, f32_bits(1.25))], "end": 2}
    for tag, payload in ints:
        yield {"name": "ldc.i", "operands": struct.pack("<I", payload),
               "row_args": [native_number(tag, payload)], "end": 6}
    for tag, payload in floats:
        yield {"name": "ldc.r", "operands": struct.pack("<I", payload),
               "row_args": [native_number(tag, payload)], "end": 6}
    for value in ints + floats:
        yield {"name": "pop", "values": [value], "end": 2}
        for name in ("brtrue", "brfalse"):
            yield {"name": name, "values": [value], "operands": struct.pack("<i", 42),
                   "row_args": [48], "end": 6}
    yield {"name": "br", "operands": struct.pack("<i", 42), "row_args": [48], "end": 6}
    for index in (-2147483648, -1, 0, 1, 2, 3, 2147483647):
        targets = [100, 110, 120]
        operands = struct.pack("<H", len(targets)) + b"".join(
            struct.pack("<i", target - (8 + 4 * i)) for i, target in enumerate(targets))
        yield {"name": "switch", "values": [(0, index & 0xFFFFFFFF)],
               "operands": operands, "row_args": [targets], "end": 16}
    for line in (0, 1, 32767, 32768, 65535):
        yield {"name": "line", "operands": struct.pack("<H", line),
               "row_args": [line], "end": 4, "context_fields": {4: 1}}
    object_address = oracle.DATA + 0x40000
    for element in range(3):
        for same in (False, True):
            for op in ("ceq", "cne"):
                a, b = object_address, object_address if same else object_address + 0x100
                # Live fixture references keep destruction out of the equality
                # operation. The original decrements them itself.
                for address in {a, b}:
                    oracle.machine.mem_write(address, struct.pack("<6I", 64, 0, 1, 1, 1, 0))
                yield {"name": f"{op}.{('iarr','rarr','sarr')[element]}",
                       "values": [(3 + element, a), (3 + element, b)],
                       "arrays": {"element": element, "same": same}, "end": 2}
    base = oracle.operand - 2
    for name in ("bsel.1", "bsel.2", "bsel.3", "bsel.x", "bsel.4", "bsel.5", "bsel.clr"):
        for seeded in (False, True):
            fields = {offset: base + destination for offset, destination in (
                (0x14, 100), (0x18, 110), (0x1C, 120), (0x20, 130), (0x24, 140))} if seeded else {}
            args = [] if name == "bsel.clr" else [200]
            yield {"name": name, "row_args": args,
                   "operands": b"" if not args else struct.pack("<i", 194),
                   "end": 2 if not args else 6, "frame_fields": fields, "event": True}
    for target in (None, 0, 2, 200):
        yield {"name": "bsel.jmp.4", "end": 2, "event": True,
               "frame_fields": {0x20: base + target if target is not None else 0,
                                0x48: 91, 0x4C: 92}}


def run():
    samples = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    profiles = json.loads((ROOT / "config/player_engine_profiles.json").read_text(encoding="utf-8"))
    lua = create_lua()
    module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    opcodes = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf-8"))
    execute = lua.eval("""
    function(mod,ops,script,values,types,arrays,handlers,branch4,profile)
      local vm=mod.new{opcodes=ops,loader=function()return script end,numeric_profile=profile,
        syscall=function()error('control fixture invoked syscall')end}
      vm:begin(script.name)
      vm:frame().handlers=handlers
      vm:frame().branch4=branch4
      if arrays then
        local a={kind='array',dims={1},element_type=arrays.element,values={}}
        local b=arrays.same and a or{kind='array',dims={1},element_type=arrays.element,values={}}
        vm:push(a);vm:push(b)
      else for i,value in ipairs(values)do vm:push(value,types[i])end end
      local ok,why=pcall(function()vm:step()end)
      local frame=vm:frame()
      local signs={}
      for i,value in ipairs(vm.stack)do signs[i]=type(value)=='number'and value==0 and 1/value==-math.huge end
      local slots={}
      for _,hook in ipairs(frame.handlers or{})do slots[hook.kind]=hook.offset end
      return ok,why,vm.stack,vm.stack_types,signs,
        frame.script.code[frame.index][1],frame.line,slots,frame.branch4
    end
    """)
    report = {"schema": 1, "timestamp": timestamp(), "samples": [], "failures": [],
              "scope": "constants, numeric branches, switch defaults, array identity, event slots, signed line metadata",
              "limits": ["line bitmap side effects and pending-click registration are tested separately",
                         "branch4 nested frames remain covered by the separate original Ruri fixture"],
              "vm_sha256": hashlib.sha256((ROOT / "player/runtime/majiro_vm.lua").read_bytes()).hexdigest(),
              "host_calls_replaced": False, "complete_engine_semantics": False}
    for sample in samples:
        profile = profile_for(profiles, sample, report)
        if profile is None:
            continue
        oracle = OpcodeOracle(sample)
        profile = profile["numeric"]
        counts, failures = Counter(), Counter()
        for case in fixtures(oracle):
            name = case["name"]
            counts[name] += 1
            values = case.get("values", [])
            args = case.get("row_args", [])
            fields = case.get("frame_fields", {})
            end = case["end"]
            offsets = sorted({end, 48, 100, 110, 120, 130, 140, 200})
            script = {"name": "control-fixture", "main": 0, "functions": [],
                      "code": [[0, int(oracle.rows[name]["opcode"], 16), *args],
                               *[[offset, int(oracle.rows["nop.191"]["opcode"], 16)] for offset in offsets]]}
            base = oracle.operand - 2
            handlers = [{"kind": kind, "offset": fields[offset] - base, "stack_size": len(values)}
                        for offset, kind in EVENT_SLOTS.items() if fields.get(offset)]
            branch4 = fields.get(0x20, 0) - base if fields.get(0x20) else None
            reason = None
            try:
                expected = oracle.execute(name, values, case.get("operands", b""),
                                          frame_fields=fields,
                                          context_fields=case.get("context_fields"), budget=20000)
                numeric_values = [native_number(*value) for value in values] if not case.get("arrays") else []
                ok, error, actual, types, signs, pc, line, slots, actual_branch4 = execute(
                    module, opcodes, lua.table_from(script, recursive=True),
                    lua.table_from(numeric_values), lua.table_from([value[0] for value in values]),
                    lua.table_from(case["arrays"], recursive=True) if case.get("arrays") else None,
                    lua.table_from(handlers, recursive=True), branch4, profile)
                if not ok:
                    reason = str(error)
                elif pc != oracle.read_u32(oracle.frame) - base:
                    reason = f"PC {pc} != {oracle.read_u32(oracle.frame)-base}"
                elif len(actual) != len(expected):
                    reason = f"stack length {len(actual)} != {len(expected)}"
                else:
                    for i, raw in enumerate(expected, 1):
                        value = -0.0 if signs[i] else actual[i]
                        if not equal(raw, value) or types[i] != raw[0]:
                            reason = f"result {value} type {types[i]} != {raw}"
                            break
                if not reason and name == "line":
                    wanted = struct.unpack("<i", struct.pack("<I", oracle.read_u32(oracle.frame+0x10)))[0]
                    if line != wanted:
                        reason = f"line {line} != {wanted}"
                if not reason and case.get("event"):
                    wanted_slots = {kind: oracle.read_u32(oracle.frame+offset)-base
                                    for offset, kind in EVENT_SLOTS.items()
                                    if oracle.read_u32(oracle.frame+offset)}
                    wanted_branch4 = oracle.read_u32(oracle.frame+0x20)
                    wanted_branch4 = wanted_branch4-base if wanted_branch4 else None
                    if table_value(slots) != wanted_slots or actual_branch4 != wanted_branch4:
                        reason = f"event slots {table_value(slots)}, {actual_branch4} != {wanted_slots}, {wanted_branch4}"
            except Exception as error:
                reason = repr(error)
            if reason:
                failures[name] += 1
                if failures[name] <= 10:
                    report["failures"].append({"sample": sample["sample"], "name": name,
                                               "values": values, "row_args": args, "reason": reason})
        report["samples"].append({"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
                                  "cases": dict(counts), "failures": dict(failures),
                                  "passed_opcodes": sorted(name for name in counts if not failures[name])})
        print(f"{sample['sample']}: {sum(counts.values())} control comparisons; "
              f"{sum(failures.values())} mismatches", flush=True)
    report["comparisons"] = sum(sum(row["cases"].values()) for row in report["samples"])
    report["mismatches"] = sum(sum(row["failures"].values()) for row in report["samples"])
    report["test_sources"] = {name: hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
                              for name in ("original_opcode.py", "test_storage_semantics.py",
                                           "test_control_semantics.py")}
    report["status"] = "passed" if not report["mismatches"] else "failed"
    finish_scope(report)
    folder = archive_dir("control_semantics")
    write_json(folder/(folder.name[:8]+"_control_semantics.json"), report)
    write_json(ROOT/"reports/control_semantics_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
