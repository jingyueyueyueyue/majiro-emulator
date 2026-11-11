"""Execute original call, argcheck, parameter load and return without lookup stubs."""
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
from original_opcode import native_number
from test_argument_semantics import argument
from test_storage_semantics import equal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json

HASH = 0x12345678


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    profiles=json.loads((ROOT/"config/player_engine_profiles.json").read_text(encoding="utf-8"))
    lua=create_lua()
    mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
    execute=lua.eval("""
    function(mod,ops,name,args,tags,location,returning,profile,missing,function_hash)
      local byname={}
      local order={'first.mjo','caller.mjo','last.mjo'}
      for _,filename in ipairs(order)do
        local functions={}
        if filename~='caller.mjo'or location=='current'then functions={{function_hash,100}}end
        if location=='library_duplicates'and filename=='first.mjo'then
          functions[#functions+1]={function_hash,300}
        end
        if missing then functions={}end
        local code={{0,name=='call'and 0x80f or 0x810,function_hash,0,#args},{12,0x191},
          {100,0x836,tags},
          #args>0 and {100+4+#tags,0x802,96+(tags[1]or 0)*256,99,-2}
            or {104,0x800,0},
          {200,0x82b},{300,0x191}}
        byname[filename]={name=filename,main=0,functions=functions,code=code}
      end
      local vm=mod.new{opcodes=ops,loader=function(filename)return byname[filename]end,
        syscall=function()error('unexpected call syscall')end,numeric_profile=profile}
      for _,filename in ipairs(order)do vm:load(filename)end
      vm.status='running';vm:enter(vm:load('caller.mjo'),0,{},false);vm:push(71,0)
      for i=#args,1,-1 do vm:push(args[i],tags[i])end
      local ok,why=pcall(function()
        vm:step()
        if returning then vm:step();vm:step();vm:step()end
      end)
      local frame=vm:frame()
      local aliases={}
      for i,value in ipairs(vm.stack)do
        for j,source in ipairs(args)do if type(source)=='table'and source==value then aliases[i]=j end end
      end
      return ok,why,vm.stack,vm.stack_types,#vm.frames,frame and frame.script.name,
        frame and frame.script.code[frame.index][1],frame and frame.args,aliases
    end
    """)
    report={"schema":1,"timestamp":timestamp(),"samples":[],"failures":[],
        "scope":"real function registry lookup, current-script precedence, duplicate libraries, parameter slots, return and discard",
        "limits":["diagnostics stop before the Windows dialog","dynamic unload and unknown script formats remain separate"],
        "host_boundaries":["bounded guest heap","initialized CRT","stop on original diagnostic"],
        "vm_sha256":hashlib.sha256((ROOT/"player/runtime/majiro_vm.lua").read_bytes()).hexdigest(),
        "complete_engine_semantics":False}
    for sample in samples:
        profile=profile_for(profiles,sample,report)
        if profile is None:
            continue
        oracle=CallOracle(sample)
        profile=profile["numeric"]
        counts,failures,diagnostics=Counter(),Counter(),Counter()
        for name in("call","callp"):
            for tags,function_hash in[(tags,hash_)for tags in(
                [], [0], [1], [2], [3], [4], [5], [0,1,2], [5,4,3,2,1,0])
                for hash_ in(HASH,0x1d128f30,0xb1bb8adc)]:
                for location in("current","library","library_duplicates"):
                    for returning in(False,True):
                        native,args=[],[]
                        for i,tag in enumerate(tags):
                            raw,value=argument(oracle,tag,i);native.append(raw);args.append(value)
                        initial=[(0,71)]+list(reversed(native))
                        caller_base=oracle.operand-2
                        code_bases={"caller.mjo":caller_base,"first.mjo":oracle.DATA+0x34000,
                                    "last.mjo":oracle.DATA+0x36000}
                        target_name="caller.mjo"if location=="current"else"first.mjo"
                        target=code_bases[target_name]+100
                        def setup(source):
                            scripts=[]
                            for filename in("first.mjo","caller.mjo","last.mjo"):
                                functions=[(function_hash,100)]if filename!="caller.mjo"or location=="current"else[]
                                if location=="library_duplicates"and filename=="first.mjo":
                                    functions.append((function_hash,300))
                                scripts.append({"code_base":code_bases[filename],"functions":functions})
                                if functions:
                                    body=struct.pack("<HH",int(source.rows["argcheck"]["opcode"],16),len(tags))+bytes(tags)
                                    if tags:
                                        body+=struct.pack("<HHIh",int(source.rows["ld"]["opcode"],16),96+tags[0]*256,99,-2)
                                    else:body+=struct.pack("<Hi",int(source.rows["ldc.i"]["opcode"],16),0)
                                    body+=struct.pack("<H",int(source.rows["ret"]["opcode"],16))
                                    source.machine.mem_write(code_bases[filename]+100,body)
                            source.registry(scripts)
                        counts[name]+=1;reason=None
                        try:
                            expected=oracle.execute(name,initial,struct.pack("<IIH",function_hash,0,len(tags)),
                                setup=setup,instructions=4 if returning else 1,budget=100000)
                            cache=oracle.read_u32(oracle.operand+4)
                            ok,why,stack,types,depth,script,pc,actual_args,aliases=execute(
                                mod,ops,name,lua.table_from(args,recursive=True),lua.table_from(tags),
                                location,returning,profile,False,function_hash)
                            if not ok:reason=str(why)
                            elif cache!=target:reason="native call cache does not point at selected function"
                            elif returning:
                                if depth!=1 or script!="caller.mjo"or pc!=12:reason="caller continuation differs"
                                elif len(stack)!=len(expected):reason="returned stack depth differs"
                                else:
                                    for i,raw in enumerate(expected,1):
                                        if raw[0]==2:
                                            same=stack[i]==oracle.string_result(*raw)
                                        elif raw[0]>=3:
                                            same=bool(aliases[i])and raw[1]==native[int(aliases[i])-1][1]
                                        else:same=equal(raw,stack[i])
                                        if not same or types[i]!=raw[0]:reason="returned value or native type differs";break
                            else:
                                active=oracle.read_u32(oracle.frame_global)
                                if depth!=2 or script!=target_name or pc!=100:reason="callee selection differs"
                                elif oracle.read_u32(active)!=target or oracle.read_u32(oracle.context)!=1:
                                    reason="native callee context differs"
                                elif oracle.read_u32(active+4)!=len(initial)+1:reason="native parameter base differs"
                                elif len(actual_args)!=len(tags)or len(stack)!=1:reason="portable argument/caller stack differs"
                                elif len(expected)!=1 or expected[0]!=(0,71):reason="native caller stack differs"
                        except Exception as error:reason=str(error)
                        if reason:
                            failures[name]+=1
                            if failures[name]<=10:report["failures"].append({"sample":sample["sample"],
                                "name":name,"function_hash":f"{function_hash:08x}","tags":tags,
                                "location":location,"returning":returning,"reason":reason})
            def missing_setup(source):
                source.registry([{"code_base":source.operand-2,"functions":[]}])
            try:
                oracle.execute(name,[],struct.pack("<IIH",HASH,0,0),setup=missing_setup,budget=100000)
                reason="original did not diagnose an absent function"
            except OriginalDiagnostic:
                ok,why,*_=execute(mod,ops,name,lua.table(),lua.table(),"library",False,profile,True,HASH)
                reason=None if not ok and"unresolved function"in str(why)else"portable missing function is not diagnosed"
                diagnostics[name]+=1
            except Exception as error:reason=str(error)
            counts[name]+=1
            if reason:
                failures[name]+=1;report["failures"].append({"sample":sample["sample"],"name":name,"reason":reason})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "cases":dict(counts),"failures":dict(failures),"matched_source_diagnostics":dict(diagnostics),
            "frame_stride":oracle.frame_stride,"resolver_va":oracle.resolve_va,
            "passed_opcodes":sorted(name for name in counts if not failures[name])})
        print(f"{sample['sample']}: {sum(counts.values())} calls; {sum(failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(sum(row["cases"].values())for row in report["samples"])
    report["mismatches"]=sum(sum(row["failures"].values())for row in report["samples"])
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py",
                    "test_argument_semantics.py","test_storage_semantics.py","test_call_semantics.py")}
    report["status"]="passed"if not report["mismatches"]else"failed"
    finish_scope(report)
    folder=archive_dir("call_semantics")
    write_json(folder/(folder.name[:8]+"_call_semantics.json"),report)
    write_json(ROOT/"reports/call_semantics_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
