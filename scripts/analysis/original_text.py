"""Recover text state and execute original text/control/layout code.

Explicit fixture boundaries are C formatting, font metrics/rasterization and
empty event delivery. Text parsing, wrapping, buffer consumption, metadata and
event selection execute the original machine code.
"""
from __future__ import annotations

import ctypes
import re
import struct
import zlib

import capstone
import pefile
import unicorn
from unicorn import x86_const as reg

from native_function import NativeFunctions
from original_calls import CallOracle
from original_opcode import native_number


class TextOracle(CallOracle):
    def __init__(self,sample,entries):
        super().__init__(sample)
        self.entries={entry["name"]:entry for entry in entries if entry["name"]}
        self.warnings=[]
        def diagnostic_sink(text):
            if not text.startswith("message : \u30da\u30fc\u30b8\u304b\u3089\u306f\u307f\u51fa\u3057\u307e\u3057\u305f"):
                return False
            self.warnings.append(text)
            return True
        self.diagnostic_sink=diagnostic_sink
        self.functions=f=NativeFunctions(self)
        pe=pefile.PE(sample["path"])
        self.iat={entry.address:entry.name.decode("ascii")if entry.name else"ordinal"
                  for library in pe.DIRECTORY_ENTRY_IMPORT for entry in library.imports}
        self.ctrl_va=self.op_helper("ctrl")
        self.proc_va=self.op_helper("proc")
        self.text_va=self.op_helper("text")
        self.ctrl_cases=self.switch_cases(self.ctrl_va,ord("c"))
        root=f.body(self.ctrl_va)
        branch_rows={row.address:row for row in root}
        for target in set(self.ctrl_cases.values()):
            branch_rows.update({row.address:row for row in f.body(target,minimum_address=self.ctrl_va)})
        self.ctrl_body=[branch_rows[key]for key in sorted(branch_rows)]
        parser_candidates=[]
        for row in root:
            if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM:
                target=row.operands[0].imm
                body=f.body(target)
                if sum(item.mnemonic=="jmp"and item.operands[0].type==capstone.CS_OP_MEM
                       and item.operands[0].mem.scale==4 for item in body)==1:
                    parser_candidates.append(target)
        if len(parser_candidates)!=1:raise ValueError("original text control parser is ambiguous")
        self.parser_va=parser_candidates[0]
        self.parser_cases=self.switch_cases(self.parser_va,ord("N"))
        height=self.scalar_reads("$console_font_height_get")
        if len(height)!=1:raise ValueError("original console height getter changed")
        self.font=height[0]-0x28
        if self.font+0x48 not in self.scalar_reads("$console_curpos_x"):
            raise ValueError("original console style layout changed")
        visible=self.scalar_reads("$console_is_on")
        if len(visible)!=1:raise ValueError("original console visibility getter changed")
        self.visible_slot=visible[0]
        self.orientation_slot=None
        for call in f.calls(f.syscall_helper(self.entries["$console_font"])):
            body=f.body(call.operands[0].imm)
            if(len(body)==2 and body[0].mnemonic=="mov"and body[0].op_str.startswith("eax,")
               and body[0].operands[1].type==capstone.CS_OP_MEM
               and not body[0].operands[1].mem.base and body[1].mnemonic=="ret"):
                if self.orientation_slot is not None:raise ValueError("original orientation getter is ambiguous")
                self.orientation_slot=body[0].operands[1].mem.disp
        if self.orientation_slot is None:
            slots=[row.operands[0].mem.disp
                for row in f.body(f.syscall_helper(self.entries["$console_font"]))
                if row.mnemonic=="cmp"and row.operands[0].type==capstone.CS_OP_MEM
                and not row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_IMM
                and row.operands[1].imm==0]
            if len(slots)>1:raise ValueError("original inline orientation getter is ambiguous")
            if slots:self.orientation_slot=slots[0]
        self.font_cache_slot=self.font+0x68
        proc=f.body(self.proc_va)
        buffer_slots=[]
        for index,row in enumerate(proc):
            if(row.mnemonic=="cmp"and row.operands[0].type==capstone.CS_OP_MEM
               and row.operands[0].mem.disp==0 and row.operands[1].type==capstone.CS_OP_IMM
               and row.operands[1].imm==0 and row.address<self.proc_va+150):
                base=row.operands[0].mem.base
                previous=[item for item in proc[:index]if item.mnemonic=="mov"
                    and item.operands[0].type==capstone.CS_OP_REG and item.operands[0].reg==base
                    and item.operands[1].type==capstone.CS_OP_MEM
                    and not item.operands[1].mem.base and not item.operands[1].mem.index]
                if previous:buffer_slots.append(previous[-1].operands[1].mem.disp)
        buffer_slots=list(dict.fromkeys(buffer_slots))
        if len(buffer_slots)!=1:raise ValueError("original text buffer pointer is ambiguous")
        self.buffer_slot=buffer_slots[0]
        text=f.body(self.text_va)
        pool_bases=[]
        for row in text:
            if(row.mnemonic=="lea"and row.operands[1].type==capstone.CS_OP_MEM
               and row.operands[1].mem.disp>=0x400000):
                pool_bases.append(row.operands[1].mem.disp)
        pool_bases=list(dict.fromkeys(pool_bases))
        if not pool_bases:
            allocators=set();releases=set()
            for index,row in enumerate(text):
                if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                following=text[index+1:index+5]
                if any(item.mnemonic=="mov"and item.operands[0].type==capstone.CS_OP_MEM
                       and not item.operands[0].mem.base and item.operands[0].mem.disp==self.buffer_slot
                       and item.operands[1].type==capstone.CS_OP_REG
                       and item.operands[1].reg==capstone.x86.X86_REG_EAX for item in following):
                    allocators.add(row.operands[0].imm)
                prior=text[max(0,index-2):index]
                if any(item.mnemonic=="push"for item in prior)and any(
                       item.mnemonic=="mov"and item.operands[1].type==capstone.CS_OP_MEM
                       and not item.operands[1].mem.base
                       and item.operands[1].mem.disp==self.buffer_slot for item in prior):
                    releases.add(row.operands[0].imm)
            if len(allocators)!=1 or len(releases)!=1:
                raise ValueError("original dynamic text pool allocation is ambiguous")
            self.pool=self.DATA+0x54000
            def text_allocate(uc,at,size,data):self._return(0,self.pool)
            def text_free(uc,at,size,data):self._return(0)
            for at in allocators:self.machine.hook_add(unicorn.UC_HOOK_CODE,text_allocate,begin=at,end=at)
            for at in releases:self.machine.hook_add(unicorn.UC_HOOK_CODE,text_free,begin=at,end=at)
        elif len(pool_bases)==1:self.pool=pool_bases[0]
        else:raise ValueError("original text pool layout changed")
        flag=[]
        for row in text:
            if row.mnemonic=="mov"and row.operands[1].type==capstone.CS_OP_MEM:
                value=row.operands[1].mem
                if not value.base and not value.index and value.disp not in(self.frame_global,self.context_global):
                    flag.append(value.disp)
            if row.mnemonic=="call":break
        if len(flag)!=1:raise ValueError("original text initialization flag changed")
        self.init_slot=flag[0]
        formats=set()
        for index,row in enumerate(self.ctrl_body):
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            prior=[]
            for item in reversed(self.ctrl_body[:index]):
                if item.mnemonic=="call":break
                prior.append(item)
            for item in prior:
                if item.mnemonic!="push"or item.operands[0].type!=capstone.CS_OP_IMM:continue
                pointer=item.operands[0].imm
                if not 0x400000<=pointer<0x600000:continue
                try:value=self._input(pointer,-1).raw.split(b"\0",1)[0]
                except Exception:continue
                if b"%"in value and len(value)<100:formats.add(row.operands[0].imm)
        if len(formats)!=1:raise ValueError(f"original text formatter is ambiguous: {formats}")
        self.format_va=formats.pop()
        self.host_crt=ctypes.CDLL("msvcrt")
        self.host_crt._snprintf.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_char_p]
        self.host_crt._snprintf.restype=ctypes.c_int
        self.machine.hook_add(unicorn.UC_HOOK_CODE,self.format,begin=self.format_va,end=self.format_va)
        fcase=f.body(self.parser_cases["f"],minimum_address=self.parser_va)
        caches=[]
        for index,row in enumerate(fcase):
            if(row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM
               and not row.operands[0].mem.base and row.operands[0].mem.disp==self.font_cache_slot):
                previous=[item.operands[0].imm for item in fcase[:index]if item.mnemonic=="call"
                          and item.operands[0].type==capstone.CS_OP_IMM]
                if previous:caches.append(previous[-1])
        if len(set(caches))>1:raise ValueError("original font cache creation boundary changed")
        self.font_cache_va=caches[0]if caches else None
        def font_cache(uc,at,size,data):self._return(0,1)
        if self.font_cache_va:
            self.machine.hook_add(unicorn.UC_HOOK_CODE,font_cache,begin=self.font_cache_va,end=self.font_cache_va)
        self.draws=[];self.events=[];self.metric_scale=1;self.page=0x12345678
        candidates=[];fastcall=set()
        for index,row in enumerate(proc):
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            pushes=[]
            for item in reversed(proc[:index]):
                if item.mnemonic=="call":break
                if item.mnemonic=="push":pushes.insert(0,item)
            if len(pushes)>=4 and pushes[-1].operands[0].type==capstone.CS_OP_IMM and pushes[-1].operands[0].imm==self.font:
                candidates.append(row.operands[0].imm)
            elif len(pushes)==3 and any(item.mnemonic=="mov"
                and item.op_str==f"ecx, 0x{self.font:x}"for item in proc[max(0,index-9):index]):
                candidates.append(row.operands[0].imm);fastcall.add(row.operands[0].imm)
        candidates=list(dict.fromkeys(candidates))
        wraps=[];rasters=[]
        for target in candidates:
            uses_extent=any(operand.type==capstone.CS_OP_MEM and not operand.mem.base
                and self.iat.get(operand.mem.disp)=="GetTextExtentPoint32A"
                for row in f.body(target,maximum_bytes=16384,maximum_instructions=5000)for operand in row.operands)
            (wraps if uses_extent else rasters).append(target)
        if not wraps and any(operand.type==capstone.CS_OP_MEM and not operand.mem.base
            and self.iat.get(operand.mem.disp)=="GetTextExtentPoint32A"
            for row in proc for operand in row.operands):
            wraps=[self.proc_va]
        if len(wraps)!=1 or len(rasters)!=1:
            raise ValueError(f"original text layout/raster boundary changed: {wraps}, {rasters}")
        self.wrap_va=wraps[0];self.raster_va=rasters[0]
        self.raster_fastcall=self.raster_va in fastcall
        wrap=f.body(self.wrap_va,maximum_bytes=16384,maximum_instructions=5000)
        prepares=[]
        for row in wrap:
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            after=f.instruction(row.address+row.size)
            if after.mnemonic=="add"and after.op_str=="esp, 8":
                after=f.instruction(after.address+after.size)
                if(after.mnemonic=="mov"and after.operands[0].type==capstone.CS_OP_MEM
                   and after.operands[1].type==capstone.CS_OP_REG
                   and after.operands[1].reg==capstone.x86.X86_REG_EAX):
                    prepares.append(row.operands[0].imm)
        if not prepares:
            for row in wrap:
                if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                target=row.operands[0].imm
                if any(operand.type==capstone.CS_OP_MEM and not operand.mem.base
                    and self.iat.get(operand.mem.disp)=="CreateFontIndirectA"
                    for item in f.body(target,maximum_bytes=16384,maximum_instructions=5000)
                    for operand in item.operands):
                    prepares.append(target)
        if len(set(prepares))!=1:raise ValueError("original font metrics preparation boundary changed")
        self.metrics_va=prepares[0]
        metrics_body=f.body(self.metrics_va)
        self.metrics_fastcall=not any(row.mnemonic=="mov"and row.operands[1].type==capstone.CS_OP_MEM
            and row.operands[1].mem.base in(capstone.x86.X86_REG_ESP,capstone.x86.X86_REG_EBP)
            and row.operands[1].mem.disp>0
            for row in metrics_body[:12]if len(row.operands)==2)
        def metrics(uc,at,size,data):
            stack=uc.reg_read(reg.UC_X86_REG_ESP)
            style,scale=((uc.reg_read(reg.UC_X86_REG_ECX),uc.reg_read(reg.UC_X86_REG_EDX))
                         if self.metrics_fastcall else struct.unpack("<2I",uc.mem_read(stack+4,8)))
            if style!=self.font or scale not in(1,4):raise ValueError("unrecognized font metrics contract")
            self.metric_scale=scale;self._return(0,0)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,metrics,begin=self.metrics_va,end=self.metrics_va)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,self.raster,begin=self.raster_va,end=self.raster_va)
        set_body=f.body(f.syscall_helper(self.entries["$console_set"]))
        validators=[]
        for row in set_body:
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            after=f.instruction(row.address+row.size)
            if after.mnemonic=="add":after=f.instruction(after.address+after.size)
            if after.mnemonic=="test"and after.op_str=="eax, eax":validators.append(row.operands[0].imm)
        self.page_head=None
        if not validators:
            heads=[row.operands[1].mem.disp for index,row in enumerate(set_body[:-1])
                if row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_REG
                and row.operands[0].reg==capstone.x86.X86_REG_EAX
                and row.operands[1].type==capstone.CS_OP_MEM
                and not row.operands[1].mem.base and not row.operands[1].mem.index
                and set_body[index+1].mnemonic=="test"and set_body[index+1].op_str=="eax, eax"]
            if len(heads)!=1:raise ValueError("original inline console page validation changed")
            self.page_head=heads[0];self.page=self.DATA+0x60000
            self.machine.mem_write(self.page,bytes(0x100));self.write_u32(self.page_head,self.page)
        elif len(set(validators))!=1:raise ValueError("original console page validation changed")
        def page_valid(uc,at,size,data):self._return(0,0)
        if validators:
            self.machine.hook_add(unicorn.UC_HOOK_CODE,page_valid,begin=validators[0],end=validators[0])
        pcase=f.body(self.parser_cases["p"],minimum_address=self.parser_va)
        event_candidates=[]
        for index,row in enumerate(pcase):
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            prior=pcase[max(0,index-4):index]
            if any(item.mnemonic in("push","mov")and item.operands[-1].type==capstone.CS_OP_IMM
                   and(item.operands[-1].imm&0xffffffff)==zlib.crc32(b"PAUSE")for item in prior):
                event_candidates.append(row.operands[0].imm)
        if len(set(event_candidates))!=1:raise ValueError("original text event transport changed")
        self.event_va=event_candidates[0]
        self.event_fastcall=any(row.mnemonic=="mov"and row.op_str.startswith("ecx,")
            and row.operands[1].type==capstone.CS_OP_IMM
            and(row.operands[1].imm&0xffffffff)==zlib.crc32(b"PAUSE")for row in pcase)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,self.event,begin=self.event_va,end=self.event_va)
        tracing=[row.operands[0].imm for row in pcase if row.mnemonic=="call"
            and row.operands[0].type==capstone.CS_OP_IMM and row.operands[0].imm!=self.event_va]
        if len(tracing)!=1:raise ValueError("original pause trace helper is ambiguous")
        trace=f.body(tracing[0]);trace_calls=[row for row in trace if row.mnemonic=="call"]
        mode=f.body(trace_calls[0].operands[0].imm)
        if(len(mode)==2 and mode[0].mnemonic=="mov"and mode[0].op_str.startswith("eax,")
               and mode[0].operands[1].type==capstone.CS_OP_MEM and not mode[0].operands[1].mem.base):
            self.production_slot=mode[0].operands[1].mem.disp
        else:
            slots=[row.operands[0].mem.disp for row in trace[:20]
                if row.mnemonic=="cmp"and row.operands[0].type==capstone.CS_OP_MEM
                and not row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_IMM
                and row.operands[1].imm==1]
            if len(slots)!=1:raise ValueError("original production-mode getter is ambiguous")
            self.production_slot=slots[0]
        timed=f.body(self.parser_cases["t"],minimum_address=self.parser_va)
        clocks=[]
        for call in timed:
            if call.mnemonic!="call"or call.operands[0].type!=capstone.CS_OP_IMM:continue
            body=f.body(call.operands[0].imm)
            if(len(body)==2 and body[0].mnemonic=="mov"and body[0].op_str.startswith("eax,")
               and body[0].operands[1].type==capstone.CS_OP_MEM and not body[0].operands[1].mem.base):
                clocks.append(body[0].operands[1].mem.disp)
        if not clocks:
            clocks=[row.operands[1].mem.disp for row in timed if row.mnemonic=="mov"
                and row.operands[1].type==capstone.CS_OP_MEM
                and not row.operands[1].mem.base and not row.operands[1].mem.index
                and row.operands[1].mem.disp!=self.frame_global]
        if len(clocks)!=1:raise ValueError("original text timer getter is ambiguous")
        self.clock_slot=clocks[0]
        waits=[row.operands[0].mem.disp for row in timed if row.mnemonic=="mov"
            and row.operands[0].type==capstone.CS_OP_MEM and row.operands[0].mem.base
            and row.operands[0].mem.disp in(0x3c,0x44,0x48)]
        if len(waits)!=1:raise ValueError("original text timer frame field is ambiguous")
        self.wait_offset=waits[0]
        overlays=[row.operands[0].mem.disp
            for row in f.body(self.parser_cases["o"],minimum_address=self.parser_va)
            if row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM
            and not row.operands[0].mem.base and not row.operands[0].mem.index
            and row.operands[1].type==capstone.CS_OP_IMM and row.operands[1].imm==1]
        if len(overlays)>1:raise ValueError("original overlay state is ambiguous")
        self.overlay_slot=overlays[0]if overlays else None
        history=self.scalar_reads("$histbuff_disable")
        if len(history)!=1:raise ValueError("original history depth is ambiguous")
        self.history_slot=history[0]
        self.initial_data=[(pe.OPTIONAL_HEADER.ImageBase+section.VirtualAddress,
            bytes(self.machine.mem_read(pe.OPTIONAL_HEADER.ImageBase+section.VirtualAddress,
                                       section.Misc_VirtualSize)))
            for section in pe.sections if section.Characteristics&0x80000000]

    def op_helper(self,name):
        address=self.rows[name]["selected_va"]
        for _ in range(10):
            row=self.functions.instruction(address)
            if row.mnemonic=="call":return row.operands[0].imm
            address+=row.size
        raise ValueError("original text opcode wrapper changed")

    def entry_body(self,name):
        address=self.entries[name]["selected_va"];first=self.functions.instruction(address)
        if first.mnemonic=="call":return self.functions.body(first.operands[0].imm)
        rows=[]
        for _ in range(40):
            row=self.functions.instruction(address);rows.append(row)
            if row.mnemonic in("jmp","ret"):break
            address+=row.size
        return rows

    def scalar_reads(self,name):
        values=[]
        for row in self.entry_body(name):
            for operand in row.operands:
                if(operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index
                   and operand.mem.disp not in self.iat):values.append(operand.mem.disp)
            if row.mnemonic=="call":break
        return list(dict.fromkeys(values))

    def switch_cases(self,address,first_char):
        rows=self.functions.body(address)
        jumps=[row for row in rows if row.mnemonic=="jmp"and row.operands[0].type==capstone.CS_OP_MEM
               and row.operands[0].mem.scale==4]
        if len(jumps)!=1:raise ValueError("original text switch table is ambiguous")
        jump=jumps[0];before=[row for row in rows if row.address<jump.address]
        bounds=[row.operands[1].imm for row in before[-14:]if row.mnemonic=="cmp"
                and row.operands[1].type==capstone.CS_OP_IMM and 0<row.operands[1].imm<128]
        if not bounds:raise ValueError("original text switch bound is missing")
        count=bounds[-1]+1;maps=[]
        for row in before[-12:]:
            if(row.mnemonic in("movzx","mov")and len(row.operands)==2
               and row.operands[1].type==capstone.CS_OP_MEM and row.operands[1].size==1):
                memory=row.operands[1].mem
                if memory.disp>=0x400000:maps.append(memory.disp)
        if len(maps)>1:raise ValueError("original text switch byte map is ambiguous")
        indices=bytes(self.machine.mem_read(maps[0],count))if maps else range(count)
        return{chr(first_char+i):self.read_u32(jump.operands[0].mem.disp+index*4)
               for i,index in enumerate(indices)}

    def format(self,uc,at,size,unused):
        stack=uc.reg_read(reg.UC_X86_REG_ESP)
        destination,format_address=struct.unpack("<2I",uc.mem_read(stack+4,8))
        fmt=self._input(format_address,-1).raw.split(b"\0",1)[0]
        arguments=[];cursor=stack+12
        pattern=re.compile(rb"%(?:0?\d+)?([dxfs])")
        for match in pattern.finditer(fmt):
            kind=match.group(1)
            if kind==b"f":
                value=struct.unpack("<d",uc.mem_read(cursor,8))[0]
                arguments.append(ctypes.c_double(value));cursor+=8
            else:
                value=self.read_u32(cursor);cursor+=4
                if kind==b"s":
                    arguments.append(ctypes.c_char_p(self._input(value,-1).raw.split(b"\0",1)[0]))
                elif kind==b"d":arguments.append(ctypes.c_int32(value))
                else:arguments.append(ctypes.c_uint32(value))
        if pattern.sub(b"",fmt).find(b"%")>=0:raise ValueError("unsupported original CRT text format: "+repr(fmt))
        output=ctypes.create_string_buffer(8192)
        length=self.host_crt._snprintf(output,len(output),fmt,*arguments)
        if length<0 or length>=len(output):raise ValueError("original text formatting fixture exceeds bound")
        uc.mem_write(destination,output.raw[:length]+b"\0")
        self._return(0,length)

    def advance(self,data):
        text=data.decode("cp932","strict")
        width=self.read_u32(self.font+0x2c)
        return sum(width*(1 if len(char.encode("cp932"))==1 else 2)for char in text)

    def _import(self,uc,at,size,unused):
        if self.import_names.get(at)=="GetTextExtentPoint32A":
            stack=uc.reg_read(reg.UC_X86_REG_ESP)
            hdc,text,length,result=struct.unpack("<4I",uc.mem_read(stack+4,16))
            data=bytes(uc.mem_read(text,length))
            uc.mem_write(result,struct.pack("<2I",self.advance(data)*self.metric_scale,
                                            self.read_u32(self.font+0x28)*self.metric_scale))
            self._return(4,1);return
        return super()._import(uc,at,size,unused)

    def raster(self,uc,at,size,unused):
        stack=uc.reg_read(reg.UC_X86_REG_ESP)
        if self.raster_fastcall:
            style,page=uc.reg_read(reg.UC_X86_REG_ECX),uc.reg_read(reg.UC_X86_REG_EDX)
            text,rectangle,mode=struct.unpack("<3I",uc.mem_read(stack+4,12))
        else:style,page,text,rectangle,mode=struct.unpack("<5I",uc.mem_read(stack+4,20))
        if style!=self.font or page!=self.page:raise ValueError("unrecognized original raster target")
        data=self._input(text,-1).raw.split(b"\0",1)[0]
        width=self.advance(data);height=self.read_u32(self.font+0x28)
        x,y=self.read_u32(self.font+0x48),self.read_u32(self.font+0x4c)
        self.draws.append({"text":data.decode("cp932"),"x":x,"y":y,"width":width,"height":height})
        uc.mem_write(rectangle,struct.pack("<4I",x,y,width,height))
        self._return(0,width)

    def event(self,uc,at,size,unused):
        stack=uc.reg_read(reg.UC_X86_REG_ESP)
        if self.event_fastcall:
            hash_,count=uc.reg_read(reg.UC_X86_REG_ECX),uc.reg_read(reg.UC_X86_REG_EDX)
            wants=self.read_u32(stack+4)
        else:hash_,count,wants=struct.unpack("<3I",uc.mem_read(stack+4,12))
        if count>16 or wants:raise ValueError("unrecognized original text event signature")
        frame=self.read_u32(self.frame_global);top=self.read_u32(frame+8)
        values=[]
        for index in range(count):
            slot=self.values+(top-index-1)*16
            tag=self.read_u32(slot+4);raw=self.read_u32(slot+12)
            values.append(self.string_result(tag,raw)if tag==2 else native_number(tag,raw))
        self.write_u32(frame+8,top-count)
        self.events.append({"hash":f"{hash_:08x}","args":values,
                           "pc":self.read_u32(frame)-(self.operand-2)})
        self._return(0)

    def call_api(self,name,args):
        values=[]
        for index,arg in enumerate(args):
            values.append((2,self.string(arg,index))if isinstance(arg,str)else(0,int(arg)&0xffffffff))
        entry=self.entries[name]
        return self.execute("syscall",list(reversed(values)),
            struct.pack("<IH",int(entry["hash"],16),len(values)),budget=500000)

    def configure(self,visible=True,history=True,width=120,height=300):
        for address,data in self.initial_data:self.machine.mem_write(address,data)
        self.machine.mem_write(self.pool,bytes(0x1010))
        self.write_u32(self.buffer_slot,0);self.write_u32(self.init_slot,1)
        self.machine.mem_write(self.font,bytes(0xb0))
        self.write_u32(self.visible_slot,int(visible))
        if self.orientation_slot:self.write_u32(self.orientation_slot,0)
        self.write_u32(self.production_slot,1)
        self.write_u32(self.clock_slot,100)
        self.call_api("$console_set",[self.page,20,30,width,height])
        self.call_api("$console_font",["MS Gothic",20,10,24,0])
        self.call_api("$console_color",[0x112233,0x445566])
        self.write_u32(self.history_slot,0 if history else 1)
        self.execute("ctrl",[],struct.pack("<H",2)+b"w\0",budget=500000)
        self.events=[];self.draws=[]
        self.warnings=[]

    def text_state(self):
        pointer=self.read_u32(self.buffer_slot)
        text=self._input(pointer,-1).raw.split(b"\0",1)[0].decode("cp932")if pointer else""
        number=lambda offset:native_number(0,self.read_u32(self.font+offset))
        return{"buffer":text,"x":number(0x48),"y":number(0x4c),
            "font":[self._input(self.font,-1).raw.split(b"\0",1)[0].decode("cp932"),
                    number(0x28),number(0x2c),number(0x30),number(0x20)],
            "color":number(0x34),"shadow":number(0x38),
            "zero":bool(self.read_u32(self.font+0x3c)),
            "speed":number(0x40),
            "overlay":bool(self.overlay_slot and self.read_u32(self.overlay_slot)),
            "history_depth":native_number(0,self.read_u32(self.history_slot)),
            "visible":bool(self.read_u32(self.visible_slot))}

    def instruction(self,name,value=None,values=()):
        operands=b""
        if name in("text","ctrl"):
            raw=value.encode("cp932")+b"\0";operands=struct.pack("<H",len(raw))+raw
        result=self.execute(name,list(values),operands,budget=500000)
        return{"stack":result,"pc":self.read_u32(self.frame)-(self.operand-2),
               "wait":native_number(0,self.read_u32(self.frame+self.wait_offset)),
               "state":self.text_state(),"events":list(self.events),"draws":list(self.draws)}

    def last_message(self):
        result=self.call_api("$get_last_msg$",[])
        if len(result)!=1:raise ValueError("original last-message getter stack changed")
        return self.string_result(*result[0])

    def profile(self):
        self.configure()
        self.instruction("ctrl","f",[(0,20),(0,10),(0,24),
            (2,self.string("",0)),(0,1)])
        state=self.instruction("ctrl","f",[(0,0xffffffff)]*3+
            [(2,self.string("",0)),(0,0xffffffff)])["state"]
        modern=state["font"][1:]==[20,10,24,1]
        legacy=state["font"][1:]==[-1,0,-1,0]
        if not(modern or legacy):raise ValueError("unclassified original font defaults")
        pause=self.instruction("ctrl","P")["events"]
        if pause and pause!=[{"hash":"7ee67053","args":[],"pc":6}]:
            raise ValueError("unclassified uppercase pause control")
        self.configure()
        self.instruction("ctrl","o",[(0,5),(0,7)])
        self.instruction("ctrl","c",[(0,0x556677),(0,0x123456)])
        restored=self.instruction("ctrl","r")["state"]["color"]==0x112233
        self.configure()
        self.instruction("text","ABC");self.instruction("proc");self.instruction("ctrl","n")
        self.instruction("text","DEF");self.instruction("proc")
        message=self.last_message()
        if message not in("ABC","ABCDEF"):raise ValueError("unclassified message capture behavior")
        return{"legacy_font_defaults":legacy,"overlay_saves_style":restored,
               "overlay_suppresses_message":bool(self.overlay_slot),
               "message_first_line_only":message=="ABC",
               "uppercase_pause_event":bool(pause)}
