"""Batch original scalar configuration and sprite-scale query comparisons."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

import capstone

from audit_player_semantics import registered_providers, source_identity
from original_calls import CallOracle
from original_opcode import f32_bits, native_number
from native_function import NativeFunctions

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,sha256,timestamp,write_json


def absolute(operand):
    return operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index


def run():
    before=source_identity()
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch={row["sample"]:row["entries"]for row in
        json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],
        "failures":[],"complete_engine_semantics":False,
        "scope":"two configuration scalar getters and two live-list sprite float getters",
        "limits":["configuration UI mutations and original registry initialization remain separate",
                  "sprite interpolation timing and invalid-handle diagnostic UI remain separate"]}
    for sample in samples:
        oracle=CallOracle(sample);f=NativeFunctions(oracle);calls=[]
        for entry in dispatch[sample["sample"]]:
            name=entry["name"]
            if name not in("$get_nanido","$get_game_speed","$sprite_xmodify_get%","$sprite_ymodify_get%"):
                continue
            body=f.body(f.syscall_helper(entry))
            fields={}
            if name in("$get_nanido","$get_game_speed"):
                at=entry["selected_va"];wrapper=[]
                for _ in range(24):
                    ins=f.instruction(at);wrapper.append(ins);at+=ins.size
                    if ins.mnemonic in("ret","jmp"):break
                candidates=[row.operands[1].mem.disp for row in wrapper if row.mnemonic=="mov"
                    and len(row.operands)==2 and absolute(row.operands[1])
                    and row.operands[1].mem.disp not in(oracle.frame_global,oracle.context_global)]
                for row in body if not candidates else[]:
                    code=f.body(row.operands[0].imm)if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM else[row]
                    if len(code)>2:continue
                    candidates.extend(item.operands[1].mem.disp for item in code
                        if item.mnemonic=="mov"and len(item.operands)==2 and absolute(item.operands[1]))
                candidates=list(dict.fromkeys(candidates))
                if len(candidates)!=1:raise ValueError("ambiguous original configuration getter: "+name)
                fields["scalar"]=candidates[0]
                fixtures=[(True,value)for value in(-2147483648,-1,0,1,2,4,2147483647)]
            else:
                heads=[row.operands[1].mem.disp for row in body if row.mnemonic=="mov"
                    and len(row.operands)==2 and absolute(row.operands[1])
                    and row.operands[1].mem.disp not in(oracle.frame_global,oracle.context_global)]
                offsets=[operand.mem.disp for row in body if row.mnemonic in("movss","fld")
                    for operand in row.operands if operand.type==capstone.CS_OP_MEM and operand.mem.base
                    and operand.mem.base not in(capstone.x86.X86_REG_ESP,capstone.x86.X86_REG_EBP)]
                if len(set(heads))!=1 or len(set(offsets))!=1:
                    raise ValueError("ambiguous original sprite scale fields: "+name)
                fields.update(head=heads[0],value=offsets[0])
                fixtures=[(present,value)for present in(False,True)for value in(-2.,-0.,.25,1.,3.5)]
                oracle.diagnostic_sink=lambda text:True
            count=0
            for present,value in fixtures:
                for discard in(False,True):
                    count+=1
                    lua,_,_,system=registered_providers()
                    vm=lua.table()
                    if "scalar"in fields:
                        oracle.write_u32(fields["scalar"],value)
                        system["state"]["settings"]["nanido"if name=="$get_nanido"else"game_speed"]=value
                        args=[]
                    else:
                        pointer=oracle.DATA+0x50000
                        oracle.machine.mem_write(pointer,bytes(0x1000))
                        oracle.write_u32(fields["head"],pointer if present else 0)
                        oracle.write_u32(pointer+fields["value"],f32_bits(value))
                        args=[pointer]
                        if present:
                            system["state"]["sprites"][pointer]=lua.table_from(
                                {"id":pointer,"xscale":value,"yscale":value,"tweens":{}},recursive=True)
                    expected=oracle.execute("syscallp"if discard else"syscall",
                        [(0,item)for item in args],struct.pack("<IH",int(entry["hash"],16),len(args)))
                    actual=system["providers"][name](vm,lua.table_from(args))
                    assert expected==([]if discard else[(0,value&0xffffffff)]if "scalar"in fields
                                      else[(1,f32_bits(value if present else 1.))]),(sample["sample"],name,expected)
                    if not discard:assert actual==native_number(*expected[0]),(name,actual,expected)
            calls.append({"name":name,"hash":entry["hash"],"cases":count,"failures":0,"status":"passed",
                "argument_counts":[0 if "scalar"in fields else 1],"discard_modes":[False,True],
                "native_fields":fields})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],"calls":calls})
        print(sample["sample"],sum(row["cases"]for row in calls),"runtime query comparisons passed",flush=True)
    report["comparisons"]=sum(row["cases"]for sample in report["samples"]for row in sample["calls"])
    report["mismatches"]=0
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:sha256(ROOT/"scripts/analysis"/name)for name in(
        "original_opcode.py","original_strings.py","original_calls.py","native_function.py","test_runtime_queries.py")}
    report["status"]="failed"if report["source_changed_during_test"]else"passed"
    folder=archive_dir("runtime_queries")
    write_json(folder/(folder.name[:8]+"_runtime_queries.json"),report)
    write_json(ROOT/"reports/runtime_query_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
