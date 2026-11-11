"""Original nested returns preserve one result and remove frame temporaries."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from lua_bridge import create_lua
from original_opcode import OpcodeOracle,f32_bits,native_number
from original_fixture_scope import profile_for, finish_scope
from test_storage_semantics import equal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    profiles=json.loads((ROOT/"config/player_engine_profiles.json").read_text(encoding="utf-8"))
    lua=create_lua()
    mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
    execute=lua.eval("""
    function(mod,ops,values,types,wants,profile)
      local script={name='return-fixture',main=0,functions={},
        code={{0,0x191},{80,0x191},{100,0x82b}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=function()error('unexpected return syscall')end,numeric_profile=profile}
      vm:begin(script.name)
      vm:frame().index=2
      vm:enter(vm:frame().script,100,{},wants)
      for i,value in ipairs(values)do vm:push(value,types[i])end
      local ok,why=pcall(function()vm:step()end)
      return ok,why,vm.stack,vm.stack_types,#vm.frames,vm:frame().script.code[vm:frame().index][1]
    end
    """)
    report={"schema":1,"timestamp":timestamp(),"samples":[],"failures":[],
            "vm_sha256":hashlib.sha256((ROOT/"player/runtime/majiro_vm.lua").read_bytes()).hexdigest(),
            "scope":"nested returns with integer/real result, extra frame temporaries and discarded result",
            "host_calls_replaced":False,"complete_engine_semantics":False}
    for sample in samples:
        profile=profile_for(profiles,sample,report)
        if profile is None:
            continue
        oracle=OpcodeOracle(sample);cases=0;failed=0
        for wants in(False,True):
            for extra in(0,1,3):
                for result in((0,0),(0,0xffffffff),(1,f32_bits(1.25)),(1,f32_bits(-2.0))):
                    values=[(0,91+i)for i in range(extra)]+[result]
                    def setup(source):
                        child=source.frame+source.frame_stride
                        source.machine.mem_write(child,bytes(source.machine.mem_read(source.frame,source.frame_stride)))
                        source.write_u32(source.frame_global,child);source.write_u32(source.context,1)
                        source.write_u32(source.frame,source.operand-2+80)
                        source.write_u32(source.frame+8,0)
                        source.write_u32(source.frame+12,int(not wants))
                    cases+=1;reason=None
                    try:
                        expected=oracle.execute("ret",values,setup=setup,budget=20000)
                        ok,why,stack,types,depth,pc=execute(mod,ops,
                            lua.table_from([native_number(*value)for value in values]),
                            lua.table_from([value[0]for value in values]),wants,
                            profile["numeric"])
                        if not ok:reason=str(why)
                        elif depth!=1 or pc!=80 or oracle.read_u32(oracle.context)!=0:
                            reason="return frame or caller continuation differs"
                        elif len(stack)!=len(expected):reason="return stack depth differs"
                        else:
                            for i,raw in enumerate(expected,1):
                                if not equal(raw,stack[i])or types[i]!=raw[0]:
                                    reason="return result or type differs";break
                    except Exception as error:reason=str(error)
                    if reason:
                        failed+=1
                        report["failures"].append({"sample":sample["sample"],"wants":wants,
                            "extra_temporaries":extra,"result":result,"reason":reason})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "frame_stride":oracle.frame_stride,"cases":{"ret":cases},"failures":{"ret":failed}if failed else{},
            "passed_opcodes":["ret"]if not failed else[]})
        print(f"{sample['sample']}: {cases} return comparisons; {failed} mismatches",flush=True)
    report["comparisons"]=sum(row["cases"]["ret"]for row in report["samples"])
    report["mismatches"]=sum(row["failures"].get("ret",0)for row in report["samples"])
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","test_storage_semantics.py","test_return_semantics.py")}
    report["status"]="passed"if not report["mismatches"]else"failed"
    finish_scope(report)
    folder=archive_dir("return_semantics")
    write_json(folder/(folder.name[:8]+"_return_semantics.json"),report)
    write_json(ROOT/"reports/return_semantics_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
