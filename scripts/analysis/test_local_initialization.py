"""Compare local default values and independent object initialization."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct
import sys

from lua_bridge import create_lua
from original_strings import StringOracle
from original_opcode import native_number

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    lua=create_lua();mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
    execute=lua.eval("""
    function(mod,ops,types)
      local script={name='local-init',main=0,functions={},
        code={{0,0x829,types},{10000,0x800,0},{10006,0x82b}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=function()error('unexpected initialization syscall')end}
      vm:begin(script.name);vm:step()
      return vm:frame().locals,vm:frame().script.code[vm:frame().index][1]
    end
    """)
    report={"schema":1,"timestamp":timestamp(),"samples":[],"failures":[],
        "scope":"local type-list consumption, zero/empty defaults, default array dimensions and ownership",
        "vm_sha256":hashlib.sha256((ROOT/"player/runtime/majiro_vm.lua").read_bytes()).hexdigest(),
        "limits":["native locals occupy stack slots; the portable VM projects them to explicit frame.locals",
                  "argument type coercion is validated separately"],
        "host_boundaries":["bounded guest allocation","initialized CRT","Windows NLS"],
        "complete_engine_semantics":False}
    fixture_types=[[],[0],[1],[2],[3],[4],[5],[0,1,2,3,4,5],[3,3,4,4,5,5],[2,2,2]]
    for sample in samples:
        oracle=StringOracle(sample);failed=0
        for types in fixture_types:
            reason=None
            try:
                expected=oracle.execute("alloca",[],struct.pack("<H",len(types))+bytes(types),budget=200000)
                actual,pc=execute(mod,ops,lua.table_from(types))
                if pc!=10000 or len(expected)!=len(types):reason="local type list was not fully consumed"
                native_arrays=set()
                for i,(tag,payload)in enumerate(expected):
                    value=actual[i]
                    if tag<2:
                        if value!=native_number(tag,payload):reason="numeric default differs";break
                    elif tag==2:
                        if value!=oracle.string_result(tag,payload):reason="string default differs";break
                    else:
                        dims=list(struct.unpack("<3I",oracle.machine.mem_read(payload+8,12)))
                        actual_dims=[n for _,n in sorted(value["dims"].items())]
                        actual_dims=[1]*(3-len(actual_dims))+actual_dims
                        if dims!=actual_dims:reason=f"default dimensions {actual_dims} != {dims}";break
                        if payload in native_arrays:reason="native default array alias";break
                        native_arrays.add(payload)
                        value["values"][0]="changed"if tag==5 else 73
                        for j,other_tag in enumerate(types):
                            if j!=i and other_tag>=3 and actual[j]["values"][0]is not None:
                                reason="portable default arrays alias each other";break
                        value["values"][0]=None
            except Exception as error:reason=str(error)
            if reason:
                failed+=1;report["failures"].append({"sample":sample["sample"],"types":types,"reason":reason})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "cases":{"alloca":len(fixture_types)},"failures":{"alloca":failed}if failed else{},
            "passed_opcodes":["alloca"]if not failed else[]})
        print(f"{sample['sample']}: {len(fixture_types)} initialization cases; {failed} mismatches",flush=True)
    report["comparisons"]=sum(row["cases"]["alloca"]for row in report["samples"])
    report["mismatches"]=sum(row["failures"].get("alloca",0)for row in report["samples"])
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","test_local_initialization.py")}
    report["status"]="passed"if not report["mismatches"]else"failed"
    folder=archive_dir("local_initialization")
    write_json(folder/(folder.name[:8]+"_local_initialization.json"),report)
    write_json(ROOT/"reports/local_initialization_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
