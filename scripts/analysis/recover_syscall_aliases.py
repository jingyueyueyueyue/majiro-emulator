"""Find exact dispatch-wrapper/helper aliases without guessing syscall names."""
from __future__ import annotations
from collections import defaultdict
import json
from pathlib import Path
import sys
import capstone
from native_function import NativeFunctions
from original_calls import CallOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def wrapper(functions,address):
    result=[]
    for _ in range(200):
        row=functions.instruction(address);result.append(row);address+=row.size
        if row.mnemonic in("jmp","ret"):break
    else:raise ValueError("dispatch wrapper exceeds bound")
    return result


def signature(functions,rows,helpers=False):
    output=[]
    addresses={row.address:index for index,row in enumerate(rows)}
    for row in rows:
        operands=[]
        for op in row.operands:
            if op.type==capstone.CS_OP_REG:operands.append(("reg",row.reg_name(op.reg)))
            elif op.type==capstone.CS_OP_MEM:
                m=op.mem;operands.append(("mem",op.size,m.segment,m.base,m.index,m.scale,m.disp))
            elif op.type==capstone.CS_OP_IMM:
                value=op.imm
                if row.mnemonic.startswith("j")and value in addresses:value=("local",addresses[value])
                elif row.mnemonic=="call"and helpers:
                    body=functions.body(value,maximum_bytes=4096)
                    value=("function",tuple(signature(functions,body,False)))
                operands.append(("imm",value))
        output.append((row.mnemonic,tuple(operands)))
    return output


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    entries={s["sample"]:s["entries"]for s in
        json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    result={"schema":1,"timestamp":timestamp(),"samples":[],"unknown_wrappers":[],
        "scope":"exact wrapper operations, argument access, shared branch destinations and called helper bodies",
        "unsupported":"No name or behavior is inferred from a partial helper match."}
    candidates=defaultdict(list);all_unknown=defaultdict(set)
    for sample in samples:
        oracle=CallOracle(sample);f=NativeFunctions(oracle);groups=defaultdict(list)
        failures=[]
        for entry in entries[sample["sample"]]:
            if not entry["name"]:all_unknown[entry["hash"]].add(sample["sample"])
            try:
                rows=wrapper(f,entry["selected_va"])
                key=tuple(signature(f,rows,helpers=True))
                groups[key].append(entry)
                if entry["name"]is None:
                    root=next((row.operands[0].imm for row in rows if row.mnemonic=="call"
                               and row.operands[0].type==capstone.CS_OP_IMM),None)
                    body=f.body(root,maximum_bytes=2048)if root else[]
                    result["unknown_wrappers"].append({"sample":sample["sample"],"hash":entry["hash"],
                        "entry":entry["selected_va"],"wrapper":[f"{r.mnemonic} {r.op_str}"for r in rows],
                        "helper":root,"helper_body":[f"{r.mnemonic} {r.op_str}"for r in body]})
            except(Exception)as error:
                failures.append({"hash":entry["hash"],"name":entry["name"],"error":str(error)})
        aliases=[]
        for group in groups.values():
            named=[e for e in group if e["name"]]
            if not named:continue
            for entry in group:
                if entry["name"]:continue
                for target in named:
                    row={"hash":entry["hash"],"alias":target["name"],"target_hash":target["hash"],
                         "entry":entry["selected_va"],"target_entry":target["selected_va"]}
                    aliases.append(row);candidates[entry["hash"]].append({**row,"sample":sample["sample"]})
        result["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                                  "aliases":aliases,"unclassified":failures})
        print(sample["sample"],len(aliases),"exact aliases;",len(failures),"unclassified wrappers",flush=True)
    accepted={}
    for hash_,matches in candidates.items():
        names={row["alias"]for row in matches};covered={row["sample"]for row in matches}
        if len(names)==1 and covered==all_unknown[hash_]:accepted[hash_]=next(iter(names))
    result["accepted_common_aliases"]=accepted
    result["matched_hashes"]=len(candidates)
    folder=archive_dir("syscall_aliases")
    write_json(folder/(folder.name[:8]+"_syscall_aliases.json"),result)
    write_json(ROOT/"reports/syscall_aliases.json",result)
    print("all-build accepted aliases",accepted)


if __name__=="__main__":run()
