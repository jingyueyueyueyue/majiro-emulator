"""Compare actual original opcode execution with the shared Lua VM in batches."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import sys

from lua_bridge import create_lua
from original_opcode import OpcodeOracle, f32_bits, native_number
from original_fixture_scope import profile_for, finish_scope

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json

INTS = [-2147483648, -2147483647, -65537, -33, -1, 0, 1, 2, 31, 32,
        33, 65535, 16777217, 2147483647]
FLOAT_BITS = [0, 0x80000000, 1, 0x80000001, 0x3F000000, 0xBF000000,
              0x3F800000, 0xBF800000, 0x4B800000, 0x4F000000,
              0x7F7FFFFF, 0xFF7FFFFF, 0x7F800000, 0xFF800000, 0x7FC00000]
BINARY_INT = {"mul.i", "div.i", "rem", "add.i", "sub.i", "shr", "shl",
              "cle.i", "clt.i", "cge.i", "cgt.i", "ceq.i", "cne.i",
              "xor", "andl", "orl", "and", "or"}
BINARY_REAL = {"mul.r", "div.r", "add.r", "sub.r",
               "cle.r", "clt.r", "cge.r", "cgt.r", "ceq.r", "cne.r"}
UNARY_INT = {"notl", "not", "neg.i", "conv.r"}
UNARY_REAL = {"neg.r", "conv.i"}


def cases(name):
    integers = [(0, n & 0xFFFFFFFF) for n in INTS]
    reals = [(1, n) for n in FLOAT_BITS]
    if name in UNARY_INT:
        return [(value,) for value in integers]
    if name in UNARY_REAL:
        return [(value,) for value in reals]
    values = integers if name in BINARY_INT else integers + reals
    result = [(a, b) for a in values for b in values]
    rng = random.Random(171027)
    if name in BINARY_INT:
        result += [((0, rng.getrandbits(32)), (0, rng.getrandbits(32)))
                   for _ in range(48)]
    return result


def run(selected=None):
    recovered = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))
    lua = create_lua()
    module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    opcodes = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf-8"))
    execute = lua.eval("""
    function(mod,ops,opcode,values,profile)
      local script={name='numeric-fixture',main=0,functions={},
        code={{0,opcode},{2,0x800,0},{8,0x82b}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,numeric_profile=profile,
        syscall=function()error('numeric fixture called a syscall')end}
      vm:begin(script.name)
      for _,value in ipairs(values)do vm:push(value)end
      local ok,why=pcall(function()vm:step()end)
      local signs={}
      for i,value in ipairs(vm.stack)do
        signs[i]=value==0 and 1/value==-math.huge
      end
      return ok,why,vm.stack,signs
    end
    """)
    folder = archive_dir("numeric_semantics")
    report = {"schema": 1, "timestamp": timestamp(), "samples": [], "failures": [],
              "original_faults": [], "scope": "numeric values and operand-stack depth",
              "host_calls_replaced": False, "complete_engine_semantics": False,
              "vm_sha256": hashlib.sha256((ROOT / "player/runtime/majiro_vm.lua").read_bytes()).hexdigest()}
    profiles = json.loads((ROOT / "config/player_engine_profiles.json").read_text(encoding="utf-8"))
    names = BINARY_INT | BINARY_REAL | UNARY_INT | UNARY_REAL
    total = 0
    for sample in recovered["samples"]:
        if selected and sample["sample"] not in selected:
            continue
        profile = profile_for(profiles, sample, report)
        if profile is None:
            continue
        oracle = OpcodeOracle(sample)
        profile = profile["numeric"]
        counts, failures, faults = Counter(), Counter(), Counter()
        for name in sorted(names):
            row = oracle.rows[name]
            for values in cases(name):
                operands = [native_number(*value) for value in values]
                try:
                    expected = oracle.execute(name, values)
                except Exception as error:
                    # These are evidence, never silently counted as successful.
                    faults[name] += 1
                    known_overflow = (name in ("div.i", "rem") and
                                      values == ((0, 0x80000000), (0, 0xFFFFFFFF)) and
                                      "UC_ERR_EXCEPTION" in str(error))
                    ok, why, _, _ = execute(module, opcodes, int(row["opcode"], 16),
                                            lua.table_from(operands), profile)
                    matched = known_overflow and not ok and "signed division overflow" in str(why)
                    report["original_faults"].append({
                        "sample": sample["sample"], "opcode": row["opcode"], "name": name,
                        "operands": values, "error": str(error),
                        "vm_reported_equivalent_fault": matched})
                    if not matched:
                        failures[name] += 1
                        report["failures"].append({
                            "sample": sample["sample"], "opcode": row["opcode"], "name": name,
                            "operands": values, "reason": "original fault was not matched: " + str(error)})
                    continue
                counts[name] += 1
                total += 1
                ok, error, actual, signs = execute(module, opcodes, int(row["opcode"], 16),
                                                   lua.table_from(operands), profile)
                reason = None
                if not ok:
                    reason = str(error)
                elif len(actual) != len(expected):
                    reason = f"stack length {len(actual)} != {len(expected)}"
                else:
                    for index, (tag, payload) in enumerate(expected, 1):
                        value = -0.0 if signs[index] else actual[index]
                        if not isinstance(value, (int, float)):
                            reason = f"nonnumeric result {value!r}"
                            break
                        actual_bits = f32_bits(value) if tag == 1 else (
                            int(value) & 0xFFFFFFFF if math.isfinite(value) else None)
                        # Preserve zero signs and infinities; NaN payloads are
                        # outside the VM's exposed value model.
                        nan = tag == 1 and math.isnan(native_number(tag, payload))
                        if not (nan and math.isnan(value)) and actual_bits != payload:
                            reason = f"result bits {actual_bits} != {payload}"
                            break
                if reason:
                    failures[name] += 1
                    if failures[name] <= 12:
                        report["failures"].append({
                            "sample": sample["sample"], "opcode": row["opcode"], "name": name,
                            "operands": values, "expected": expected, "reason": reason})
        result = {"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
                  "numeric_profile": profile,
                  "cases": dict(counts), "failures": dict(failures),
                  "original_faults": dict(faults),
                  "passed_opcodes": sorted(name for name in names
                                          if counts[name] and not failures[name]),
                  "status": "passed" if not failures else "failed"}
        report["samples"].append(result)
        print(f"{sample['sample']}: {sum(counts.values())} comparisons; "
              f"{sum(failures.values())} mismatches; {sum(faults.values())} original faults",
              flush=True)
    report["comparisons"] = total
    report["matched_original_faults"] = sum(row["vm_reported_equivalent_fault"]
                                          for row in report["original_faults"])
    report["test_sources"] = {
        name: hashlib.sha256((ROOT / "scripts/analysis" / name).read_bytes()).hexdigest()
        for name in ("original_opcode.py", "test_numeric_semantics.py")
    }
    report["mismatches"] = sum(sum(row["failures"].values()) for row in report["samples"])
    report["status"] = "passed" if report["samples"] and not report["mismatches"] else "failed"
    finish_scope(report)
    write_json(folder / (folder.name[:8] + "_numeric_semantics.json"), report)
    write_json(ROOT / "reports/numeric_semantics_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="append")
    raise SystemExit(run(parser.parse_args().sample))
