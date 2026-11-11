"""Recover original music request flags and real stream-handle lookup."""
from __future__ import annotations

import struct

import capstone
import pefile

from native_function import NativeFunctions
from original_calls import CallOracle


class AudioOracle(CallOracle):
    def __init__(self,sample,entries):
        super().__init__(sample)
        self.functions=NativeFunctions(self)
        self.entries={entry["name"]:entry for entry in entries if entry["name"]}
        pe=pefile.PE(sample["path"])
        self.iat={entry.address:entry.name.decode("ascii")if entry.name else"ordinal"
                  for library in pe.DIRECTORY_ENTRY_IMPORT for entry in library.imports}
        self.critical_calls=0
        self.pause_helper=self.functions.syscall_helper(self.entries["$music_pause"])
        root=self.functions.body(self.pause_helper)
        slots=[]
        for row in root:
            for operand in row.operands:
                if(operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                    and operand.mem.disp not in self.iat
                    and operand.mem.disp not in(self.frame_global,self.context_global)):
                    slots.append(operand.mem.disp)
            if row.mnemonic=="call":break
        slots=list(dict.fromkeys(slots))
        if len(slots)!=1:raise ValueError("original music handle slot is ambiguous")
        self.music_slot=slots[0]
        pending=[self.pause_helper];visited=set();found=[]
        for depth in range(3):
            following=[]
            for address in pending:
                if address in visited:continue
                visited.add(address);rows=self.functions.body(address)
                for index,row in enumerate(rows):
                    if row.mnemonic!="cmp":continue
                    memory=row.operands[0]
                    if(memory.type!=capstone.CS_OP_MEM or not 0x100<=memory.mem.disp<=0x1000
                            or not memory.mem.base):continue
                    base=memory.mem.base
                    loads=[entry for entry in rows[:index]if entry.mnemonic=="mov"
                        and entry.operands[0].type==capstone.CS_OP_REG and entry.operands[0].reg==base
                        and entry.operands[1].type==capstone.CS_OP_MEM
                        and not entry.operands[1].mem.base and not entry.operands[1].mem.index]
                    nexts=[entry.operands[1].mem.disp for entry in rows[index+1:]
                        if entry.mnemonic=="mov"and entry.operands[0].type==capstone.CS_OP_REG
                        and entry.operands[0].reg==base and entry.operands[1].type==capstone.CS_OP_MEM
                        and entry.operands[1].mem.base==base
                        and memory.mem.disp<entry.operands[1].mem.disp<memory.mem.disp+0x200]
                    if loads and len(set(nexts))==1:
                        found.append((loads[-1].operands[1].mem.disp,nexts[0],memory.mem.disp,address))
                following.extend(row.operands[0].imm for row in rows if row.mnemonic=="call"
                                 and row.operands[0].type==capstone.CS_OP_IMM)
            if found:break
            pending=following
        found=list(dict.fromkeys(found))
        if len(found)!=1:raise ValueError(f"original stream registry lookup is ambiguous: {found}")
        self.stream_head,self.next_offset,self.handle_offset,pause_core=found[0]
        writes=[row.operands[0].mem.disp for at in visited|{pause_core}
                for row in self.functions.body(at)
                if row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM
                and row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_IMM
                and row.operands[1].imm==1
                and self.handle_offset<row.operands[0].mem.disp<self.next_offset]
        if len(set(writes))!=1:raise ValueError("original music pause flag is ambiguous")
        self.pause_offset=writes[0];self.resume_offset=self.pause_offset+4
        self.active_offset=self.handle_offset+0x30
        self.stream=self.DATA+0x50000
        self.music_handle=0x12345678

    def _import(self,uc,at,size,unused):
        name=self.import_names.get(at)
        if name in("EnterCriticalSection","LeaveCriticalSection"):
            self.critical_calls+=1
            return self._return(1)
        return super()._import(uc,at,size,unused)

    def reset_music(self,present=True,pause_requested=False,resume_requested=False):
        self.machine.mem_write(self.stream,bytes(0x700))
        self.write_u32(self.stream+self.handle_offset,self.music_handle)
        self.write_u32(self.stream+self.active_offset,1)
        self.write_u32(self.stream+self.pause_offset,int(pause_requested))
        self.write_u32(self.stream+self.resume_offset,int(resume_requested))
        self.write_u32(self.stream_head,self.stream if present else 0)
        self.write_u32(self.music_slot,self.music_handle if present else 0)

    def call_music(self,name,discard=False):
        entry=self.entries[name]
        return self.execute("syscallp"if discard else"syscall",[],
            struct.pack("<IH",int(entry["hash"],16),0),budget=100000)

    def music_requests(self):
        return bool(self.read_u32(self.stream+0x680)),bool(self.read_u32(self.stream+0x684))
