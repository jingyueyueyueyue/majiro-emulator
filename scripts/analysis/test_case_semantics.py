"""Compare case selectors, residual stack and typed branches in every EXE."""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import struct
import sys

from audit_player_semantics import registered_providers, source_identity
from original_opcode import f32_bits, native_number
from original_strings import StringOracle
from original_fixture_scope import profile_for, finish_scope
from test_storage_semantics import equal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json

CASE_HASH=0x11F91FD3


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    profiles=json.loads((ROOT/"config/player_engine_profiles.json").read_text(encoding="utf-8"))
    before=source_identity()
    report={"schema":1,"timestamp":timestamp(),"samples":[],"failures":[],
            "source_identity":before,"vm_sha256":before["files"]["player/runtime/majiro_vm.lua"],
            "scope":"typed case comparisons, selector storage, operand retention and branch destination",
            "host_boundaries":["initialized CRT","bounded guest heap","Windows CP932 NLS"],
            "limits":["earliest builds lack a string relational branch and read an uninitialized local; "
                      "these undefined inputs are separately checked for explicit VM rejection"],
            "complete_engine_semantics":False}
    names=("br.case","bne.case","bge.case","ble.case","blt.case","bgt.case")
    for sample in samples:
        profile=profile_for(profiles,sample,report,
            ("numeric","minmax_arguments","string_case","integer_parse","empty_search_absent","case_string_relations"))
        if profile is None:
            continue
        oracle=StringOracle(sample)
        lua,_,_,system=registered_providers(profile["minmax_arguments"],profile["string_case"],
                                            profile["integer_parse"],profile["empty_search_absent"])
        mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
        ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
        execute=lua.eval("""
        function(mod,ops,strings,opcode,left,right,tag,profile,case_strings)
          local script={name='case-fixture',main=0,functions={},
            code={{0,opcode,48},{6,0x800,0},{48,0x800,0}}}
          local vm=mod.new{opcodes=ops,loader=function()return script end,
            syscall=function()error('unexpected case syscall')end,string_ops=strings,
            numeric_profile=profile}
          vm.case_string_relations=case_strings
          vm:begin(script.name)
          vm.globals[2][0x11f91fd3]=left
          vm.threads[1].case_type=tag
          vm:frame().case=left
          vm:push(right,tag)
          local ok,why=pcall(function()vm:step()end)
          local signs={}
          for i,value in ipairs(vm.stack)do signs[i]=type(value)=='number'and value==0 and 1/value==-math.huge end
          local selector=vm.globals[2][0x11f91fd3]
          return ok,why,vm:frame().script.code[vm:frame().index][1],
            vm.stack,vm.stack_types,selector,signs,
            type(selector)=='number'and selector==0 and 1/selector==-math.huge
        end
        """)
        counts,failures,rejected=Counter(),Counter(),Counter()
        groups=[[(0,value&0xffffffff)for value in(-2147483648,-1,0,1,2147483647)],
                [(1,value)for value in(0,0x80000000,f32_bits(-1.25),f32_bits(2.5),0x7fc00000)],
                [(2,value)for value in("","A","a","\uff21","\uff41","\u0391","\u03b1","\uff76")]]
        for name in names:
            for group in groups:
                for first,second in itertools.product(group,repeat=2):
                    tag=first[0]
                    left=oracle.string(first[1],0)if tag==2 else first[1]
                    right=oracle.string(second[1],1)if tag==2 else second[1]
                    case_pointer=oracle.DATA+0x50000
                    oracle.machine.mem_write(case_pointer,struct.pack("<4I",CASE_HASH,tag,2,left))
                    counts[name]+=1
                    reason=None
                    try:
                        expected=oracle.execute(name,[(tag,right)],struct.pack("<i",42),
                            context_fields={8:1,0x20:case_pointer},budget=200000)
                        left_value=first[1]if tag==2 else native_number(tag,left)
                        right_value=second[1]if tag==2 else native_number(tag,right)
                        ok,why,pc,stack,types,selector,signs,selector_sign=execute(mod,ops,system["strings"],
                            int(oracle.rows[name]["opcode"],16),left_value,right_value,tag,
                            profile["numeric"],profile["case_string_relations"])
                        if tag==2 and name not in("br.case","bne.case")and not profile["case_string_relations"]:
                            if not ok and "does not define string relational case"in str(why):
                                rejected[name]+=1
                                counts[name]-=1
                                continue
                            raise ValueError("undefined original string relation was not rejected")
                        if not ok:reason=str(why)
                        elif pc!=oracle.read_u32(oracle.frame)-(oracle.operand-2):
                            reason=f"branch destination {pc} differs"
                        elif len(stack)!=len(expected):reason=f"residual stack {len(stack)} != {len(expected)}"
                        else:
                            for i,raw in enumerate(expected,1):
                                actual=-0.0 if signs[i]else stack[i]
                                if (tag==2 and actual!=oracle.string_result(*raw))or(
                                    tag!=2 and not equal(raw,actual))or types[i]!=raw[0]:
                                    reason="residual stack value or type differs";break
                        raw_tag=oracle.read_u32(case_pointer+4);raw=oracle.read_u32(case_pointer+12)
                        if selector_sign:selector=-0.0
                        if not reason and (
                            (raw_tag==2 and selector!=oracle.string_result(raw_tag,raw))or
                            (raw_tag!=2 and not equal((raw_tag,raw),selector))):
                            reason="stored thread selector differs"
                    except Exception as error:reason=str(error)
                    if reason:
                        failures[name]+=1
                        if failures[name]<=12:report["failures"].append({"sample":sample["sample"],
                            "name":name,"left":first,"right":second,"reason":reason})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "original_reference":oracle.reference_provenance,
            "cases":dict(counts),"failures":dict(failures),
            "undefined_string_relations_rejected":dict(rejected),
            "passed_opcodes":sorted(name for name in counts if not failures[name])})
        print(f"{sample['sample']}: {sum(counts.values())} case comparisons; "
              f"{sum(failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(sum(row["cases"].values())for row in report["samples"])
    report["mismatches"]=sum(sum(row["failures"].values())for row in report["samples"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_reference.py","original_strings.py","test_storage_semantics.py","test_case_semantics.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    finish_scope(report)
    folder=archive_dir("case_semantics")
    write_json(folder/(folder.name[:8]+"_case_semantics.json"),report)
    write_json(ROOT/"reports/case_semantics_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
