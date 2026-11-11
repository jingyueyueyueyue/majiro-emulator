"""Original argument tags, arity checks and source diagnostic comparison."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

from lua_bridge import create_lua
from original_calls import CallOracle, OriginalDiagnostic
from original_fixture_scope import profile_for, finish_scope
from original_opcode import f32_bits, native_number

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def argument(oracle, tag, index):
    if tag == 0:
        return (tag, (-7-index) & 0xFFFFFFFF), -7-index
    if tag == 1:
        return (tag, f32_bits(1.0+index)), 1.0+index
    if tag == 2:
        text = "argument" + str(index) + "\u3042"
        return (tag, oracle.string(text, index)), text
    element = tag-3
    pointer = oracle.DATA+0x50000+index*0x400
    data = [0, 0]
    values = {0: 0, 1: 0}
    if element == 2:
        data = [oracle.string("", 10+index)] * 2
        values = {0: "", 1: ""}
    oracle.machine.mem_write(pointer, struct.pack("<7I", 64, 0, 1, 1, 2, *data))
    return (tag, pointer), {"kind": "array", "element_type": element,
                           "dims": [2], "values": values}


def run():
    samples = json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    profiles = json.loads((ROOT/"config/player_engine_profiles.json").read_text(encoding="utf-8"))
    lua = create_lua()
    module = lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    opcodes = lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
    execute = lua.eval("""
    function(mod,ops,declared,args,tags,profile)
      local script={name='argument-fixture',main=0,functions={},
        code={{0,0x836,declared},{4+#declared,0x191}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=function()error('unexpected argument syscall')end,numeric_profile=profile}
      args._types=tags
      vm:begin(script.name)
      vm:frame().args=args;vm:frame().arg_types=tags
      vm:frame().system[2802105789]=#args
      local ok,why=pcall(function()vm:step()end)
      return ok,why,vm:frame().args,vm:frame().arg_types,
        vm:frame().script.code[vm:frame().index][1],vm:frame().system[2802105789]
    end
    """)
    fixtures = [
        ([], []), ([0], [0]), ([1], [1]), ([2], [2]),
        ([3], [3]), ([4], [4]), ([5], [5]),
        ([0,1,2,3,4,5], [0,1,2,3,4,5]),
        ([5,4,3,2,1,0], [5,4,3,2,1,0]),
        ([], [0,1,2]), ([0], [0,1,2]), ([0,1], [0,1,2]),
        ([0], []), ([0,1], [0]), ([5,4,3], [5,4]),
    ]
    fixtures += [([expected], [supplied]) for expected in range(6)
                 for supplied in range(6) if expected != supplied]
    report = {"schema": 1, "timestamp": timestamp(), "samples": [], "failures": [],
              "scope": "strict native argument type tags, accepted extra arguments, arity diagnostics and parameter preservation",
              "limits": ["native source diagnostics stop before the Windows error dialog; dialog interaction is not exercised"],
              "host_boundaries": ["bounded guest heap", "CP932 CRT initialization",
                                  "stop on original source diagnostic"],
              "vm_sha256": hashlib.sha256((ROOT/"player/runtime/majiro_vm.lua").read_bytes()).hexdigest(),
              "complete_engine_semantics": False}
    for sample in samples:
        profile = profile_for(profiles, sample, report)
        if profile is None:
            continue
        oracle = CallOracle(sample)
        failed = normal = diagnostics = 0
        for declared, tags in fixtures:
            args, native = [], []
            for i, tag in enumerate(tags):
                raw, value = argument(oracle, tag, i)
                args.append(value); native.append(raw)
            initial = list(reversed(native)) + [(0, len(native))]
            original_error = None
            expected = None
            try:
                expected = oracle.execute("argcheck", initial,
                    struct.pack("<H", len(declared))+bytes(declared),
                    frame_fields={4: len(initial)}, budget=200000)
            except OriginalDiagnostic as error:
                original_error = str(error)
            except Exception as error:
                failed += 1
                report["failures"].append({"sample": sample["sample"], "declared": declared,
                    "supplied": tags, "reason": "unexpected original failure: " + str(error)})
                continue
            ok, why, actual_args, actual_tags, pc, count = execute(module, opcodes,
                lua.table_from(declared), lua.table_from(args, recursive=True), lua.table_from(tags),
                profile["numeric"])
            reason = None
            if original_error:
                diagnostics += 1
                if ok:
                    reason = "original emits a source diagnostic but VM accepted arguments"
                elif not any(text in str(why) for text in ("not enough function arguments", "argument type mismatch")):
                    reason = "runtime failed for an unrelated reason: " + str(why)
            else:
                normal += 1
                if not ok:
                    reason = str(why)
                elif pc != 4+len(declared) or count != len(tags) or len(actual_args) != len(tags):
                    reason = "parameter count or type-list consumption differs"
                elif expected != initial:
                    reason = "native argument checking unexpectedly changed the parameter slots"
                else:
                    for i, tag in enumerate(tags, 1):
                        if actual_tags[i] != tag:
                            reason = "portable argument tag changed"; break
                        if tag < 3 and actual_args[i] != args[i-1]:
                            reason = "portable argument value changed"; break
            if reason:
                failed += 1
                report["failures"].append({"sample": sample["sample"], "declared": declared,
                    "supplied": tags, "native_diagnostic": original_error, "reason": reason})
        cases = normal+diagnostics
        report["samples"].append({"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
            "cases": {"argcheck": cases}, "accepted_cases": normal, "source_diagnostics": diagnostics,
            "failures": {"argcheck": failed} if failed else {},
            "passed_opcodes": ["argcheck"] if not failed else []})
        print(f"{sample['sample']}: {cases} argument checks; {failed} mismatches", flush=True)
    report["comparisons"] = sum(row["cases"]["argcheck"] for row in report["samples"])
    report["mismatches"] = sum(row["failures"].get("argcheck", 0) for row in report["samples"])
    report["test_sources"] = {name: hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in ("original_opcode.py", "original_strings.py", "original_calls.py", "test_argument_semantics.py")}
    report["status"] = "passed" if not report["mismatches"] else "failed"
    finish_scope(report)
    folder = archive_dir("argument_semantics")
    write_json(folder/(folder.name[:8]+"_argument_semantics.json"), report)
    write_json(ROOT/"reports/argument_semantics_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
