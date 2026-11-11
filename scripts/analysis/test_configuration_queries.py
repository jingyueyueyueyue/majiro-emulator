"""Read original configuration getters through real syscall dispatch."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

import capstone

from audit_player_semantics import registered_providers, source_identity
from original_calls import CallOracle
from original_opcode import native_number

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json

FIELDS={"$is_talk_male":"ISTALKMALE","$is_talk_female":"ISTALKFEMALE",
        "$is_voice_continue":"ISVOICECONTINUE","$is_voice_panning":"ISVOICEPANNING"}


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name={row["sample"]:row for row in dispatch}
    before=source_identity()
    md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32);md.detail=True
    report={"schema":1,"timestamp":timestamp(),"samples":[],"failures":[],
        "source_identity":before,"complete_engine_semantics":False,"host_calls_replaced":False,
        "scope":"read-only configuration values and production/testplay report-voice guard",
        "limits":["configuration loading itself and external registry mutation are separate validation targets"]}

    def decode(oracle,at,length=200):
        return list(md.disasm(bytes(oracle.machine.mem_read(at,length)),at))

    def scalar_global(oracle,at):
        rows=decode(oracle,at,32)
        if(len(rows)>=2 and rows[0].mnemonic=="mov"and rows[1].mnemonic=="ret"and
           rows[0].operands[1].type==capstone.CS_OP_MEM and not rows[0].operands[1].mem.base):
            return rows[0].operands[1].mem.disp
        return None

    for sample in samples:
        oracle=CallOracle(sample);calls=[]
        for entry in by_name[sample["sample"]]["entries"]:
            name=entry["name"]
            if name not in FIELDS and name!="$is_report_no_voice":continue
            at=entry["selected_va"];rows=decode(oracle,at,40)
            if rows[0].mnemonic=="call":at=rows[0].operands[0].imm
            rows=decode(oracle,at,200)
            failed=count=0
            references=[]
            if name in FIELDS:
                for row in rows:
                    for operand in row.operands:
                        if(operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                           and operand.mem.disp not in(oracle.frame_global,oracle.context_global)):
                            references.append(operand.mem.disp)
                    if row.mnemonic in("ret","jmp"):break
                references=list(dict.fromkeys(references))
                if len(references)!=1:raise ValueError(f"{sample['sample']} {name}: ambiguous configuration slot")
                fixtures=[(None,value)for value in(-2147483648,-1,0,1,7)]
            else:
                getters=[];returns=0
                for row in rows:
                    if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM:
                        slot=scalar_global(oracle,row.operands[0].imm)
                        if slot is not None and slot not in getters:getters.append(slot)
                    if row.mnemonic=="ret":returns+=1
                    if row.mnemonic=="int3"or returns==2:break
                if not getters:
                    mode_slot=value_slot=None
                    for row in rows:
                        if(row.mnemonic=="mov"and len(row.operands)==2 and
                           row.operands[1].type==capstone.CS_OP_MEM and not row.operands[1].mem.base):
                            value_slot=row.operands[1].mem.disp
                        if(row.mnemonic=="cmp"and row.operands[0].type==capstone.CS_OP_MEM
                           and not row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_IMM
                           and row.operands[1].imm==1):
                            mode_slot=row.operands[0].mem.disp
                        if row.mnemonic=="jmp":break
                    if mode_slot and value_slot:getters=[mode_slot,value_slot]
                if len(getters)!=2:raise ValueError(f"{sample['sample']}: report-voice getters changed: {getters}")
                references=getters
                fixtures=[(mode,value)for mode in(-1,0,1,2)for value in(-1,0,1,7)]
            for mode,value in fixtures:
                count+=2;reason=None
                override={"environment":{FIELDS[name]:str(value)}}if name in FIELDS else{
                    "testplay":mode!=1,"report_no_voice":value}
                lua,_,_,system=registered_providers(overrides=override)
                def setup(source):
                    for address,number in zip(references,[value]if mode is None else[mode,value]):
                        source.write_u32(address,number)
                try:
                    expected=oracle.execute("syscall",[],struct.pack("<IH",int(entry["hash"],16),0),
                        setup=setup,budget=100000)
                    discarded=oracle.execute("syscallp",[],struct.pack("<IH",int(entry["hash"],16),0),
                        setup=setup,budget=100000)
                    provider=system["providers"][name]
                    if provider is None:raise RuntimeError("configuration provider missing")
                    actual=provider(lua.table(),lua.table())
                    if discarded:reason="discarded configuration result left a native stack value"
                    elif len(expected)!=1 or actual!=native_number(*expected[0]):
                        reason=f"configuration result {actual} != {expected}"
                except Exception as error:reason=str(error)
                if reason:
                    failed+=1;report["failures"].append({"sample":sample["sample"],"name":name,
                        "mode":mode,"configured":value,"reason":reason})
            calls.append({"name":name,"hash":entry["hash"],"cases":count,"failures":failed,
                "status":"passed"if not failed else"failed","argument_counts":[0],"discard_modes":[False,True],
                "native_configuration_slots":references})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],"calls":calls})
        print(f"{sample['sample']}: {sum(row['cases']for row in calls)} configuration queries; "
              f"{sum(row['failures']for row in calls)} mismatches",flush=True)
    report["comparisons"]=sum(row["cases"]for sample in report["samples"]for row in sample["calls"])
    report["mismatches"]=sum(row["failures"]for sample in report["samples"]for row in sample["calls"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py","test_configuration_queries.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("configuration_queries")
    write_json(folder/(folder.name[:8]+"_configuration_queries.json"),report)
    write_json(ROOT/"reports/configuration_query_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
