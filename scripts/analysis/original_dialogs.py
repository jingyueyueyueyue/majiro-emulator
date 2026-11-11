"""Recover original dialog formatting and terminal Win32 dialog boundaries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys

import capstone
import unicorn
from unicorn import x86_const as reg

from native_function import NativeFunctions
from original_text import TextOracle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir


class DialogOracle(TextOracle):
    def __init__(self, sample, entries):
        super().__init__(sample,entries)
        self.entries={row["name"]:row for row in entries if row["name"]}
        self.functions=NativeFunctions(self)
        self.dialogs=[]
        self.host_events=[]
        self.answer=1
        self.caption=b"Majiro reference fixture"
        f=self.functions
        # Persistent diagnostic-log output is a separate host feature. Keep
        # the error dialog's guard and displayed-message formatter original.
        ordinary=f.syscall_helper(self.entries["$dialog_ok"])
        concatenators=[]
        ordinary_roots=[ordinary]
        for _ in range(2):
            following=[]
            for candidate in ordinary_roots:
                for call in f.calls(candidate):
                    target=call.operands[0].imm;body=f.body(target)
                    if any(row.mnemonic=="cmp"and row.operands[-1].type==capstone.CS_OP_IMM
                           and row.operands[-1].imm==5 for row in body)and any(
                           row.mnemonic=="jmp"and row.operands[0].type==capstone.CS_OP_MEM
                           and row.operands[0].mem.scale==4 for row in body):
                        concatenators.append(target)
                    following.append(target)
            if concatenators:break
            ordinary_roots=following
        if len(set(concatenators))!=1:raise ValueError("original dialog concatenator changed")
        self.concatenate_va=concatenators[0]
        error=f.syscall_helper(self.entries["$dialog_error_ok"])
        error_body=f.body(error)
        guard=[]
        for call in f.calls(error):
            body=f.body(call.operands[0].imm)
            if(len(body)==2 and body[0].mnemonic=="mov"and body[1].mnemonic=="ret"
               and body[0].operands[1].type==capstone.CS_OP_MEM
               and not body[0].operands[1].mem.base):
                # The guard getter precedes logging/window/title getters.
                guard.append(body[0].operands[1].mem.disp)
                break
        if not guard:
            guard=[row.operands[0].mem.disp for row in error_body if row.mnemonic=="cmp"
                and row.operands[0].type==capstone.CS_OP_MEM and not row.operands[0].mem.base
                and row.operands[1].type==capstone.CS_OP_IMM and row.operands[1].imm==1]
        if len(set(guard))!=1:raise ValueError("original production dialog guard changed")
        self.mode_slot=guard[0]
        logs=[]
        ordinary_calls={row.operands[0].imm for row in f.calls(ordinary)}
        for call in f.calls(error):
            target=call.operands[0].imm
            if target in ordinary_calls or target in(self.concatenate_va,ordinary):continue
            if any(row.operands[0].imm==self.concatenate_va for row in f.calls(target)):
                logs.append(target)
        if len(set(logs))>1:raise ValueError("original error diagnostic logging boundary changed")
        if logs:self.log_va=logs[0]
        else:
            self.log_va=None
            # Older builds inline the log writer. Intercept its actual fopen
            # boundary with an unavailable fixture stream; argument formatting
            # and the inline guard still execute.
            candidates=[]
            for index,row in enumerate(error_body):
                if row.mnemonic!="push"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                try:raw=self._input(row.operands[0].imm,-1).value
                except Exception:continue
                if raw not in(b"a",b"at",b"a+t"):continue
                calls=[item.operands[0].imm for item in error_body[index+1:]if item.mnemonic=="call"
                       and item.operands[0].type==capstone.CS_OP_IMM]
                if len(calls)>=2:candidates.append(calls[1])
            if len(set(candidates))!=1:raise ValueError("original inline diagnostic file open changed")
            self.log_va=candidates[0]
        def log_boundary(uc,at,size,unused):
            self.host_events.append({"boundary":"diagnostic_log"})
            self._return(0,0)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,log_boundary,begin=self.log_va,end=self.log_va)
        pending=[f.syscall_helper(self.entries["$rand"])]
        seen=set();tls=set()
        for _ in range(4):
            following=[]
            for target in pending:
                if target in seen:continue
                seen.add(target);body=f.body(target)
                if any(row.mnemonic=="imul"and"0x343fd"in row.op_str for row in body):
                    calls=[row.operands[0].imm for row in body if row.mnemonic=="call"]
                    if calls:tls.add(calls[0])
                else:following.extend(row.operands[0].imm for row in body if row.mnemonic=="call"
                                      and row.operands[0].type==capstone.CS_OP_IMM)
            if tls:break
            pending=following
        if len(tls)>1:raise ValueError("original dialog CRT thread storage changed")
        self.thread_data=self.DATA+0x60000
        def thread_data(uc,at,size,unused):self._return(0,self.thread_data)
        for target in tls:self.machine.hook_add(unicorn.UC_HOOK_CODE,thread_data,begin=target,end=target)

    def _import(self,uc,at,size,unused):
        name=self.import_names.get(at)
        esp=uc.reg_read(reg.UC_X86_REG_ESP)
        counts={"GdiFlush":0,"SetForegroundWindow":1,"ShowWindow":2,"UpdateWindow":1,
                "SetActiveWindow":1,"GetActiveWindow":0,"GetForegroundWindow":0,
                "SetFocus":1,"EnableWindow":2,"SetCursor":1}
        if name in counts:
            count=counts[name]
            args=list(struct.unpack("<"+"I"*count,uc.mem_read(esp+4,count*4)))if count else[]
            self.host_events.append({"api":name,"arguments":args})
            self._return(count,1);return
        if name=="GetWindowTextA":
            window,output,capacity=struct.unpack("<3I",uc.mem_read(esp+4,12))
            raw=self.caption[:max(0,capacity-1)]
            if capacity:uc.mem_write(output,raw+b"\0")
            self.host_events.append({"api":name,"window":window})
            self._return(3,len(raw));return
        if name=="MessageBoxA":
            window,text,title,flags=struct.unpack("<4I",uc.mem_read(esp+4,16))
            message=self._input(text,-1).value
            caption=self._input(title,-1).value if title else b""
            self.dialogs.append({"message_hex":message.hex(),"message":message.decode("cp932"),
                "caption_hex":caption.hex(),"caption":caption.decode("cp932"),
                "flags":flags,"buttons":flags&15,"icon":flags&0xf0,"window":window,
                "answer":self.answer})
            self._return(4,self.answer);return
        return super()._import(uc,at,size,unused)

    def call_dialog(self,name,args,discard=False,mode=0):
        self.dialogs=[];self.host_events=[]
        self.write_u32(self.mode_slot,mode)
        values=[]
        for index,value in enumerate(args):
            if isinstance(value,str):values.append((2,self.string(value,index)))
            elif isinstance(value,dict)and value.get("native_type")in(3,4,5):
                # Original concatenation emits a type label without reading
                # the array payload; a bounded valid object is still provided.
                pointer=self.DATA+0x50000+index*0x100
                self.machine.mem_write(pointer,struct.pack("<6I",64,0,1,1,1,0))
                values.append((value["native_type"],pointer))
            elif isinstance(value,float):values.append((1,struct.unpack("<I",struct.pack("<f",value))[0]))
            else:values.append((0,value&0xffffffff))
        entry=self.entries[name]
        result=self.execute("syscallp"if discard else"syscall",list(reversed(values)),
            struct.pack("<IH",int(entry["hash"],16),len(values)),budget=2_000_000)
        return {"stack":result,"dialogs":list(self.dialogs),"host_events":list(self.host_events)}


def inspect():
    parser=argparse.ArgumentParser()
    parser.add_argument("--sample",default="RURINOKASANE")
    parser.add_argument("--name",default="$dialog_ok")
    args=parser.parse_args()
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
    sample=next(row for row in samples if row["sample"]==args.sample)
    entries=next(row["entries"]for row in dispatch if row["sample"]==args.sample)
    oracle=DialogOracle(sample,entries)
    root=oracle.functions.syscall_helper(oracle.entries[args.name])
    lines=[]
    for at in [root]+[row.operands[0].imm for row in oracle.functions.calls(root)]:
        lines.append("FUNCTION "+hex(at))
        for row in oracle.functions.body(at):
            lines.append(f"{row.address:08x} {row.mnemonic} {row.op_str}")
    folder=archive_dir("dialogs_recovery")
    path=folder/(folder.name[:8]+"_disassembly.txt")
    path.write_text("\n".join(lines)+"\n",encoding="utf8")
    print(path.relative_to(ROOT));print("\n".join(lines))


if __name__=="__main__":inspect()
