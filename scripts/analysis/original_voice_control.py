"""Execute original v-tag parsing, API formatting and one-shot consumption.

The downstream voice routine is a declared boundary here. Its playback
semantics must be validated separately instead of inferred from these tests.
"""
from __future__ import annotations

import struct

import capstone
import unicorn
from unicorn import x86_const as reg

from original_opcode import native_number
from original_voice_settings import VoiceSettingsOracle


class VoiceControlOracle(VoiceSettingsOracle):
    def __init__(self,sample,entries):
        super().__init__(sample,entries)
        f=self.functions
        body=f.body(self.parser_cases["v"],minimum_address=self.parser_va)
        calls=[row for row in body if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM]
        if len(calls)!=2:raise ValueError("original voice control call structure changed")
        self.voice_va=calls[0].operands[0].imm
        before=[row for row in body if row.address<calls[0].address]
        pushes=[row for row in before if row.mnemonic=="push"]
        if len(pushes)not in(3,5):raise ValueError("original voice control calling convention changed")
        self.voice_fastcall=len(pushes)==3
        default=f.body(calls[1].operands[0].imm)
        reads=[row.operands[1].mem.disp for row in default
            if row.mnemonic=="mov"and row.operands[1].type==capstone.CS_OP_MEM
            and not row.operands[1].mem.base and not row.operands[1].mem.index]
        if len(set(reads))!=1:raise ValueError("original default voice skip field changed")
        self.skip_default_slot=reads[0]
        self.voice_requests=[]
        def voice(uc,at,size,unused):
            stack=uc.reg_read(reg.UC_X86_REG_ESP)
            if self.voice_fastcall:
                filename,force=uc.reg_read(reg.UC_X86_REG_ECX),uc.reg_read(reg.UC_X86_REG_EDX)
                mode,male,skip=struct.unpack("<3I",uc.mem_read(stack+4,12))
            else:filename,force,mode,male,skip=struct.unpack("<5I",uc.mem_read(stack+4,20))
            filename=self._input(filename,-1).value.decode("cp932")
            self.voice_requests.append({"filename":filename,"force":native_number(0,force),
                "mode":native_number(0,mode),"male":native_number(0,male),"skip":native_number(0,skip)})
            self._return(0)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,voice,begin=self.voice_va,end=self.voice_va)
        wrapper=f.body(f.syscall_helper(self.entries["$voice"]))
        self.guard_va=next(row.operands[0].imm for row in wrapper
            if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM)
        self.disabled=False
        def guard(uc,at,size,unused):self._return(0,int(self.disabled))
        self.machine.hook_add(unicorn.UC_HOOK_CODE,guard,begin=self.guard_va,end=self.guard_va)
        prior=[]
        for row in self.entry_body("$voice_get_fn$"):
            if row.mnemonic=="call":break
            prior.append(row)
        buffers={operand.imm for row in prior for operand in row.operands
                 if operand.type==capstone.CS_OP_IMM and operand.imm>=0x400000}
        if len(buffers)!=1:raise ValueError("original API voice filename buffer changed")
        self.requested_buffer=buffers.pop()

    def reset_control(self,male=0,skip=0,default_skip=0,disabled=False):
        self.voice_requests=[];self.disabled=disabled
        self.write_u32(self.scalars["$set_voice_male"],male)
        self.write_u32(self.scalars["$set_voice_skip"],skip)
        self.write_u32(self.skip_default_slot,default_skip)
        self.machine.mem_write(self.requested_buffer,b"prior\0")

    def control_state(self):
        return{"requests":list(self.voice_requests),"male":self.scalar("$set_voice_male"),
            "skip":self.scalar("$set_voice_skip"),
            "requested":self._input(self.requested_buffer,-1).value.decode("cp932")}

    def control(self,tag):
        self.instruction("ctrl",tag)
        return self.control_state()
