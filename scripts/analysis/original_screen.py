"""Original grp_home dispatch and client-to-game coordinate conversion."""
from __future__ import annotations

import struct

import capstone
import pefile
import unicorn
from unicorn import x86_const as reg

from native_function import NativeFunctions
from native_references import NativeReferences
from original_calls import CallOracle
from original_opcode import native_number


def absolute(operand):
    return operand.type==capstone.CS_OP_MEM and not operand.mem.base and not operand.mem.index


class ScreenOracle(CallOracle):
    def __init__(self,sample,entries):
        super().__init__(sample)
        self.hash_entries={entry["hash"]:entry for entry in entries}
        self.entries={entry["name"]:entry for entry in entries if entry["name"]}
        f=self.functions=NativeFunctions(self)
        pe=pefile.PE(sample["path"])
        self.iat={entry.address:entry.name.decode("ascii")if entry.name else"ordinal"
                  for library in pe.DIRECTORY_ENTRY_IMPORT for entry in library.imports}
        self.root=f.syscall_helper(self.entries["$grp_home"])
        root=f.body(self.root)
        candidates=[]
        for address in [self.root]+[row.operands[0].imm for row in root
                if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM]:
            body=f.body(address)
            writes=[row.operands[0].mem.disp for row in body if row.mnemonic=="mov"
                and absolute(row.operands[0])and row.operands[1].type==capstone.CS_OP_REG
                and row.operands[0].mem.disp not in self.iat]
            invalidates=any(row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_MEM
                and self.iat.get(row.operands[0].mem.disp)=="InvalidateRect"for row in body)
            if 2<=len(set(writes))<=3 and invalidates:
                candidates.append((address,body,list(dict.fromkeys(writes))[:2]))
        if len(candidates)!=1:raise ValueError("original screen home setter is ambiguous")
        self.setter,self.setter_body,slots=candidates[0]
        refs=NativeReferences(self,sample["path"])
        self.home_references={slot:[row.address for row in refs.instructions(slot)]for slot in slots}
        mappings=[]
        setter_reads={operand.mem.disp for row in self.setter_body for operand in row.operands[1:]
                      if absolute(operand)}
        for slot,name in zip(slots,("$curpos_x","$curpos_y")):
            possible=[];identities=[];pending=[self.entries[name]["selected_va"]];seen=set()
            for depth in range(4):
                following=[]
                for address in pending:
                    if address in seen:continue
                    seen.add(address)
                    leading=f.instruction(address)
                    if address==self.entries[name]["selected_va"]and leading.mnemonic=="call":
                        following.append(leading.operands[0].imm);continue
                    body=f.body(address)
                    if(len(body)==2 and body[0].mnemonic=="mov"and len(body[0].operands)==2
                       and absolute(body[0].operands[1])and body[1].mnemonic=="ret"):
                        identities.append(body[0].operands[1].mem.disp)
                    divisions=[row for row in body if row.mnemonic in("divsd","fdiv","idiv")and
                           len(row.operands)>=1 and absolute(row.operands[-1])]
                    reads={operand.mem.disp for row in body for operand in row.operands
                           if absolute(operand)}
                    if slot in reads and len(divisions)==1:
                        scale=divisions[0].operands[-1].mem.disp
                        extra=reads-{slot,scale}
                        fixed=divisions[0].mnemonic=="idiv"
                        if fixed:
                            centers=extra&setter_reads
                            if len(extra)!=2 or len(centers)!=1:raise ValueError("original fixed screen converter changed")
                            center=centers.pop();offset=next(iter(extra-{center}))
                            input_slot=None
                        else:
                            centers={row.operands[1].mem.disp for row in body if row.mnemonic=="sub"
                                and len(row.operands)==2 and absolute(row.operands[1])
                                and row.operands[1].mem.disp!=slot}
                            # Some x87 builds load home/client-origin globals
                            # into registers before subtracting them.
                            origins={}
                            for row in body:
                                if row.mnemonic=="sub"and len(row.operands)==2:
                                    source=row.operands[1]
                                    if source.type==capstone.CS_OP_REG and source.reg in origins:
                                        origin=origins[source.reg]
                                        if origin!=slot:centers.add(origin)
                                if row.operands and row.operands[0].type==capstone.CS_OP_REG:
                                    destination=row.operands[0].reg
                                    if row.mnemonic=="mov"and len(row.operands)==2 and absolute(row.operands[1]):
                                        origins[destination]=row.operands[1].mem.disp
                                    elif row.mnemonic not in("cmp","test","push"):
                                        origins.pop(destination,None)
                            if len(centers)!=1:raise ValueError("original double screen converter changed")
                            center=centers.pop();offset=None
                            inputs=extra-{center}
                            if len(inputs)>1:raise ValueError("original sampled client coordinate is ambiguous")
                            input_slot=next(iter(inputs))if inputs else None
                        stack_argument=any(operand.type==capstone.CS_OP_MEM
                            and operand.mem.base in(capstone.x86.X86_REG_ESP,capstone.x86.X86_REG_EBP)
                            and operand.mem.disp>0 for row in body for operand in row.operands)
                        # A stack-argument converter may read an extra window
                        # offset. That global is not its physical cursor input.
                        if stack_argument:
                            input_slot=None
                        possible.append({"address":address,"home":slot,"center":center,"identity":False,
                            "offset":offset,"scale":scale,"fixed":fixed,"fastcall":not stack_argument,
                            "input_slot":input_slot,"name":name,
                            "integer_conversion":"x87"if divisions[0].mnemonic=="fdiv"else"sse32"})
                    else:following.extend(row.operands[0].imm for row in body if row.mnemonic=="call"
                            and row.operands[0].type==capstone.CS_OP_IMM)
                if possible:break
                pending=following
            unique={row["address"]:row for row in possible}
            if not unique:
                identities=list(dict.fromkeys(identities))
                if len(identities)!=1:raise ValueError("original identity cursor getter changed")
                periods=[]
                for row in refs.instructions(slot):
                    if row.mnemonic!="mov"or row.operands[0].type!=capstone.CS_OP_REG:continue
                    at=row.address+row.size
                    for _ in range(9):
                        item=f.instruction(at);at+=item.size
                        if item.mnemonic in("ret","jmp","call"):break
                        if item.mnemonic=="idiv"and absolute(item.operands[0]):
                            periods.append(item.operands[0].mem.disp);break
                        if item.mnemonic=="mov"and len(item.operands)==2 and absolute(item.operands[1]):
                            break
                periods=list(dict.fromkeys(periods))
                if len(periods)!=1:raise ValueError("original modulo home renderer changed")
                unique={0:{"address":None,"home":slot,"center":None,"scale":None,"offset":None,
                    "fixed":False,"fastcall":False,"identity":True,"input_slot":identities[0],
                    "name":name,"period":periods[0]}}
            if len(unique)!=1:raise ValueError(f"original client coordinate converter changed: {slot:x} {list(unique)}")
            mappings.append(next(iter(unique.values())))
        # The setter writes X then Y in every recovered build; its first input
        # can also be recovered from the dispatch stack accessor below.
        self.axes=mappings
        if any(axis.get("integer_conversion")=="x87"for axis in self.axes):
            # Unicorn starts FPCW at zero (24-bit precision), unlike an
            # initialized x87. Normalize the architectural reset state;
            # otherwise Y's extra fisub rounds away small cursor offsets.
            self.machine.reg_write(reg.UC_X86_REG_FPCW,0x37f)
            self.initial=self.machine.context_save()
        # The virtual-cursor reset branch copies the latest physical sample
        # into the sampled logical cursor slots. Recover its two sources so
        # tests can supply physical events without pretending a converter
        # invocation updates the original event loop's input sample.
        self.has_virtual_cursor="b84c9953"in self.hash_entries
        wrapper=self.hash_entries["b84c9953"]["selected_va"]if self.has_virtual_cursor else None
        targets=[]
        for _ in range(32):
            if wrapper is None:break
            row=f.instruction(wrapper);wrapper+=row.size
            if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM:
                targets.append(row.operands[0].imm)
            if row.mnemonic in("jmp","ret"):break
        setters=[]
        for target in targets:
            body=f.body(target)
            if any(row.mnemonic=="cmp"and any(operand.type==capstone.CS_OP_IMM
                and operand.imm&0xffffffff==0xffffd8f0 for operand in row.operands)for row in body):
                reset=body[:next(index for index,row in enumerate(body)if row.mnemonic=="ret")]
                reads=[row.operands[1].mem.disp for row in reset if row.mnemonic=="mov"
                       and len(row.operands)==2 and absolute(row.operands[1])]
                if len(reads)==2:setters.append(reads)
        if not self.has_virtual_cursor:
            sampled=[axis["input_slot"]for axis in self.axes]
            if any(at is None for at in sampled):
                raise ValueError("original cursor without virtual API has unknown sample slots")
            self.physical_slots=sampled;self.cursor_reset_sentinel=False
        elif not setters:
            # Earlier cursor setters always copy explicit X/Y into both
            # logical and physical slots; they have no -10000 reset command.
            direct=[]
            for target in targets:
                body=f.body(target)
                stores=[row.operands[0].mem.disp for row in body if row.mnemonic=="mov"
                        and absolute(row.operands[0])and row.operands[1].type==capstone.CS_OP_REG]
                if len(stores)==4 and len(set(stores))==4:
                    direct.append(stores)
            if len(direct)!=1:raise ValueError("original direct cursor sampler is ambiguous")
            self.physical_slots=[direct[0][0],direct[0][2]]
            self.cursor_reset_sentinel=False
        elif len(setters)==1:
            self.physical_slots=setters[0];self.cursor_reset_sentinel=True
        else:raise ValueError("original physical cursor sample reset is ambiguous")
        self.baseline={"mode":None,"window":[],"script":[]}
        if len(root)>20 and self.root!=self.setter:
            scalar=[]
            for row in root:
                if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                body=f.body(row.operands[0].imm)
                if len(body)==2 and body[0].mnemonic=="mov"and absolute(body[0].operands[1]):
                    scalar.append(body[0].operands[1].mem.disp)
                elif(body and body[0].mnemonic=="mov"and len(body[0].operands)==2
                     and absolute(body[0].operands[1])and any(item.mnemonic=="cmp"
                         and item.op_str=="eax, -1"for item in body[:4])):
                    self.baseline["mode"]=body[0].operands[1].mem.disp
            direct=[row.operands[1].mem.disp for row in root if row.mnemonic=="mov"
                    and len(row.operands)==2 and absolute(row.operands[1])
                    and row.operands[1].mem.disp not in(self.frame_global,self.context_global)]
            if len(scalar)!=2 or len(direct)!=2 or self.baseline["mode"]is None:
                raise ValueError("original mode-dependent home baseline changed")
            # Wrappers fetch Y first, followed by X.
            self.baseline["window"]=list(reversed(scalar))
            self.baseline["script"]=list(reversed(direct))
        self.invalidations=[]
        self.window=0x1234
        window_slots=[]
        for row in self.setter_body:
            if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
            body=f.body(row.operands[0].imm)
            if len(body)==2 and body[0].mnemonic=="mov"and absolute(body[0].operands[1]):
                window_slots.append(body[0].operands[1].mem.disp)
        self.window_slots=window_slots
        paints=[]
        for slot in slots:
            for reference in refs.instructions(slot):
                if reference.mnemonic!="mov"or reference.operands[0].type!=capstone.CS_OP_REG:continue
                rows=[];address=reference.address
                for _ in range(300):
                    try:row=f.instruction(address)
                    except(StopIteration,unicorn.UcError):break
                    rows.append(row);address+=row.size
                    if(row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_MEM
                       and self.iat.get(row.operands[0].mem.disp)=="StretchDIBits"):
                        reads={operand.mem.disp for item in rows for operand in item.operands if absolute(operand)}
                        if set(slots)<=reads:paints.append((len(rows),reference.address,rows))
                        break
                    if row.mnemonic in("ret","int3","hlt"):break
        if not paints:raise ValueError("original screen DIB presentation path not recovered")
        _,self.paint_entry,self.paint_rows=min(paints,key=lambda row:row[0])
        self.paint_result=None
        self.frame_pixels=None
        self.frame_source=None
        self.frame_size=(0,0)
        self.paint_dimensions=None
        fill_registers=set()
        for index,row in enumerate(self.paint_rows):
            if(row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_REG
               and row.operands[1].type==capstone.CS_OP_MEM
               and self.iat.get(row.operands[1].mem.disp)=="FillRect"):
                fill_registers.add(row.operands[0].reg)
            if(row.mnemonic=="call"and(
                (row.operands[0].type==capstone.CS_OP_MEM and self.iat.get(row.operands[0].mem.disp)=="FillRect")
                or(row.operands[0].type==capstone.CS_OP_REG and row.operands[0].reg in fill_registers))):
                for following in self.paint_rows[index+1:]:
                    if(following.mnemonic=="mov"and following.operands[0].type==capstone.CS_OP_REG
                       and absolute(following.operands[1])and following.operands[1].mem.disp not in self.iat):
                        self.paint_dimensions=following.operands[1].mem.disp
                        break
                break
        self.initial_data=[(pe.OPTIONAL_HEADER.ImageBase+section.VirtualAddress,
            bytes(self.machine.mem_read(pe.OPTIONAL_HEADER.ImageBase+section.VirtualAddress,
                                       section.Misc_VirtualSize)))
            for section in pe.sections if section.Characteristics&0x80000000]

    def reset_data(self):
        for address,data in self.initial_data:self.machine.mem_write(address,data)

    def _import(self,uc,at,size,unused):
        if self.import_names.get(at)=="InvalidateRect":
            stack=uc.reg_read(reg.UC_X86_REG_ESP)
            window,rect,erase=struct.unpack("<3I",uc.mem_read(stack+4,12))
            values=list(struct.unpack("<4i",uc.mem_read(rect,16)))if rect else None
            self.invalidations.append({"rect":values,"erase":erase})
            self._return(3,1);return
        if self.import_names.get(at)in("GetStockObject","DeleteObject"):
            self._return(1,1);return
        if self.import_names.get(at)=="FillRect":
            if self.frame_pixels is not None:
                stack=uc.reg_read(reg.UC_X86_REG_ESP);pointer=self.read_u32(stack+8)
                left,top,right,bottom=struct.unpack("<4i",uc.mem_read(pointer,16))
                w,h=self.frame_size
                for y in range(max(0,top),min(h,bottom)):
                    begin=max(0,left);end=min(w,right)
                    if end>begin:self.frame_pixels[(y*w+begin)*4:(y*w+end)*4]=bytes((0,0,0,255))*(end-begin)
            self._return(3,1);return
        if self.import_names.get(at)=="StretchDIBits":
            stack=uc.reg_read(reg.UC_X86_REG_ESP)
            values=struct.unpack("<13I",uc.mem_read(stack+4,52))
            self.paint_result=[native_number(0,values[1]),native_number(0,values[2])]
            if self.frame_pixels is not None:
                w,h=self.frame_size
                actual=[native_number(0,item)for item in values[3:9]]
                if actual!=[w,h,0,0,w,h]:
                    raise ValueError("native DIB extent fixture is not 1:1: "+repr(actual))
                dx,dy=self.paint_result
                for sy in range(max(0,-dy),min(h,h-dy)):
                    sx0=max(0,-dx);sx1=min(w,w-dx)
                    if sx1>sx0:
                        src=(sy*w+sx0)*4;dst=((sy+dy)*w+sx0+dx)*4
                        self.frame_pixels[dst:dst+(sx1-sx0)*4]=self.frame_source[src:src+(sx1-sx0)*4]
            uc.emu_stop();return
        return super()._import(uc,at,size,unused)

    def call_home(self,x,y,discard=False,center=(0,0),scale=(1.,1.),baseline=(0,0),ignore=False,
                  offset=(0,0)):
        self.invalidations=[]
        for axis,origin,zoom,margin in zip(self.axes,center,scale,offset):
            if axis["identity"]:continue
            self.write_u32(axis["center"],origin)
            if axis["fixed"]:
                self.write_u32(axis["scale"],int(zoom*1000))
                self.write_u32(axis["offset"],margin)
            else:self.machine.mem_write(axis["scale"],struct.pack("<d",zoom))
        if self.baseline["mode"]is not None:
            self.write_u32(self.baseline["mode"],int(ignore))
            for slot,value in zip(self.baseline["script"if ignore else"window"],baseline):
                self.write_u32(slot,value)
        for slot in self.window_slots:self.write_u32(slot,self.window)
        entry=self.entries["$grp_home"]
        result=self.execute("syscallp"if discard else"syscall",[(0,y),(0,x)],
            struct.pack("<IH",int(entry["hash"],16),2),budget=300000)
        return{"stack":result,"home":[native_number(0,self.read_u32(axis["home"]))for axis in self.axes],
               "invalidations":list(self.invalidations)}

    def client_to_game(self,x,y):
        result=[];uc=self.machine
        for axis,value in zip(self.axes,(x,y)):
            if axis["input_slot"]is not None:
                self.write_u32(axis["input_slot"],value)
                entry=self.entries[axis["name"]]
                got=self.execute("syscall",[],struct.pack("<IH",int(entry["hash"],16),0))
                if len(got)!=1:raise ValueError("original client coordinate query result changed")
                result.append(native_number(*got[0]));continue
            uc.context_restore(self.initial)
            stack=self.STACK+0xf000
            uc.mem_write(stack,struct.pack("<II",self.STOP,value&0xffffffff))
            uc.reg_write(reg.UC_X86_REG_ESP,stack);uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
            if axis["fastcall"]:uc.reg_write(reg.UC_X86_REG_ECX,value&0xffffffff)
            uc.emu_start(axis["address"],self.STOP,count=10000)
            if uc.reg_read(reg.UC_X86_REG_EIP)!=self.STOP:raise RuntimeError("original coordinate converter exceeded bound")
            result.append(native_number(0,uc.reg_read(reg.UC_X86_REG_EAX)))
        return result

    def virtual_cursor(self,x,y,discard=False):
        entry=self.hash_entries["b84c9953"]
        return self.execute("syscallp"if discard else"syscall",[(0,y),(0,x)],
            struct.pack("<IH",int(entry["hash"],16),2),budget=100000)

    def sample_cursor(self,x,y):
        for slot,value in zip(self.physical_slots,(x,y)):self.write_u32(slot,value)
        if self.has_virtual_cursor:
            self.virtual_cursor(-10000,0,discard=True)if self.cursor_reset_sentinel else self.virtual_cursor(x,y,discard=True)

    def cursor(self,discard=False):
        result=[]
        for name in("$curpos_x","$curpos_y"):
            entry=self.entries[name]
            values=self.execute("syscallp"if discard else"syscall",[],
                struct.pack("<IH",int(entry["hash"],16),0),budget=100000)
            if len(values)!=(0 if discard else 1):raise ValueError("original cursor query shape changed")
            if not discard:result.append(native_number(*values[0]))
        return result

    def profile(self):
        if all(axis["identity"]for axis in self.axes):
            return{"coordinate_mode":"identity","home_direction":-1,
                   "home_modulo":True,"home_affects_input":False}
        modes={axis["fixed"]for axis in self.axes}
        if len(modes)!=1:raise ValueError("mixed original X/Y screen converter versions")
        fixed=modes.pop()
        result={"coordinate_mode":"fixed1000"if fixed else"double",
                "home_direction":-1 if fixed else 1,"home_modulo":False,"home_affects_input":True}
        if not fixed and any(axis.get("integer_conversion")=="x87"for axis in self.axes):
            result["integer_conversion"]="x87"
        return result

    def paint_origin(self,width=800,height=600):
        for axis,size in zip(self.axes,(width,height)):
            if axis["identity"]:self.write_u32(axis["period"],size)
        # Fixture DIB descriptors are opaque: the real code computes the
        # destination, and execution stops at the GDI boundary before pixels.
        descriptor=self.DATA+0x50000
        self.machine.mem_write(descriptor,bytes(128))
        for offset in(0x20,0x24,0x28,0x2c):self.write_u32(descriptor+offset,self.DATA+0x51000)
        for index,row in enumerate(self.paint_rows):
            if(row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_REG
               and absolute(row.operands[1])):
                register=row.operands[0].reg
                for following in self.paint_rows[index+1:index+5]:
                    if any(operand.type==capstone.CS_OP_MEM and operand.mem.base==register
                           and operand.mem.disp in(0x20,0x24,0x28,0x2c)for operand in following.operands):
                        self.write_u32(row.operands[1].mem.disp,descriptor)
        if self.paint_dimensions is not None:
            offsets=(0,8)if self.axes[0]["identity"]else(-8,0,8,16)
            for offset in offsets:
                self.write_u32(self.paint_dimensions+offset,width)
                self.write_u32(self.paint_dimensions+offset+4,height)
        self.paint_result=None;uc=self.machine;uc.context_restore(self.initial)
        uc.mem_write(self.STACK,bytes(0x10000))
        stack=self.STACK+0xd000
        # In the modern tail X/Y/HDC remain to be pushed; the other DIB
        # arguments were already established by the enclosing paint routine.
        for index,value in enumerate((width,height,0,0,width,height,descriptor,descriptor,0,0xcc0020)):
            self.write_u32(stack+index*4,value)
        if self.axes[0]["fixed"]or self.axes[0]["identity"]:self.write_u32(stack+12,1)
        uc.reg_write(reg.UC_X86_REG_ESP,stack)
        uc.reg_write(reg.UC_X86_REG_EBP,self.STACK+0xc000)
        self.write_u32(self.STACK+0xc008,1)
        uc.reg_write(reg.UC_X86_REG_ESI,1)
        pushes=[row for row in self.paint_rows if row.mnemonic=="push"]
        if not self.axes[0]["fixed"]and not self.axes[0]["identity"]and len(pushes)==7:
            # The older x87 tail starts before destination extents and
            # source origins are pushed. Seed those live registers and
            # only the six arguments already pushed by its caller.
            if [row.op_str for row in pushes] != ["esi","esi","edx","eax","ecx","eax","edi"]:
                raise ValueError("unrecognized seven-argument DIB tail")
            for index,value in enumerate((width,height,descriptor,descriptor,0,0xcc0020)):
                self.write_u32(stack+index*4,value)
            uc.reg_write(reg.UC_X86_REG_EAX,width)
            uc.reg_write(reg.UC_X86_REG_EDX,height)
            uc.reg_write(reg.UC_X86_REG_EBX,self.read_u32(self.axes[1]["center"]))
            uc.reg_write(reg.UC_X86_REG_ESI,0)
            uc.reg_write(reg.UC_X86_REG_EDI,1)
        uc.reg_write(reg.UC_X86_REG_EFLAGS,2)
        uc.emu_start(self.paint_entry,0,count=100000)
        if self.paint_result is None:raise RuntimeError("original presentation path exceeded bound")
        return self.paint_result

    def paint_frame(self,pixels,width,height,previous=None):
        if len(pixels)!=width*height*4:raise ValueError("native frame fixture has the wrong size")
        self.frame_source=bytes(pixels);self.frame_size=(width,height)
        if self.axes[0]["fixed"]or self.axes[0]["identity"]:
            self.frame_pixels=bytearray(previous if previous is not None else bytes((0,0,0,255))*width*height)
        else:self.frame_pixels=bytearray(bytes((0,0,0,255))*width*height)
        try:
            self.paint_origin(width,height)
            return bytes(self.frame_pixels)
        finally:self.frame_pixels=None;self.frame_source=None
