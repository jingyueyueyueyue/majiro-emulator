"""Recover ultra-fast eligibility and its original menu/input boundaries."""
from __future__ import annotations

import struct

import capstone
import unicorn

from original_calls import CallOracle


class ModeOracle(CallOracle):
    def __init__(self,sample,entries):
        super().__init__(sample)
        self.entries={entry["name"]:entry for entry in entries if entry["name"]}
        md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32);md.detail=True
        def decode(at,length=240):
            return list(md.disasm(bytes(self.machine.mem_read(at,length)),at))
        if "$ultra_fast_enable" not in self.entries:
            # Early engines have only AUTO/FAST/FAST_READ. Recover their
            # actual three writes and exercise each setter; an absent ultra
            # interface has no return-value convention to infer.
            def assigned(name, wanted):
                rows=decode(self.entries[name]["selected_va"],80)
                if rows[0].mnemonic=="call":rows=decode(rows[0].operands[0].imm,120)
                found=[]
                for row in rows:
                    if row.mnemonic in("jmp","ret"):break
                    if row.mnemonic!="mov"or len(row.operands)!=2:continue
                    dest,value=row.operands
                    if(dest.type==capstone.CS_OP_MEM and not dest.mem.base and not dest.mem.index
                       and value.type==capstone.CS_OP_IMM and value.imm==wanted):
                        found.append(dest.mem.disp)
                if not found:
                    command=next((row.operands[0].imm for row in rows if row.mnemonic=="push"
                                  and row.operands[0].type==capstone.CS_OP_IMM),None)
                    calls=[row.operands[0].imm for row in rows if row.mnemonic=="call"
                           and row.operands[0].type==capstone.CS_OP_IMM]
                    for target in dict.fromkeys(calls):
                        body=decode(target,80)
                        switch=next((row for row in body if row.mnemonic=="jmp"
                                     and row.operands[0].type==capstone.CS_OP_MEM
                                     and row.operands[0].mem.index
                                     and row.operands[0].mem.scale==4),None)
                        if switch is None or command is None:continue
                        case=self.read_u32(switch.operands[0].mem.disp+command*4)
                        for row in decode(case,80):
                            if row.mnemonic in("ret","jmp"):break
                            if row.mnemonic!="mov"or len(row.operands)!=2:continue
                            dest,value=row.operands
                            if(dest.type==capstone.CS_OP_MEM and not dest.mem.base and not dest.mem.index
                               and value.type==capstone.CS_OP_IMM and value.imm==wanted):
                                found.append(dest.mem.disp)
                found=list(dict.fromkeys(found))
                if len(found)!=1:raise ValueError("early original playback flag is ambiguous: "+name)
                return found[0]
            self.mode_slots={"auto_mode":assigned("$set_auto_mode",1),
                             "fast_mode":assigned("$set_fast_mode_force",1),
                             "fast_read_mode":assigned("$set_fast_mode_ar",1)}
            if len(set(self.mode_slots.values()))!=3:
                raise ValueError("early original playback flags overlap")
            self.allowed_slot=None;self.refresh_va=None;self.keyboard_va=None
            self.refresh_count=0;self.keyboard_count=0
            for name,expected in(("$set_auto_mode","auto_mode"),("$set_fast_mode_force","fast_mode")):
                result=self.call_mode(name,0,{field:False for field in self.mode_slots})
                if result!=[(0,0)]or any(self.read_u32(at)!=(field==expected)
                                        for field,at in self.mode_slots.items()):
                    raise ValueError("early original playback setter calibration failed")
            return
        rows=decode(self.entries["$ultra_fast_enable"]["selected_va"],60)
        allowed=[]
        refresh=None
        for row in rows:
            if(row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM
               and not row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_IMM
               and row.operands[1].imm==1):
                allowed.append(row.operands[0].mem.disp)
            if row.mnemonic=="call":
                refresh=row.operands[0].imm;break
        if len(allowed)!=1 or refresh is None:raise ValueError("original ultra enable layout changed")
        self.allowed_slot=allowed[0]
        self.refresh_va=refresh;self.refresh_count=0;self.keyboard_count=0
        def refresh_hook(uc,at,size,data):
            self.refresh_count+=1
            self._return(0)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,refresh_hook,begin=refresh,end=refresh)
        address=self.entries["$set_ultra_fast_mode"]["selected_va"]
        rows=decode(address,40)
        helper=rows[0].operands[0].imm if rows[0].mnemonic=="call"else address
        body=[];keyboard=None
        for row in decode(helper,240):
            body.append(row)
            if row.mnemonic=="call"and keyboard is None:keyboard=row.operands[0].imm
            if row.mnemonic in("ret","jmp"):break
        def assignments(rows,known=None):
            registers=dict(known or{});writes=[]
            for row in rows:
                if row.mnemonic=="xor"and row.operands[0].reg==row.operands[1].reg:
                    registers[row.operands[0].reg]=0
                elif row.mnemonic=="mov":
                    destination,source=row.operands
                    value=source.imm if source.type==capstone.CS_OP_IMM else registers.get(source.reg)if source.type==capstone.CS_OP_REG else None
                    if destination.type==capstone.CS_OP_REG and value is not None:registers[destination.reg]=value
                    if(destination.type==capstone.CS_OP_MEM and not destination.mem.base and value in(0,1)):
                        writes.append((destination.mem.disp,value))
                if row.mnemonic=="ret":break
            return writes
        writes=assignments(body)
        if len(writes)!=4:
            candidates=[]
            for row in body:
                if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                code=decode(row.operands[0].imm,180)
                jump=next((entry for entry in code if entry.mnemonic=="jmp"
                    and entry.operands[0].type==capstone.CS_OP_MEM
                    and entry.operands[0].mem.index and entry.operands[0].mem.scale==4),None)
                if jump is not None:candidates.append((code,jump.operands[0].mem.disp))
            if len(candidates)!=1:raise ValueError("original ultra mode command is ambiguous")
            code,table=candidates[0]
            known={}
            for row in code:
                if row.mnemonic=="xor"and row.operands[0].reg==row.operands[1].reg:known[row.operands[0].reg]=0
                if row.mnemonic=="jmp":break
            writes=assignments(decode(self.read_u32(table+6*4),120),known)
        if len(writes)!=4 or sorted(value for _,value in writes)!=[0,0,1,1]or keyboard is None:
            raise ValueError("original ultra setter state layout changed")
        def mode_getter(name):
            at=self.entries[name]["selected_va"];rows=decode(at,40)
            if rows[0].mnemonic=="call":rows=decode(rows[0].operands[0].imm,100)
            slots=[]
            for row in rows:
                for operand in row.operands:
                    if(operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                       and operand.mem.disp not in(self.frame_global,self.context_global)):
                        slots.append(operand.mem.disp)
                if row.mnemonic in("jmp","ret"):break
            slots=list(dict.fromkeys(slots))
            if len(slots)!=1:raise ValueError("original mode getter changed: "+name)
            return slots[0]
        def keyboard_hook(uc,at,size,data):
            self.keyboard_count+=1
            self._return(0)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,keyboard_hook,begin=keyboard,end=keyboard)
        self.keyboard_va=keyboard
        ultra=mode_getter("$is_ultra_fast_mode")
        def zero_modes(source):
            source.write_u32(source.allowed_slot,1)
            for slot,_ in writes:source.write_u32(slot,0)
        auto_entry=self.entries["$set_auto_mode"]
        result=self.execute("syscall",[],struct.pack("<IH",int(auto_entry["hash"],16),0),
                            setup=zero_modes,budget=100000)
        auto_candidates=[slot for slot,_ in writes if self.read_u32(slot)==1]
        if result!=[(0,0)]or len(auto_candidates)!=1:
            raise ValueError("original AUTO flag calibration failed")
        auto=auto_candidates[0]
        rest=[(slot,value)for slot,value in writes if slot not in(auto,ultra)]
        if len(rest)!=2 or sorted(value for _,value in rest)!=[0,1]:
            raise ValueError(f"original ultra mode assignments are ambiguous: writes={writes}, auto={auto:x}, ultra={ultra:x}")
        self.mode_slots={"auto_mode":auto,"ultra_fast_mode":ultra,
            "fast_mode":next(slot for slot,value in rest if value==1),
            "fast_read_mode":next(slot for slot,value in rest if value==0)}

    def call_mode(self,name,allowed,modes,discard=False):
        def setup(source):
            if source.allowed_slot is not None:source.write_u32(source.allowed_slot,allowed)
            for field,address in source.mode_slots.items():
                source.write_u32(address,int(modes[field]))
        entry=self.entries[name]
        return self.execute("syscallp"if discard else"syscall",[],struct.pack("<IH",int(entry["hash"],16),0),
                            setup=setup,budget=100000)

    def disable_returns_previous(self):
        if "$ultra_fast_disable" not in self.entries:return None
        result=self.call_mode("$ultra_fast_disable",7,{field:False for field in self.mode_slots})
        if result not in([(0,0)],[(0,7)]):raise ValueError("unknown ultra disable return convention")
        return result==[(0,7)]
