"""Compare scalar calls through original syscall opcodes, including ABI cleanup."""
from __future__ import annotations

import itertools
import hashlib
import json
from pathlib import Path
import random
import struct
import sys

from audit_player_semantics import registered_providers, source_identity
from original_fixture_scope import profile_for, finish_scope
from original_opcode import OpcodeOracle, native_number

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def cases(name):
    edges = [-2147483648, -65537, -1, 0, 1, 127, 255, 256, 65535, 2147483647]
    if name == "$abs":
        return [(value,) for value in edges]
    rng = random.Random(171027)
    if name in ("$min", "$max"):
        return [*itertools.product(edges, repeat=2),
                *itertools.product((-3, 0, 4), repeat=3),
                *((9, 4, 7, -8, 100, 2, -50, value) for value in edges),
                *(tuple(rng.randint(-2147483648, 2147483647) for _ in range(size))
                  for size in (2, 3, 4, 8) for _ in range(32))]
    if name == "$rgba":
        result = []
        for channel in range(4):
            for edge in edges:
                values = [17, 39, 127, 219]
                values[channel] = edge
                result.append(tuple(values))
        result += [(value,) * 4 for value in edges]
        result += [tuple(rng.randint(-1000, 1000) for _ in range(4)) for _ in range(64)]
        return result
    raise ValueError(name)


def run():
    dispatch = json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf-8"))
    opcode_rows = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))
    profiles = json.loads((ROOT / "config/player_engine_profiles.json").read_text(encoding="utf-8"))
    by_name = {row["sample"]: row for row in opcode_rows["samples"]}
    identity = source_identity()
    folder = archive_dir("scalar_syscalls")
    report = {"schema": 1, "timestamp": timestamp(), "samples": [], "failures": [],
              "source_identity": identity, "host_calls_replaced": False,
              "vm_sha256":identity["files"]["player/runtime/majiro_vm.lua"],
              "scope": "integer arguments, result value and complete syscall/syscallp stack cleanup",
              "complete_engine_semantics": False}
    for sample in dispatch["samples"]:
        profile = profile_for(profiles, sample, report, ("numeric", "minmax_arguments"))
        if profile is None:
            continue
        oracle = OpcodeOracle(by_name[sample["sample"]])
        lua, _, _, system = registered_providers(profile["minmax_arguments"])
        vm_module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
        opcodes = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf-8"))
        execute = lua.eval("""
        function(mod,ops,invoke,hash,args,discard,profile)
          local script={name='scalar-fixture',main=0,functions={},
            code={{0,discard and 0x835 or 0x834,hash,#args},{8,0x800,0},{14,0x82b}}}
          local vm=mod.new{opcodes=ops,loader=function()return script end,
            syscall=invoke,numeric_profile=profile}
          vm:begin(script.name)
          for i=#args,1,-1 do vm:push(args[i])end
          local ok,why=pcall(function()vm:step()end)
          return ok,why,vm.stack
        end
        """)
        results = []
        for entry in sample["entries"]:
            name = entry["name"]
            if name not in {"$abs", "$min", "$max", "$rgba"}:
                continue
            count = failed = 0
            arities = set()
            for args in cases(name):
                for discard in (False, True):
                    count += 1
                    arities.add(len(args))
                    reason = None
                    expected = None
                    try:
                        expected = oracle.execute(
                            "syscallp" if discard else "syscall",
                            [(0, value) for value in reversed(args)],
                            struct.pack("<IH", int(entry["hash"], 16), len(args)), budget=20000)
                        if len(expected) != int(not discard):
                            raise ValueError(f"unexpected original stack depth: {expected}")
                        ok, error, actual = execute(vm_module, opcodes, system["invoke"],
                                                    int(entry["hash"], 16), lua.table_from(args),
                                                    discard, profile["numeric"])
                        if not ok:
                            reason = str(error)
                        elif len(actual) != len(expected):
                            reason = f"stack depth {len(actual)} != {len(expected)}"
                        elif not discard and actual[1] != native_number(*expected[0]):
                            reason = f"result {actual[1]} != {native_number(*expected[0])}"
                    except Exception as error:
                        reason = repr(error)
                    if reason:
                        failed += 1
                        if failed <= 10:
                            report["failures"].append({
                                "sample": sample["sample"], "hash": entry["hash"], "name": name,
                                "args": args, "discard": discard, "expected": expected, "reason": reason,
                            })
            results.append({"hash": entry["hash"], "name": name, "cases": count,
                            "argument_counts": sorted(arities), "discard_modes": [False, True],
                            "failures": failed, "status": "passed" if not failed else "failed"})
        op_cases=sum(row["cases"]for row in results)//2
        all_passed=not any(row["failures"]for row in results)
        report["samples"].append({"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
                                  "original_reference": oracle.reference_provenance,
                                  "calls": results,"cases":{"syscall":op_cases,"syscallp":op_cases},
                                  "passed_opcodes":["syscall","syscallp"]if all_passed else[]})
        print(f"{sample['sample']}: {sum(row['cases'] for row in results)} scalar comparisons; "
              f"{sum(row['failures'] for row in results)} mismatches", flush=True)
    report["source_changed_during_test"] = source_identity() != identity
    report["test_sources"] = {
        name: hashlib.sha256((ROOT / "scripts/analysis" / name).read_bytes()).hexdigest()
        for name in ("original_opcode.py", "original_reference.py", "test_scalar_syscalls.py")
    }
    report["comparisons"] = sum(row["cases"] for sample in report["samples"] for row in sample["calls"])
    report["mismatches"] = sum(row["failures"] for sample in report["samples"] for row in sample["calls"])
    report["status"] = "passed" if not (
        report["mismatches"] or report["source_changed_during_test"]) else "failed"
    finish_scope(report)
    write_json(folder / (folder.name[:8] + "_scalar_syscalls.json"), report)
    write_json(ROOT / "reports/scalar_syscall_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
