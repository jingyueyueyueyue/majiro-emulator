"""Original voice routing with real resolver, stream stop and gain arithmetic."""
from __future__ import annotations

import struct

import capstone
import unicorn
from unicorn import x86_const as reg

from original_extsound import ExtSoundOracle
from original_opcode import native_number
from original_voice_control import VoiceControlOracle


class VoicePlaybackOracle(ExtSoundOracle):
    def __init__(self,sample,entries):
        super().__init__(sample,entries)
        recovered=VoiceControlOracle(sample,entries)
        self.voice_va=recovered.voice_va;self.voice_fastcall=recovered.voice_fastcall
        self.female_slot=recovered.female_slot
        f=self.functions
        stop=f.body(f.syscall_helper(self.entries["$voice_stop"]))
        handles={row.operands[0].mem.disp for row in stop if row.mnemonic=="mov"
            and row.operands[0].type==capstone.CS_OP_MEM and not row.operands[0].mem.base
            and row.operands[1].type==capstone.CS_OP_IMM and row.operands[1].imm==0}
        gains={row.operands[0].mem.disp for row in stop if row.mnemonic=="mov"
            and row.operands[0].type==capstone.CS_OP_MEM and not row.operands[0].mem.base
            and row.operands[1].type==capstone.CS_OP_IMM and row.operands[1].imm==1000}
        if len(handles)!=1 or len(gains)!=1:raise ValueError("original voice handle/gain recovery changed")
        self.voice_slot=handles.pop();self.gain_slot=gains.pop()
        core=f.body(self.voice_va);transports=[]
        for index,row in enumerate(core):
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            after=[]
            for following in core[index+1:index+9]:
                if following.mnemonic=="call":break
                after.append(following)
            if any(item.mnemonic=="mov"and item.operands[0].type==capstone.CS_OP_MEM
                and not item.operands[0].mem.base and item.operands[0].mem.disp==self.voice_slot
                and item.operands[1].type==capstone.CS_OP_REG
                and item.operands[1].reg==capstone.x86.X86_REG_EAX for item in after):
                transports.append(row.operands[0].imm)
        if len(transports)!=1:raise ValueError("original voice decoder entry is ambiguous")
        self.voice_decoder_va=transports[0];self.voice_plays=[]
        if self.voice_decoder_va==self.play_va:self.machine.hook_del(self.play_hook)
        def play(uc,at,size,unused):
            stack=uc.reg_read(reg.UC_X86_REG_ESP)
            if self.voice_fastcall:
                filename=uc.reg_read(reg.UC_X86_REG_ECX);gain=self.read_u32(stack+4)
            else:filename,gain=struct.unpack("<2I",uc.mem_read(stack+4,8))
            name=self._input(filename,-1).value.decode("cp932")
            self.voice_plays.append({"filename":name,"gain":native_number(0,gain)})
            self._return(0,self.transport_handle)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,play,begin=self.voice_decoder_va,end=self.voice_decoder_va)
        fast_body=recovered.entry_body("$is_fast_mode")
        entry=self.entries["$is_fast_mode"]["selected_va"]
        first=f.instruction(entry)
        if first.mnemonic=="call":self.fast_va=first.operands[0].imm
        else:
            references={operand.mem.disp for row in fast_body for operand in row.operands
                if operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index}
            matches=[]
            for row in core:
                if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                at=row.operands[0].imm;leading=f.instruction(at)
                if not any(operand.type==capstone.CS_OP_MEM and not operand.mem.base
                    and operand.mem.disp in references for operand in leading.operands):continue
                uses={operand.mem.disp for item in f.body(at)for operand in item.operands
                    if operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index}
                if len(uses&references)>=2:matches.append(at)
            if len(set(matches))!=1:raise ValueError("original fast-mode input boundary changed")
            self.fast_va=matches[0]
        self.fast=False
        def fast(uc,at,size,unused):self._return(0,int(self.fast))
        self.machine.hook_add(unicorn.UC_HOOK_CODE,fast,begin=self.fast_va,end=self.fast_va)
        self.cached_slots=set();simple=[]
        for row in core:
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            at=row.operands[0].imm
            first=f.instruction(at)
            if not(first.mnemonic=="mov"and first.op_str.startswith("eax,")
                   and first.operands[1].type==capstone.CS_OP_MEM
                   and not first.operands[1].mem.base and not first.operands[1].mem.index):continue
            body=f.body(at)
            if len(body)==2 and body[-1].mnemonic=="ret":
                simple.append((at,first.operands[1].mem.disp))
            elif any(item.mnemonic=="cmp"and item.op_str=="eax, -1"for item in body[:5]):
                self.cached_slots.add(first.operands[1].mem.disp)
        # Recover runtime master and gender fields from the pre-stop branch.
        first_stop=next(row.address for row in core if row.mnemonic=="mov"
            and row.operands[0].type==capstone.CS_OP_MEM
            and not row.operands[0].mem.base and row.operands[0].mem.disp==self.voice_slot)
        prefix=[row for row in core if row.address<first_stop]
        groups=[]
        for row in prefix:
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            body=f.body(row.operands[0].imm)
            if any(item.mnemonic=="call"for item in body)or len(body)>12:continue
            reads={operand.mem.disp for item in body for operand in item.operands
                   if operand.type==capstone.CS_OP_MEM and not operand.mem.base
                   and not operand.mem.index and operand.mem.disp not in self.iat}
            if reads:groups.append(reads)
        if self.voice_fastcall:
            # Optimized build inlines all three loads after computing skip.
            began=False;reads=[]
            for row in prefix:
                if row.mnemonic=="sete":began=True
                if not began:continue
                for operand in row.operands:
                    if(operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                       and operand.mem.disp not in self.iat and operand.mem.disp!=self.voice_slot):
                        reads.append(operand.mem.disp)
            reads=list(dict.fromkeys(reads))
            if len(reads)!=3 or reads[2]!=self.female_slot:
                raise ValueError("original inline voice gender/master fields changed")
            self.voice_master_slot,self.male_slot=reads[:2]
        else:
            single=[next(iter(group))for group in groups if len(group)==1]
            union=set().union(*groups)
            if len(union)!=3 or self.female_slot not in union:
                raise ValueError(f"original voice gender/master fields changed: {groups}")
            if len(single)==1:self.voice_master_slot=single[0]
            elif len(single)==3:
                # Old builds call male, female, then the master scalar.
                self.voice_master_slot=single[-1]
            else:raise ValueError("original voice master scalar is ambiguous")
            self.male_slot=next(iter(union-{self.female_slot,self.voice_master_slot}))
        # The unchanged post-play gain read is the previous-volume field.
        previous=[]
        for index,row in enumerate(core):
            if(row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM
               and not row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_REG):
                destination=row.operands[0].mem.disp
                if destination in(self.voice_slot,self.gain_slot):continue
                earlier=core[max(0,index-4):index]
                if any(item.mnemonic=="mov"and item.operands[0].type==capstone.CS_OP_REG
                    and item.operands[0].reg==row.operands[1].reg
                    and item.operands[1].type==capstone.CS_OP_MEM
                    and item.operands[1].mem.disp==self.gain_slot for item in earlier):
                    previous.append(destination)
        if len(set(previous))>1:raise ValueError("original previous voice gain is ambiguous")
        self.previous_gain_slot=previous[0]if previous else None

    def run_voice(self,filename,force=0,mode=1,male=0,skip=0,files=("voice.wav",),
                  master=1000,gain=1000,male_enabled=1,female_enabled=1,fast=False,previous=True):
        self.fast=fast;self.files={name.lower()for name in files};self.voice_plays=[];self.lookups=[]
        self.reset_music(previous)
        self.write_u32(self.voice_slot,self.music_handle if previous else 0)
        self.write_u32(self.gain_slot,gain)
        if self.previous_gain_slot:self.write_u32(self.previous_gain_slot,321)
        for address in self.cached_slots:self.write_u32(address,0)
        for address in self.vfs_ready:self.write_u32(address,1)
        self.write_u32(self.voice_master_slot,master)
        self.write_u32(self.male_slot,male_enabled);self.write_u32(self.female_slot,female_enabled)
        # Per-call panning/continuation side paths are excluded from this suite.
        uc=self.machine;uc.context_restore(self.initial)
        stack=self.STACK+0xf000;uc.mem_write(self.STACK,bytes(0x10000))
        pointer=self.string(filename,0)+12
        args=[mode,male,skip]if self.voice_fastcall else[pointer,force,mode,male,skip]
        uc.mem_write(stack,struct.pack("<"+"I"*(len(args)+1),self.STOP,
                                      *(value&0xffffffff for value in args)))
        uc.reg_write(reg.UC_X86_REG_ESP,stack);uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
        if self.voice_fastcall:
            uc.reg_write(reg.UC_X86_REG_ECX,pointer);uc.reg_write(reg.UC_X86_REG_EDX,force&0xffffffff)
        uc.emu_start(self.voice_va,self.STOP,count=500000)
        if uc.reg_read(reg.UC_X86_REG_EIP)!=self.STOP:raise RuntimeError("original voice routine exceeded bound")
        return{"plays":list(self.voice_plays),"lookups":list(self.lookups),
            "old_active":previous and bool(self.read_u32(self.stream+self.active_offset)),
            "gain":native_number(0,self.read_u32(self.gain_slot)),
            "previous_gain":native_number(0,self.read_u32(self.previous_gain_slot))
                if self.previous_gain_slot else None}

    def profile(self):
        force=self.run_voice("voice",force=1,fast=True)
        exact=self.run_voice("##nw##",files=("##nw##.wav",),gain=413)
        prefix=self.run_voice("##other",files=("##other.wav",),gain=413)
        untouched=lambda row:not row["plays"]and not row["lookups"]and row["old_active"]and row["gain"]==413
        marker=("prefix"if untouched(prefix)else"exact"if untouched(exact)else"none")
        if marker=="prefix"and not untouched(exact):raise ValueError("unclassified voice marker behavior")
        return{"marker":marker,"force_overrides_skip":bool(force["plays"])}
