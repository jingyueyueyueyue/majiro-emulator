"""Execute older voice routing without assuming a newer stream-object layout."""
import struct
import capstone
import unicorn
from unicorn import x86_const as reg
from original_text import TextOracle


class LegacyVoiceProfileOracle(TextOracle):
    def __init__(self,sample,entries):
        super().__init__(sample,entries)
        f=self.functions
        control=f.body(self.parser_cases["v"],minimum_address=self.parser_va)
        calls=[row.operands[0].imm for row in control if row.mnemonic=="call"]
        if len(calls)!=2:raise ValueError("legacy voice control routing changed")
        self.voice_va=calls[0]
        body=f.body(self.voice_va);resolver=None;play=None;simple=set()
        stop_api=f.syscall_helper(self.entries["$voice_stop"])
        stop=next(row.operands[0].imm for row in f.body(stop_api)
                  if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM)
        for index,row in enumerate(body):
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            target=row.operands[0].imm
            after=body[index+1:index+7]
            child=f.body(target)
            if len(child)==2 and child[0].mnemonic=="mov"and child[1].mnemonic=="ret":
                simple.add(target)
            if len(child)<=12 and child[0].mnemonic=="mov"and not any(
                    item.mnemonic=="call"for item in child)and any(
                    operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                    for item in child for operand in item.operands):
                simple.add(target)
            if len(child)>2 and child[0].mnemonic=="mov"and child[1].mnemonic=="cmp"and child[1].op_str=="eax, -1":
                simple.add(target)
            if any(item.mnemonic=="test"and item.op_str=="eax, eax"for item in after[:3]):
                prior=body[max(0,index-5):index]
                if sum(item.mnemonic=="push"for item in prior)==2 and any(
                    item.mnemonic=="lea"and"esp"in item.op_str for item in prior):
                    resolver=target
            if any(item.mnemonic=="mov"and item.operands[0].type==capstone.CS_OP_MEM
                   and not item.operands[0].mem.base and item.operands[1].type==capstone.CS_OP_REG
                   and item.operands[1].reg==capstone.x86.X86_REG_EAX for item in after[:3]):
                play=target
        if stop is None or resolver is None or play is None:
            raise ValueError(f"legacy voice host boundaries are ambiguous: {stop}, {resolver}, {play}")
        self.boundaries={"voice":self.voice_va,"stop":stop,"resolve":resolver,"play":play}
        self.played=[];self.stopped=0;self.fast=False
        def constant(uc,at,size,unused):self._return(0,1)
        for at in simple:self.machine.hook_add(unicorn.UC_HOOK_CODE,constant,begin=at,end=at)
        def stop_boundary(uc,at,size,unused):
            self.stopped+=1;self._return(0)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,stop_boundary,begin=stop,end=stop)
        def resolve(uc,at,size,unused):
            output,filename=struct.unpack("<2I",uc.mem_read(uc.reg_read(reg.UC_X86_REG_ESP)+4,8))
            uc.mem_write(output,self._input(filename,-1).value+b"\0");self._return(0,1)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,resolve,begin=resolver,end=resolver)
        def played(uc,at,size,unused):
            self.played.append(True);self._return(0,1234)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,played,begin=play,end=play)
        # The final fast-mode query is a real call after the resolver branch.
        fasts=[]
        for index,row in enumerate(body):
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            if row.operands[0].imm in(simple|{stop,resolver,play}):continue
            after=body[index+1:index+4]
            if any(item.mnemonic=="test"and item.op_str=="eax, eax"for item in after):
                fasts.append(row.operands[0].imm)
        fasts=list(dict.fromkeys(fasts))
        if len(fasts)!=1:raise ValueError(f"legacy voice fast-mode boundary changed: {fasts}; simple={simple}")
        def fast(uc,at,size,unused):self._return(0,int(self.fast))
        self.machine.hook_add(unicorn.UC_HOOK_CODE,fast,begin=fasts[0],end=fasts[0])

    def probe(self,name,force=0,fast=False):
        self.played=[];self.stopped=0;self.fast=fast
        uc=self.machine;uc.context_restore(self.initial)
        stack=self.STACK+0xf000;uc.mem_write(self.STACK,bytes(0x10000))
        uc.mem_write(stack,struct.pack("<6I",self.STOP,self.string(name,0)+12,force,1,0,0))
        uc.reg_write(reg.UC_X86_REG_ESP,stack);uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
        uc.emu_start(self.voice_va,self.STOP,count=100000)
        if uc.reg_read(reg.UC_X86_REG_EIP)!=self.STOP:raise ValueError("legacy voice profile exceeded bound")
        return {"played":bool(self.played),"stop_count":self.stopped}

    def profile(self):
        ordinary=self.probe("voice")
        exact=self.probe("##nw##");prefix=self.probe("##other")
        forced=self.probe("voice",force=1,fast=True)
        if not ordinary["played"]or exact["stop_count"]!=1 or prefix["stop_count"]!=1:
            raise ValueError("legacy voice marker profile is unclassified")
        self.profile_evidence={"ordinary":ordinary,"exact":exact,"prefix":prefix,"forced_fast":forced}
        return {"marker":"none","force_overrides_skip":forced["played"]}
