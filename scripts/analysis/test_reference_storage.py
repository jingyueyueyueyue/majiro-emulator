"""Original string assignments and array-reference assignment/alias semantics."""
from __future__ import annotations
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import struct
import sys

from audit_player_semantics import registered_providers,source_identity
from original_strings import StringOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    profiles=json.loads((ROOT/"config/player_engine_profiles.json").read_text(encoding="utf-8"))
    before=source_identity()
    report={"schema":1,"timestamp":timestamp(),"samples":[],"failures":[],
        "source_identity":before,"vm_sha256":before["files"]["player/runtime/majiro_vm.lua"],
        "scope":"string local/array assignment, concatenation, expression result and typed array alias assignment",
        "host_boundaries":["initialized CRT","bounded guest heap","Windows NLS"],
        "complete_engine_semantics":False}
    texts=("","A","a","\uff21","\uff41","\u3042","A\u30bd")
    for sample in samples:
        oracle=StringOracle(sample)
        profile=profiles["executables"][sample["exe_sha256"]]
        lua,_,_,system=registered_providers(profile["minmax_arguments"],profile["string_case"],
                                            profile["integer_parse"],profile["empty_search_absent"])
        mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
        ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
        execute=lua.eval("""
        function(mod,ops,strings,opcode,flags,left,right,index,element,array_refs,same,profile)
          local script={name='reference-store',main=0,functions={},
            code={{0,opcode,flags,123,0},{10,0x800,0},{16,0x82b}}}
          local vm=mod.new{opcodes=ops,loader=function()return script end,
            syscall=function()error('unexpected reference-store syscall')end,
            string_ops=strings,numeric_profile=profile}
          vm:begin(script.name)
          if array_refs then
            left={kind='array',dims={2},element_type=element,values={[0]=3,[1]=7}}
            right=same and left or{kind='array',dims={2},element_type=element,values={[0]=8,[1]=9}}
          end
          local container=index and{kind='array',dims={3},element_type=2,
            values={[0]=left,[1]=left,[2]=left}}or nil
          vm:frame().locals[0]=container or left
          vm:push(right)
          if index then vm:push(index,0)end
          local ok,why=pcall(function()vm:step()end)
          local stored=container and container.values[index%3]or vm:frame().locals[0]
          return ok,why,stored,vm.stack,vm.stack_types,stored==right,
            #vm.stack>0 and vm.stack[1]==right
        end
        """)
        counts,failures=Counter(),Counter()
        names=[name for name in oracle.rows if name.startswith(("st.","stp.","stelem.","stelemp."))
               and(name.endswith(".s")or name.endswith("arr"))]
        for name in sorted(names):
            array_refs=name.endswith("arr")
            element=("iarr","rarr","sarr").index(name.split(".")[-1])if array_refs else 2
            indexed=name.startswith("stelem")
            tag=element+3 if array_refs else 2
            flags=96+tag*256+(2048 if indexed else 0)
            pairs=[(None,None,same)for same in(False,True)]if array_refs else[
                (left,right,False)for left,right in itertools.product(texts,repeat=2)]
            for left,right,same in pairs:
                for index in(-1,0,3)if indexed else(None,):
                    if array_refs:
                        left_ptr=oracle.DATA+0x50000;right_ptr=left_ptr if same else left_ptr+0x1000
                        oracle.machine.mem_write(left_ptr,struct.pack("<7I",64,0,1,1,2,3,7))
                        if not same:
                            oracle.machine.mem_write(right_ptr,struct.pack("<7I",64,0,1,1,2,8,9))
                    else:
                        left_ptr,right_ptr=oracle.string(left,0),oracle.string(right,1)
                    values=[(tag,left_ptr),(tag,right_ptr)]
                    array_pointer=oracle.DATA+0x50000
                    if indexed:
                        oracle.machine.mem_write(array_pointer,struct.pack("<8I",64,0,1,1,3,*([left_ptr]*3)))
                        values=[(5,array_pointer),(2,right_ptr),(0,index&0xffffffff)]
                        flags=96+512+2048
                    counts[name]+=1;reason=None
                    try:
                        expected=oracle.execute(name,values,struct.pack("<HIh",flags,123,0),budget=200000)
                        native_stored=oracle.read_u32(array_pointer+20+(index%3)*4)if indexed else expected[0][1]
                        ok,why,stored,stack,types,stored_alias,result_alias=execute(mod,ops,system["strings"],
                            int(oracle.rows[name]["opcode"],16),flags,left,right,index,
                            element,array_refs,same,profile["numeric"])
                        if not ok:reason=str(why)
                        elif len(stack)!=len(expected)-1:reason="expression stack depth differs"
                        elif array_refs:
                            if stored_alias!=(native_stored==right_ptr):reason="stored array alias differs"
                            elif len(stack)and result_alias!=(expected[-1][1]==right_ptr):
                                reason="expression array alias differs"
                        else:
                            if stored!=oracle.string_result(2,native_stored):
                                reason=f"stored string {stored!r} differs"
                            else:
                                for i,(kind,pointer)in enumerate(expected[1:],1):
                                    if types[i]!=kind or stack[i]!=oracle.string_result(kind,pointer):
                                        reason=f"expression string {stack[i]!r} != {oracle.string_result(kind,pointer)!r}"
                                        break
                    except Exception as error:reason=str(error)
                    if reason:
                        failures[name]+=1
                        if failures[name]<=8:report["failures"].append({"sample":sample["sample"],
                            "name":name,"left":left,"right":right,"same_array":same,
                            "index":index,"reason":reason})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "cases":dict(counts),"failures":dict(failures),
            "passed_opcodes":sorted(name for name in counts if not failures[name])})
        print(f"{sample['sample']}: {sum(counts.values())} reference assignments; "
              f"{sum(failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(sum(row["cases"].values())for row in report["samples"])
    report["mismatches"]=sum(sum(row["failures"].values())for row in report["samples"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","test_reference_storage.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("reference_storage")
    write_json(folder/(folder.name[:8]+"_reference_storage.json"),report)
    write_json(ROOT/"reports/reference_storage_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
