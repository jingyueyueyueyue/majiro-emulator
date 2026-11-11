"""Original creation, copy and release of all typed Majiro array objects."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys

from audit_player_semantics import registered_providers, source_identity
from original_opcode import f32_bits, native_number
from original_strings import StringOracle
from original_fixture_scope import finish_scope

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def run():
    samples = json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    calls = json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name = {row["sample"]: row for row in calls}
    before = source_identity()
    report = {"schema": 1, "timestamp": timestamp(), "samples": [], "failures": [],
              "source_identity": before, "complete_engine_semantics": False,
              "scope": "array element type, one/two/three dimensions, default values, independent copies and release",
              "limits": ["invalid integer zero dimensions enter an original error dialog and remain pending",
                         "object allocation addresses and reference counts are compared by ownership invariants"],
              "host_boundaries": ["single-thread CRT locks", "bounded guest allocation and release", "Windows NLS"]}
    names = {f"$dim_{operation}{suffix}" for operation in ("create", "copy", "release")
             for suffix in ("#", "%#", "$#")}
    for sample in samples:
        oracle = StringOracle(sample)
        lua, _, _, system = registered_providers()
        results = []
        unavailable_reason = None
        for entry in by_name[sample["sample"]]["entries"]:
            name = entry["name"]
            if name not in names:
                continue
            element = 2 if name.endswith("$#") else 1 if name.endswith("%#") else 0
            operation = name.split("_", 1)[1].split("#")[0].rstrip("%$")
            dimensions = [()] if operation == "release" else [(1,), (4,), (2,3), (2,1,3)]
            if operation == "create" and element != 0:
                dimensions += [(0,), (2,0)]
            if operation == "copy":
                dimensions += [(0,)]
            count = failed = unavailable = 0
            arities = set()
            for dims in dimensions:
                for discard in (False, True):
                    count += 1
                    length = math.prod(dims)
                    lua_args = None
                    input_array = None
                    if operation == "create":
                        arguments = list(dims)
                        values = [(0, value) for value in reversed(arguments)]
                        lua_args = lua.table_from(arguments)
                    elif operation == "release":
                        arguments, values, lua_args = [], [], lua.table()
                    else:
                        data = [f"field{i}\u3042" if element == 2 else
                                (i*1.25-3.5 if element == 1 else i*65437-2147483648)
                                for i in range(length)]
                        raw = [oracle.string(value, i) if element == 2 else
                               f32_bits(value) if element == 1 else value & 0xFFFFFFFF
                               for i,value in enumerate(data)]
                        if len(data) > 16:
                            raise ValueError("array fixture string budget")
                        input_array = oracle.DATA+0x50000
                        header = struct.pack("<5I",64,0,*((1,)*(3-len(dims))+dims))
                        oracle.machine.mem_write(input_array,header+struct.pack("<"+"I"*len(raw),*raw))
                        values = [(3+element,input_array)]
                        array = {"kind":"array","element_type":element,"dims":list(dims),
                                 "values":{i:value for i,value in enumerate(data)}}
                        lua_args = lua.table_from([array],recursive=True)
                        arguments = [array]
                    arities.add(len(arguments))
                    reason = None
                    try:
                        if unavailable_reason is not None:
                            unavailable += 1
                            report.setdefault("unsupported_fixtures",[]).append({
                                "sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                                "name":name,"dimensions":dims,"discard":discard,
                                "status":"unsupported_fixture",
                                "reason":"Guest execution stopped after prior unmapped protected-code fetch: "+unavailable_reason,
                                "runtime_behavior_verified":False})
                            continue
                        try:
                            expected = oracle.execute("syscallp"if discard else"syscall",values,
                                struct.pack("<IH",int(entry["hash"],16),len(values)),budget=200000)
                        except Exception as native_error:
                            if "UC_ERR_FETCH_UNMAPPED" not in str(native_error):
                                raise
                            unavailable_reason = str(native_error)
                            # Protected original code is not initialized by
                            # this fixture. Retain every unavailable input,
                            # without turning it into a VM mismatch or pass.
                            unavailable += 1
                            report.setdefault("unsupported_fixtures",[]).append({
                                "sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                                "name":name,"dimensions":dims,"discard":discard,
                                "status":"unsupported_fixture","reason":str(native_error),
                                "runtime_behavior_verified":False})
                            continue
                        actual = system["providers"][name](None,lua_args)
                        if len(expected) != int(not discard):
                            raise ValueError("original array call stack depth changed")
                        if not discard:
                            tag,pointer = expected[0]
                            expected_dims = list(struct.unpack("<3I",oracle.machine.mem_read(pointer+8,12)))
                            actual_dims = [int(value)for _,value in sorted(actual["dims"].items())]
                            actual_dims = [1]*(3-len(actual_dims))+actual_dims
                            if tag != element+3 or actual["element_type"] != element or actual_dims != expected_dims:
                                reason = f"array type/dimensions {actual_dims} != {tag}, {expected_dims}"
                            elif input_array and pointer == input_array:
                                reason = "original copy aliases source array"
                            else:
                                for i in range(math.prod(expected_dims)):
                                    raw = oracle.read_u32(pointer+20+i*4)
                                    value = oracle.string_result(2,raw)if element==2 else native_number(element,raw)
                                    got = actual["values"][i]
                                    if got is None:
                                        got = ""if element==2 else 0
                                    if got != value:
                                        reason = f"array element {i}: {got!r} != {value!r}"
                                        break
                            if operation == "copy" and actual == lua_args[1]:
                                reason = "runtime copy aliases source array"
                            if operation == "copy" and length:
                                original_value=lua_args[1]["values"][0]
                                actual["values"][0]=""if element==2 else 0
                                if lua_args[1]["values"][0] != original_value:
                                    reason = "runtime copy mutation changed its source"
                    except Exception as error:
                        reason = str(error)
                    if reason:
                        failed += 1
                        if failed <= 8:
                            report["failures"].append({"sample":sample["sample"],"name":name,
                                "dimensions":dims,"discard":discard,"reason":reason})
            results.append({"name":name,"hash":entry["hash"],"cases":count-unavailable,
                "attempted_cases":count,"unsupported_fixture_cases":unavailable,"failures":failed,
                "status":"failed"if failed else"incomplete"if unavailable else"passed","argument_counts":sorted(arities),
                "discard_modes":[False,True]})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                                  "original_reference":oracle.reference_provenance,
                                  "calls":results,
                                  "unsupported_fixture_cases":sum(r["unsupported_fixture_cases"]for r in results)})
        print(f"{sample['sample']}: {sum(row['cases']for row in results)} array calls; "
              f"{sum(row['failures']for row in results)} mismatches",flush=True)
    report["comparisons"]=sum(row["cases"]for sample in report["samples"]for row in sample["calls"])
    report["mismatches"]=sum(row["failures"]for sample in report["samples"]for row in sample["calls"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in ("original_opcode.py","original_reference.py","original_strings.py","test_array_syscalls.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    finish_scope(report)
    folder=archive_dir("array_syscalls")
    write_json(folder/(folder.name[:8]+"_array_syscalls.json"),report)
    write_json(ROOT/"reports/array_syscall_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":
    raise SystemExit(run())
