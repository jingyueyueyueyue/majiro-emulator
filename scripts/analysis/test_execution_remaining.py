"""Original hashed subcalls: script precedence, typed arguments and return disposal."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers, source_identity
from lua_bridge import table_value
from original_calls import OriginalDiagnostic
from original_execution_remaining import ExecutionOracle
from original_opcode import native_number
from test_argument_semantics import argument

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


FIXTURE = """
function(mod,ops,system,args,tags,hash,duplicate,fallback,missing,returning,discard)
  local scripts={}
  local functions={['caller.mjo']=missing and{}or{{hash,100}},
    ['callee.mjo']=(missing or fallback)and{}or(duplicate and{{hash,100},{hash,200}}or{{hash,100}}),
    ['library.mjo']=missing and{}or{{hash,300}}}
  local function add(name)
    local code={{0,discard and 0x835 or 0x834,0x9627ddc3,#args},{8,0x191}}
    for _,at in ipairs{100,200,300}do
      code[#code+1]={at,0x836,tags}
      code[#code+1]=#tags>0 and{at+4+#tags,0x802,96+tags[1]*256,99,-2}or{at+4,0x800,77}
      code[#code+1]={at+50,0x82b}
    end
    scripts[name]={name=name,main=0,functions=functions[name],code=code}
  end
  for _,name in ipairs{'caller.mjo','callee.mjo','library.mjo'}do add(name)end
  local vm=mod.new{opcodes=ops,loader=function(name)return scripts[name]end,syscall=system.invoke}
  vm:begin('caller');vm:load('callee');vm:load('library');vm:push(71)
  for i=#args,1,-1 do vm:push(args[i],i==1 and 2 or i==2 and 0 or tags[i-2])end
  local ok,why=pcall(function()
    vm:step()
    if returning then vm:step();vm:step();vm:step()end
  end)
  local frame=vm:frame()
  local types,aliases={},{}
  for i,value in ipairs(vm.stack)do
    types[i]=vm.stack_types[i]
    for j,source in ipairs(args)do if type(source)=='table'and source==value then aliases[i]=j end end
  end
  return ok,why,#vm.frames,frame.script.name,frame.script.code[frame.index][1],
    frame.args,frame.arg_types,vm.stack,types,aliases,frame.wants_result
end
"""


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    entries={r["sample"]:r["entries"]for r in
        json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    before=source_identity()
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],"failures":[],
        "scope":"real call_sub dispatch, frame construction, target-script precedence, original function resolution, "
                "typed parameter ownership, target return and discarded return",
        "host_boundaries":["bounded guest allocation","single-thread CRT locks","verified filename lookup boundary",
                           "source-load boundary with preloaded script tables","GS cookie verification"],
        "limits":["script resource I/O and MJO loading are validated by separate reader suites",
                  "reset_exec and exec_func cleanup/restart sequencing remain pending",
                  "thread_atend registration and shutdown dispatch remain pending",
                  "unnamed platform/input calls retain missing-provider diagnostics"],
        "complete_engine_semantics":False}
    for sample in samples:
        oracle=ExecutionOracle(sample,entries[sample["sample"]])
        lua,_,_,system=registered_providers()
        mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
        ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
        execute=lua.eval(FIXTURE)
        count=failed=0;arities=set()
        for tags in([], [0],[1],[2],[3],[4],[5],[0,1,2],[5,4,3,2,1,0]):
            for duplicate,fallback in((False,False),(True,False),(False,True)):
                for discard in(False,True):
                    for returning in(False,True):
                        for filename in("callee","CALLEE.mjo"):
                            native,args=[],[filename,0x12345678]
                            for index,tag in enumerate(tags):
                                raw,value=argument(oracle,tag,index);native.append(raw);args.append(value)
                            count+=1;arities.add(len(args));reason=None
                            try:
                                expected=oracle.call_sub(filename,0x12345678,native,
                                    discard,duplicate,fallback,returning=returning)
                                ok,why,depth,name,pc,parameters,types,stack,stack_types,aliases,wants=execute(
                                    mod,ops,system,lua.table_from(args,recursive=True),lua.table_from(tags),
                                    0x12345678,duplicate,fallback,False,returning,discard)
                                if not ok:reason=str(why)
                                elif returning:
                                    if(depth!=1 or name!="caller.mjo"or pc!=8
                                       or expected["pc"]!=expected["caller_code"]+8 or expected["depth"]!=0):
                                        reason="caller continuation differs"
                                    elif len(stack)!=len(expected["stack"]):reason="returned stack depth differs"
                                    else:
                                        for index,(tag,payload)in enumerate(expected["stack"],1):
                                            if tag==2:same=stack[index]==oracle.string_result(tag,payload)
                                            elif tag>=3:same=bool(aliases[index])and payload==native[int(aliases[index])-3][1]
                                            else:same=stack[index]==native_number(tag,payload)
                                            if not same or stack_types[index]!=tag:reason="returned typed value differs";break
                                else:
                                    target="caller.mjo"if fallback else"callee.mjo"
                                    target_pc=expected["caller_code"]if fallback else expected["callee_code"]
                                    if expected["pc"]!=target_pc+100 or name!=target or pc!=100 or depth!=2:
                                        reason="target script/function precedence differs"
                                    elif wants!=(not discard):reason="return disposal frame flag differs"
                                    elif expected["stack"]!=[(0,71)]or list(stack.values())!=[71]:
                                        reason="caller operand stack differs"
                                    elif expected["parameters"]!=native:reason="native parameter positions differ"
                                    elif list(types.values())!=tags or len(parameters)!=len(tags):
                                        reason="portable parameter tags or count differ"
                                if expected["loaded_files"]!=[filename]:reason="source filename boundary differs"
                            except Exception as error:reason=str(error)
                            if reason:
                                failed+=1
                                if len(report["failures"])<30:report["failures"].append({
                                    "sample":sample["sample"],"tags":tags,"duplicate":duplicate,
                                    "fallback":fallback,"discard":discard,"returning":returning,
                                    "file":filename,"reason":reason})
        for discard in(False,True):
            count+=1;reason=None
            try:
                oracle.call_sub("callee",0x12345678,discard=discard,missing=True)
                reason="missing function did not reach original diagnostic"
            except OriginalDiagnostic:
                ok,why,*_=execute(mod,ops,system,lua.table_from(["callee",0x12345678]),lua.table(),
                    0x12345678,False,False,True,False,discard)
                if ok or"script function missing"not in str(why):
                    reason="missing function did not preserve portable diagnostic"
            except Exception as error:reason=str(error)
            if reason:
                failed+=1;report["failures"].append({"sample":sample["sample"],"missing":True,
                                                    "discard":discard,"reason":reason})
        calls=[{"name":"$call_sub","hash":"9627ddc3","cases":count,"failures":failed,
                "status":"passed"if not failed else"failed","argument_counts":sorted(arities),
                "discard_modes":[False,True]}]
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],"calls":calls})
        print(f"{sample['sample']}: {count} hashed script calls, {failed} mismatches",flush=True)
    report["comparisons"]=sum(c["cases"]for s in report["samples"]for c in s["calls"])
    report["mismatches"]=sum(c["failures"]for s in report["samples"]for c in s["calls"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py","native_function.py",
                    "original_execution_remaining.py","test_execution_remaining.py","test_argument_semantics.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("execution_remaining")
    write_json(folder/(folder.name[:8]+"_execution_remaining.json"),report)
    write_json(ROOT/"reports/execution_remaining_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
