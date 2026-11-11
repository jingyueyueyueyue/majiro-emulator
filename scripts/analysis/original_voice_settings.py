"""Original voice label tables, per-character queries and one-shot flags."""
from __future__ import annotations

import struct

import capstone

from original_text import TextOracle
from original_opcode import native_number


class VoiceSettingsOracle(TextOracle):
    def __init__(self,sample,entries):
        super().__init__(sample,entries)
        f=self.functions
        self.label_tables={}
        for name,length in(("$voice_setup",30),("$voice_text_setup",15)):
            if name not in self.entries:continue
            body=f.body(f.syscall_helper(self.entries[name]))
            stores=[row for row in body if row.mnemonic=="rep stosd"]
            if stores:
                before=[row for row in body if row.address<stores[0].address]
                bases=[row.operands[1].imm for row in before if row.mnemonic=="mov"
                    and row.op_str.startswith("edi,")and row.operands[1].type==capstone.CS_OP_IMM]
                sizes=[row.operands[1].imm*4 for row in before if row.mnemonic=="mov"
                    and row.op_str.startswith("ecx,")and row.operands[1].type==capstone.CS_OP_IMM]
            else:
                before=[]
                for row in body:
                    if row.mnemonic=="call":break
                    before.append(row)
                pushes=[row.operands[0].imm for row in before if row.mnemonic=="push"
                        and row.operands[0].type==capstone.CS_OP_IMM]
                if len(pushes)<3 or pushes[-2]!=0:raise ValueError("original voice label clear changed")
                sizes,bases=[pushes[-3]],[pushes[-1]]
            if len(bases)!=1 or sizes!=[length*128]:
                raise ValueError("original voice label table dimensions changed")
            self.label_tables[name]={"base":bases[0],"capacity":length,"stride":128}
        setup=f.body(f.syscall_helper(self.entries["$get_voice_setup"]))if "$get_voice_setup" in self.entries else []
        arrays={operand.mem.disp for row in setup for operand in row.operands
                if operand.type==capstone.CS_OP_MEM and operand.mem.index and operand.mem.scale==4
                and operand.mem.disp>=0x400000}
        flags={operand.mem.disp for row in setup for operand in row.operands
               if operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
               and operand.mem.disp not in(self.frame_global,self.context_global)}
        if setup and(len(arrays)!=1 or len(flags)!=1):raise ValueError("original voice setup query fields changed")
        self.levels=arrays.pop()if arrays else None;self.female_slot=flags.pop()if flags else None
        self.scalars={}
        for name in("$voice_musicdown_vol","$is_voice_musicdown","$set_voice_male","$set_voice_skip"):
            if name not in self.entries:continue
            values=self.scalar_reads(name)
            if len(values)!=1:raise ValueError("original voice scalar field changed: "+name)
            self.scalars[name]=values[0]

    def call(self,name,args=(),discard=False,setup=None):
        values=[]
        if len(args)>30:raise ValueError("voice fixture exceeds recovered table capacity")
        for index,arg in enumerate(args):
            if isinstance(arg,str):
                raw=arg.encode("cp932")+b"\0"
                if len(raw)>128:raise ValueError("voice label exceeds original slot")
                pointer=self.DATA+0x30000+index*0x100
                self.machine.mem_write(pointer,struct.pack("<3I",64,0,len(raw))+raw)
                values.append((2,pointer))
            else:values.append((0,int(arg)&0xffffffff))
        entry=self.entries[name]
        return self.execute("syscallp"if discard else"syscall",list(reversed(values)),
            struct.pack("<IH",int(entry["hash"],16),len(values)),setup=setup,budget=500000)

    def labels(self,name):
        table=self.label_tables[name]
        return[self._input(table["base"]+index*table["stride"],-1).value.decode("cp932")
               for index in range(table["capacity"])]

    def set_levels(self,values,female):
        if len(values)!=30:raise ValueError("voice level fixture must fill every slot")
        self.machine.mem_write(self.levels,struct.pack("<30i",*values))
        self.write_u32(self.female_slot,female)

    def scalar(self,name):
        return native_number(0,self.read_u32(self.scalars[name]))
