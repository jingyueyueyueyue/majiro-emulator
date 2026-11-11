"""Original two-file SE routing with explicit VFS/device/decoder boundaries."""
from __future__ import annotations

import struct
import ctypes

import capstone
import unicorn
from unicorn import x86_const as reg

from original_audio import AudioOracle


class ExtSoundOracle(AudioOracle):
    def __init__(self,sample,entries):
        super().__init__(sample,entries)
        self.files=set();self.disabled=False;self.lookups=[];self.plays=[];self.channel=None
        self.transport_handle=0x76543210
        f=self.functions
        def absolute(operand):
            if operand.type==capstone.CS_OP_IMM:return operand.imm
            if operand.type==capstone.CS_OP_MEM and operand.mem.disp>=0x400000:return operand.mem.disp
            return 0
        pending=[f.syscall_helper(self.entries["$sound_is_exist"])]
        seen=set();resolvers=[]
        for depth in range(3):
            following=[]
            for address in pending:
                if address in seen:continue
                seen.add(address);body=f.body(address);extensions=set()
                for row in body:
                    for operand in row.operands:
                        at=absolute(operand)
                        if 0x400000<=at<0x600000:
                            try:text=bytes(self.machine.mem_read(at,5)).lower()
                            except unicorn.UcError:continue
                            if text.startswith((b".wav\0",b".ogg\0")):extensions.add(text)
                if len(extensions)==2:
                    candidates=set()
                    for row in body:
                        if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                        following_row=f.instruction(row.address+row.size)
                        if following_row.mnemonic=="add"and following_row.op_str.startswith("esp,"):
                            following_row=f.instruction(following_row.address+following_row.size)
                        if following_row.mnemonic=="test"and following_row.op_str=="eax, eax":
                            candidates.add(row.operands[0].imm)
                    if len(candidates)!=1:raise ValueError("original audio VFS predicate is ambiguous")
                    resolvers.append((address,candidates.pop()))
                following.extend(row.operands[0].imm for row in body if row.mnemonic=="call"
                                 and row.operands[0].type==capstone.CS_OP_IMM)
            if resolvers:break
            pending=following
        resolvers=list(dict.fromkeys(resolvers))
        if len(resolvers)!=1:raise ValueError("original WAV/OGG audio resolver is unrecognized")
        self.resolver_va,self.exists_va=resolvers[0]
        resolver_body=f.body(self.resolver_va)
        splitters=set()
        for index,row in enumerate(resolver_body):
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            pushes=[]
            for previous in reversed(resolver_body[:index]):
                if previous.mnemonic=="call":break
                if previous.mnemonic=="push":pushes.insert(0,previous)
                if len(pushes)==5:break
            if len(pushes)==5 and all(pushes[i].operands[0].type==capstone.CS_OP_IMM
                and pushes[i].operands[0].imm==0 for i in(0,2,3)):
                splitters.add(row.operands[0].imm)
        if len(splitters)!=1:raise ValueError("original CRT audio path splitter is ambiguous")
        self.splitpath_va=splitters.pop()
        crt=ctypes.CDLL("msvcrt")
        crt._setmbcp.argtypes=[ctypes.c_int];crt._setmbcp.restype=ctypes.c_int
        if crt._setmbcp(932)!=0:raise RuntimeError("debug CRT CP932 initialization failed")
        crt._splitpath.argtypes=[ctypes.c_char_p,*([ctypes.c_void_p]*4)]
        crt._splitpath.restype=None
        self.host_crt=crt
        def splitpath(uc,at,size,unused):
            stack=uc.reg_read(reg.UC_X86_REG_ESP)
            values=struct.unpack("<5I",uc.mem_read(stack+4,20))
            data=self._input(values[0],-1).raw.split(b"\0",1)[0]
            outputs=[ctypes.create_string_buffer(1024)for _ in range(4)]
            crt._splitpath(data,*outputs)
            for address,output in zip(values[1:],outputs):
                if address:uc.mem_write(address,output.value+b"\0")
            self._return(0)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,splitpath,begin=self.splitpath_va,end=self.splitpath_va)
        compared=set();initialized=set()
        for row in resolver_body:
            if(row.mnemonic=="cmp"and row.operands[0].type==capstone.CS_OP_MEM
               and not row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_IMM
               and row.operands[1].imm==0):compared.add(row.operands[0].mem.disp)
            if(row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM
               and not row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_IMM
               and row.operands[1].imm==1):initialized.add(row.operands[0].mem.disp)
        self.vfs_ready=compared&initialized
        self.exists_fastcall=False
        for row in f.body(self.exists_va):
            if(row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_REG
               and row.operands[1].type==capstone.CS_OP_REG and row.operands[1].reg==capstone.x86.X86_REG_ECX):
                self.exists_fastcall=True
            if row.mnemonic=="call":break
        def exists(uc,at,size,unused):
            address=uc.reg_read(reg.UC_X86_REG_ECX)if self.exists_fastcall else self.read_u32(
                uc.reg_read(reg.UC_X86_REG_ESP)+4)
            name=self._input(address,-1).raw.split(b"\0",1)[0].decode("cp932")
            self.lookups.append(name)
            self._return(0,int(name.lower()in self.files))
        self.machine.hook_add(unicorn.UC_HOOK_CODE,exists,begin=self.exists_va,end=self.exists_va)
        self.channels={};transport=set();envs=set();guards=set()
        for index in range(4):
            suffix=""if index==0 else str(index+1)
            channel="sound"+suffix;name="$extsound"+suffix
            stop=f.syscall_helper(self.entries["$sound"+suffix+"_stop"])
            stop_body=f.body(stop);slot=None;gains=[]
            for row in stop_body:
                if(row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM
                   and not row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_IMM
                   and row.operands[1].imm==1000):gains.append(row.operands[0].mem.disp)
                if slot is None:
                    for operand in row.operands:
                        if(operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                           and operand.mem.disp not in self.iat
                           and operand.mem.disp not in(self.frame_global,self.context_global)):
                            slot=operand.mem.disp;break
            if slot is None or len(gains)!=1:raise ValueError("original SE handle/gain fields changed")
            root=f.syscall_helper(self.entries[name])
            guard=next(row.operands[0].imm for row in f.body(root)if row.mnemonic=="call"
                       and row.operands[0].type==capstone.CS_OP_IMM)
            guards.add(guard)
            pending=[root];seen=set();matches=[]
            for depth in range(3):
                following=[]
                for address in pending:
                    if address in seen:continue
                    seen.add(address);body=f.body(address)
                    for row in body:
                        if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                        next_row=f.instruction(row.address+row.size);result_register=capstone.x86.X86_REG_EAX
                        if(next_row.mnemonic=="mov"and next_row.operands[0].type==capstone.CS_OP_REG
                           and next_row.operands[1].type==capstone.CS_OP_REG
                           and next_row.operands[1].reg==capstone.x86.X86_REG_EAX):
                            result_register=next_row.operands[0].reg
                            next_row=f.instruction(next_row.address+next_row.size)
                        if next_row.mnemonic=="add"and next_row.op_str in("esp, 8","esp, 0x10"):
                            cleanup=next_row.operands[1].imm
                            after=f.instruction(next_row.address+next_row.size)
                            if(after.mnemonic=="mov"and after.operands[0].type==capstone.CS_OP_MEM
                               and not after.operands[0].mem.base and after.operands[0].mem.disp==slot
                               and after.operands[1].type==capstone.CS_OP_REG
                               and after.operands[1].reg==result_register):
                                matches.append((row.operands[0].imm,cleanup,address))
                    following.extend(row.operands[0].imm for row in body if row.mnemonic=="call"
                                     and row.operands[0].type==capstone.CS_OP_IMM)
                if matches:break
                pending=following
            if len(matches)!=1:raise ValueError(f"original {channel} transport boundary is ambiguous: {matches}")
            target,cleanup,lower=matches[0];transport.add((target,cleanup))
            lower_body=f.body(lower);pairs=[]
            for row in lower_body:
                if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM:
                    candidate=row.operands[0].imm
                    if candidate==guard:continue
                    code=f.body(candidate);reads=[];mutates=False;calls=False
                    for item in code:
                        if item.mnemonic=="call":calls=True
                        if(item.mnemonic in("mov","inc","dec")and item.operands[0].type==capstone.CS_OP_MEM
                           and not item.operands[0].mem.base):mutates=True
                        for operand in item.operands:
                            if(operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                               and operand.mem.disp not in self.iat
                               and operand.mem.disp not in(self.frame_global,self.context_global)):
                                reads.append(operand.mem.disp)
                    reads=list(dict.fromkeys(reads))
                    if not calls and not mutates and len(reads)==2:pairs.append(tuple(reads))
                if row.mnemonic.startswith("cmov")and row.operands[-1].type==capstone.CS_OP_MEM:
                    memory=row.operands[-1].mem
                    if memory.base or not memory.disp:continue
                    nearby=[item for item in lower_body if row.address-24<=item.address<=row.address]
                    reads=[]
                    for item in nearby:
                        for operand in item.operands:
                            if(operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                               and operand.mem.disp not in self.iat):reads.append(operand.mem.disp)
                    reads=list(dict.fromkeys(reads))
                    if len(reads)==2:pairs.append(tuple(reads))
            pairs=list(dict.fromkeys(pairs))
            if len(pairs)!=1:raise ValueError(f"original SE master/enable getters are ambiguous: {pairs}")
            envs.add(pairs[0])
            self.channels[channel]={"api":name,"slot":slot,"gain_slot":gains[0]}
        if len(transport)!=1 or len(envs)!=1 or len(guards)!=1:
            raise ValueError("original SE channels do not share the recovered transport contract")
        self.play_va,self.play_cleanup=transport.pop()
        self.master_slot,self.enabled_slot=envs.pop()
        self.disabled_va=guards.pop()
        def disabled(uc,at,size,unused):self._return(0,int(self.disabled))
        self.machine.hook_add(unicorn.UC_HOOK_CODE,disabled,begin=self.disabled_va,end=self.disabled_va)
        def play(uc,at,size,unused):
            stack=uc.reg_read(reg.UC_X86_REG_ESP)
            if self.play_cleanup==8:
                first=uc.reg_read(reg.UC_X86_REG_ECX);second=uc.reg_read(reg.UC_X86_REG_EDX)
                gain,loop=struct.unpack("<2I",uc.mem_read(stack+4,8))
            else:first,second,gain,loop=struct.unpack("<4I",uc.mem_read(stack+4,16))
            first=self._input(first,-1).raw.split(b"\0",1)[0].decode("cp932")
            second=self._input(second,-1).raw.split(b"\0",1)[0].decode("cp932")
            self.plays.append({"channel":self.channel,"first":first,"second":second,
                               "gain":gain,"loop":loop})
            self._return(0,self.transport_handle)
        self.play_hook=self.machine.hook_add(unicorn.UC_HOOK_CODE,play,begin=self.play_va,end=self.play_va)
        if "$is_ultra_fast_mode" not in self.entries:
            self.ultra_slot=None
            return
        entry=self.entries["$is_ultra_fast_mode"];address=entry["selected_va"]
        first=f.instruction(address)
        body=f.body(first.operands[0].imm if first.mnemonic=="call"else address)
        slots=[]
        for row in body:
            for operand in row.operands:
                if(operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                   and operand.mem.disp not in self.iat):slots.append(operand.mem.disp)
            if row.mnemonic=="call":break
        slots=list(dict.fromkeys(slots))
        if len(slots)!=1:raise ValueError("original ultra-fast state getter is ambiguous")
        self.ultra_slot=slots[0]

    def call_extsound(self,channel,first,second,files,previous=True,master=1000,gain=1000,
                      ultra=False,disabled=False,discard=False):
        self.channel=channel;self.files={name.lower()for name in files};self.disabled=disabled
        self.lookups=[];self.plays=[]
        first=self.string(first,0);second=self.string(second,1);info=self.channels[channel]
        self.reset_music(previous)
        def setup(source):
            for address in source.vfs_ready:source.write_u32(address,1)
            source.write_u32(info["slot"],source.music_handle if previous else 0)
            source.write_u32(info["gain_slot"],gain)
            source.write_u32(source.master_slot,master)
            source.write_u32(source.enabled_slot,1)
            if source.ultra_slot is not None:source.write_u32(source.ultra_slot,int(ultra))
        entry=self.entries[info["api"]]
        result=self.execute("syscallp"if discard else"syscall",[(2,second),(2,first)],
            struct.pack("<IH",int(entry["hash"],16),2),setup=setup,budget=300000)
        return{"result":result,"plays":list(self.plays),"lookups":list(self.lookups),
               "old_active":bool(self.read_u32(self.stream+0x650)),
               "handle":self.read_u32(info["slot"]),"next_gain":self.read_u32(info["gain_slot"])}
