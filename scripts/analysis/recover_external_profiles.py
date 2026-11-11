"""Recover original local license-check variants without invoking host checks."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import capstone
import pefile
from original_calls import CallOracle
from native_function import NativeFunctions
from recover_syscall_aliases import wrapper

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,atomic_bytes,lua_value


def recover():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    entries={r["sample"]:r["entries"]for r in
        json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    result={}
    for sample in samples:
        entry=next((e for e in entries[sample["sample"]]if e["hash"]=="87a631a6"),None)
        if not entry:continue
        o=CallOracle(sample);f=NativeFunctions(o);pending=[entry["selected_va"]];seen=set();crypt=False;constant=False
        pe=pefile.PE(sample["path"])
        iat={e.address:e.name for d in pe.DIRECTORY_ENTRY_IMPORT for e in d.imports}
        for _ in range(4):
            following=[]
            for address in pending:
                if address in seen:continue
                seen.add(address)
                body=wrapper(f,address)if address==entry["selected_va"]else f.body(address,maximum_bytes=2048)
                if len(body)==2 and body[0].mnemonic=="xor"and body[0].op_str=="eax, eax"\
                  and body[1].mnemonic=="ret":constant=True
                for row in body:
                    if row.mnemonic!="call":continue
                    operand=row.operands[0]
                    if operand.type==capstone.CS_OP_MEM and iat.get(operand.mem.disp)==b"CryptDecrypt":crypt=True
                    if operand.type==capstone.CS_OP_IMM:following.append(operand.imm)
            pending=following
        if crypt:kind="cryptoapi_user_license"
        elif constant:kind="native_constant_zero"
        elif len(wrapper(f,entry["selected_va"]))==3 and any(
            row.mnemonic=="xor"for row in wrapper(f,entry["selected_va"])):kind="native_constant_zero"
        else:raise ValueError("unrecovered license wrapper: "+sample["sample"])
        result[sample["exe_sha256"]]={"license":kind}
        print(sample["sample"],kind)
    atomic_bytes(ROOT/"player/runtime/majiro_external_profiles.lua",(
        "-- Recovered original local license-check variants; EXE SHA-256 keys.\nreturn "+
        lua_value(result)+"\n").encode("utf8"))
    return result


if __name__=="__main__":recover()
