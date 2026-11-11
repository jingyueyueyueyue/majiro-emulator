"""Bounded original platform/input controls; no game or device is launched."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

import capstone
import unicorn
from unicorn import x86_const as reg

from original_opcode import native_number
from original_screen import ScreenOracle, absolute

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir

HASHES=("2bd1709d","7e0b8320","3670b283","42d7c922","c32e286f",
        "05b1a6e8","d5999d46","929a367d","125cfbdb","d4590bd3")

class PlatformOracle(ScreenOracle):
    def __init__(self,sample,entries):
        super().__init__(sample,entries)
        self.platform_calls=[];self.client_rect=(0,0,800,600);self.desktop_origin=(41,-23)
        self.cursor_client=(0,0);self.cursor_success=True;self.caps=0;self.moved=None
        self.functions_cache={}
        f=self.functions
        self.roots={};self.bodies={}
        for hash_ in HASHES:
            if hash_ not in self.hash_entries:continue
            entry=self.hash_entries[hash_]
            wrapper=[];at=entry["selected_va"]
            for _ in range(35):
                row=f.instruction(at);wrapper.append(row);at+=row.size
                if row.mnemonic in("jmp","ret"):break
            calls=[row.operands[0].imm for row in wrapper if row.mnemonic=="call"
                   and row.operands[0].type==capstone.CS_OP_IMM]
            inline=wrapper[0].mnemonic!="call"
            if hash_=="d4590bd3":
                possible=[at for at in calls if any(row.mnemonic=="call"and
                    row.operands[0].type==capstone.CS_OP_MEM and
                    self.iat.get(row.operands[0].mem.disp)=="ClientToScreen"for row in f.body(at))]
                if not possible:
                    grandchildren={row.operands[0].imm for at in calls for row in f.body(at)
                                   if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM}
                    possible=[at for at in grandchildren if any(row.mnemonic=="call"and
                        row.operands[0].type==capstone.CS_OP_MEM and
                        self.iat.get(row.operands[0].mem.disp)=="ClientToScreen"for row in f.body(at))]
                if len(possible)!=1:raise ValueError("mouse move native helper changed")
                root=possible[0]
            else:root=entry["selected_va"]if inline else calls[0]
            self.roots[hash_]=root
            self.bodies[hash_]=wrapper if inline else f.body(root)
        self.keyboard_slot=None;self.keyboard_clear=[]
        self.caps_guard=None;self.drag_flag=None;self.drop_buffer=None
        if "d5999d46"in self.roots:
            body=self.bodies["d5999d46"]
            slots=[row.operands[0].mem.disp for row in body if row.mnemonic=="mov"
                   and absolute(row.operands[0])and row.operands[1].type==capstone.CS_OP_IMM
                   and row.operands[1].imm==1]
            if len(slots)!=1:raise ValueError("keyboard disabled slot changed")
            self.keyboard_slot=slots[0]
            enable=self.bodies["05b1a6e8"]
            has_buffers=any(row.mnemonic.startswith("rep")for row in enable)or sum(
                row.mnemonic=="push"and row.operands[0].type==capstone.CS_OP_IMM
                and row.operands[0].imm in(256,1024)for row in enable)>=2
            initializer=self.roots["05b1a6e8"]if has_buffers else next(
                row.operands[0].imm for row in enable if row.mnemonic=="call")
            init=enable if has_buffers else f.body(initializer)
            first=init[0]
            if first.mnemonic=="cmp"and absolute(first.operands[0]):
                self.write_u32(first.operands[0].mem.disp,1)
            elif first.mnemonic=="mov"and absolute(first.operands[1]):
                self.write_u32(first.operands[1].mem.disp,1)
            ecx=edi=None
            for index,row in enumerate(init):
                if row.mnemonic=="push"and row.operands[0].type==capstone.CS_OP_IMM:
                    count=row.operands[0].imm
                    if count in(256,1024)and index+2<len(init):
                        target=init[index+2]
                        if target.mnemonic=="push"and target.operands[0].type==capstone.CS_OP_IMM:
                            self.keyboard_clear.append((target.operands[0].imm,count))
                if row.mnemonic=="mov"and row.operands[1].type==capstone.CS_OP_IMM:
                    if row.op_str.startswith("ecx,"):ecx=row.operands[1].imm
                    if row.op_str.startswith("edi,"):edi=row.operands[1].imm
                if row.mnemonic.startswith("rep stosd")and ecx is not None and edi is not None:
                    self.keyboard_clear.append((edi,ecx*4))
                if row.mnemonic=="mov"and absolute(row.operands[0])and row.operands[1].type==capstone.CS_OP_IMM:
                    if row.operands[1].imm&0xffffffff==0xffffffff:self.caps_guard=row.operands[0].mem.disp
            calls=[row.operands[0].imm for row in init if row.mnemonic=="call"
                   and row.operands[0].type==capstone.CS_OP_IMM]
            writer=next((row.operands[0].imm for row in reversed(body)
                if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM),None)
            final_calls=[target for target in calls if target!=writer]
            if final_calls and final_calls[-1]!=final_calls[0]and final_calls.count(final_calls[-1])==1:
                target=final_calls[-1]
                self.machine.hook_add(unicorn.UC_HOOK_CODE,
                    lambda uc,at,size,unused:self._return(0),
                    begin=target,end=target)
            for row in init:
                if row.mnemonic=="jmp"and row.operands[0].type==capstone.CS_OP_IMM and(
                    row.operands[0].imm<initializer or row.operands[0].imm>=initializer+8192):
                    self.machine.hook_add(unicorn.UC_HOOK_CODE,
                        lambda uc,at,size,unused:self._return(0),
                        begin=row.operands[0].imm,end=row.operands[0].imm)
        if "125cfbdb"in self.roots:
            body=self.bodies["125cfbdb"]
            slots=[row.operands[0].mem.disp for row in body if row.mnemonic=="cmp"
                   and absolute(row.operands[0])and row.operands[1].type==capstone.CS_OP_IMM
                   and row.operands[1].imm&0xffffffff==0xffffffff]
            if len(slots)!=1:raise ValueError("caps guard changed")
            self.caps_guard=slots[0]
        drag=self.bodies["42d7c922"]
        slots=[row.operands[0].mem.disp for row in drag if row.mnemonic=="mov"
               and absolute(row.operands[0])and row.operands[1].type==capstone.CS_OP_REG]
        if len(slots)!=1:raise ValueError("drag acceptance slot changed")
        self.drag_flag=slots[0]
        getter=self.bodies["c32e286f"]
        immediate=[row.operands[1].imm for row in getter if row.mnemonic=="mov"
                   and row.operands[0].type==capstone.CS_OP_REG and row.operands[1].type==capstone.CS_OP_IMM
                   and row.operands[1].imm>=0x400000]
        if immediate:self.drop_buffer=immediate[0]
        else:
            child=next(row.operands[0].imm for row in getter if row.mnemonic=="call")
            code=f.body(child)
            if len(code)!=2 or code[0].mnemonic!="mov"or code[0].operands[1].type!=capstone.CS_OP_IMM:
                raise ValueError("drop filename buffer getter changed")
            self.drop_buffer=code[0].operands[1].imm
        inside=self.bodies.get("3670b283",[])
        self.cursor_enabled_slot=next((operand.mem.disp for row in inside[:5]for operand in row.operands
            if absolute(operand)and operand.mem.disp not in self.iat),None)
        # Containment changed from exclusive client bounds to inclusive
        # bounds expanded by twice the emulated margins.
        calls=[row.operands[0].imm for row in inside if row.mnemonic=="call"
               and row.operands[0].type==capstone.CS_OP_IMM]
        self.containment_slots=[]
        tail=f.body(calls[-1])if calls else inside
        reads=[operand.mem.disp for row in tail for operand in row.operands[1:]
               if absolute(operand)and operand.mem.disp not in self.iat]
        if len(reads)==6:
            size_slots=[address for address in reads if address not in
                        {value for axis in self.axes for value in(axis["home"],axis["center"])}]
            if len(size_slots)!=2:size_slots=[reads[2],reads[5]]
            self.containment_slots=[(address,"width"if address==size_slots[0]else
                "height"if address==size_slots[1]else 0)for address in reads]
            self.inclusive_bounds=True
            self.containment_mode="fixed_offset_inclusive"if self.axes[0]["fixed"]else"modern_margin_inclusive"
        else:
            self.inclusive_bounds=False
            self.containment_mode="legacy_exclusive"if inside else"unavailable"
            for at,which in zip(calls[-2:],("width","height")):
                body=f.body(at)
                slots=[operand.mem.disp for row in body[:3]for operand in row.operands[1:]if absolute(operand)]
                if len(slots)!=1:raise ValueError("legacy containment dimension getter changed")
                self.containment_slots.append((slots[0],which))
        # No per-call GS key depends on fixture data. Hook only recognized
        # cookie validators, leaving original platform arithmetic intact.
        cookies=set()
        for root in list(self.roots.values()):
            candidates=[root]+[row.operands[0].imm for row in f.body(root)
                if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM]
            for at in candidates:
                code_body=f.body(at,maximum_bytes=1024)
                for index,row in enumerate(code_body):
                    if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                    target=row.operands[0].imm;code=f.body(target,maximum_bytes=128)
                    if target not in cookies and((code and code[0].mnemonic=="cmp"and code[0].op_str.startswith("ecx, dword ptr [")
                       and any("ret"in item.mnemonic for item in code))
                       or(index>0 and code_body[index-1].mnemonic=="xor"
                          and code_body[index-1].op_str=="ecx, ebp")):
                        self.machine.hook_add(unicorn.UC_HOOK_CODE,
                            lambda uc,at,size,unused:self._return(0,uc.reg_read(reg.UC_X86_REG_EAX)),
                            begin=target,end=target)
                        cookies.add(target)

    def profile(self):
        reference=self.call_control("d4590bd3",(0,0),home=(3,-2))["moved"]
        delta=(reference[0]-self.desktop_origin[0],reference[1]-self.desktop_origin[1])
        if self.axes[0]["identity"]:
            mode="legacy_identity"
        elif not self.axes[0]["fixed"]:
            mode="modern"
        elif delta==(3,-2):mode="fixed_plus"
        elif delta==(-3,2):mode="fixed_minus"
        else:raise ValueError("mouse move profile is unrecovered")
        clears_caps=False
        if "05b1a6e8"in self.roots:
            request=self.call_control("05b1a6e8",caps=1)
            clears_caps=any(row["api"]=="keybd_event"and row["arguments"][:1]==[20]
                            for row in request["platform_calls"])
        return {"containment":getattr(self,"containment_mode","unrecovered"),"mouse_move":mode,
                "keyboard_enable_clears_caps":clears_caps}

    def _import(self,uc,at,size,unused):
        name=self.import_names.get(at);stack=uc.reg_read(reg.UC_X86_REG_ESP)
        def args(count):return list(struct.unpack("<"+"I"*count,uc.mem_read(stack+4,count*4)))
        if name=="GetClientRect":
            hwnd,pointer=args(2);uc.mem_write(pointer,struct.pack("<4i",*self.client_rect))
            self.platform_calls.append({"api":name,"window":hwnd});self._return(2,1);return
        if name=="GetCursorPos":
            pointer,=args(1);x,y=self.cursor_client;ox,oy=self.desktop_origin
            uc.mem_write(pointer,struct.pack("<2i",x+ox,y+oy))
            self.platform_calls.append({"api":name,"success":self.cursor_success})
            self._return(1,int(self.cursor_success));return
        if name in("ScreenToClient","ClientToScreen"):
            hwnd,pointer=args(2);x,y=struct.unpack("<2i",uc.mem_read(pointer,8));ox,oy=self.desktop_origin
            sign=-1 if name=="ScreenToClient"else 1
            uc.mem_write(pointer,struct.pack("<2i",native_number(0,(x+sign*ox)&0xffffffff),
                                                    native_number(0,(y+sign*oy)&0xffffffff)))
            self.platform_calls.append({"api":name,"window":hwnd});self._return(2,1);return
        if name=="SetCursorPos":
            x,y=args(2);self.moved=(native_number(0,x),native_number(0,y))
            self.platform_calls.append({"api":name,"point":list(self.moved)});self._return(2,1);return
        if name=="GetKeyState":
            key,=args(1);self.platform_calls.append({"api":name,"key":key})
            self._return(1,self.caps);return
        if name=="keybd_event":
            self.platform_calls.append({"api":name,"arguments":args(4)});self._return(4);return
        if name=="DragAcceptFiles":
            self.platform_calls.append({"api":name,"arguments":args(2)});self._return(2);return
        if name in("EnterCriticalSection","LeaveCriticalSection"):
            self._return(1);return
        return super()._import(uc,at,size,unused)

    def call_control(self,hash_,args=(),discard=False,keyboard_disabled=0,caps=0,caps_guard=-1,
                     drop_bytes=b"",enabled=1,point=(0,0),size=(800,600),success=True,home=(0,0)):
        self.call_home(*home)
        self.platform_calls=[];self.moved=None;self.cursor_client=point;self.cursor_success=success
        self.caps=caps;self.client_rect=(0,0,*size)
        if len(drop_bytes)>259 or b"\0"in drop_bytes:raise ValueError("drop buffer fixture bounds")
        self.machine.mem_write(self.drop_buffer,drop_bytes+b"\0"+bytes(259-len(drop_bytes)))
        if self.keyboard_slot is not None:self.write_u32(self.keyboard_slot,keyboard_disabled)
        if self.caps_guard is not None:self.write_u32(self.caps_guard,caps_guard)
        if self.cursor_enabled_slot is not None:self.write_u32(self.cursor_enabled_slot,enabled)
        for slot,value in self.containment_slots:
            self.write_u32(slot,size[0]if value=="width"else size[1]if value=="height"else value)
        for axis,value in zip(self.axes,home):self.write_u32(axis["home"],value)
        for slot,count in self.keyboard_clear:self.machine.mem_write(slot,b"\x7f"*count)
        entry=self.hash_entries[hash_]
        values=[(0,value&0xffffffff)for value in args]
        got=self.execute("syscallp"if discard else"syscall",list(reversed(values)),
            struct.pack("<IH",int(hash_,16),len(args)),budget=300000)
        if hash_=="c32e286f":
            result=[bytes(self.machine.mem_read(pointer+12,self.read_u32(pointer+8)-1)).hex()
                    for tag,pointer in got if tag==2]
        else:result=[native_number(tag,payload)for tag,payload in got]
        return {"result":result,"platform_calls":list(self.platform_calls),"moved":self.moved,
                "keyboard_disabled":self.read_u32(self.keyboard_slot)if self.keyboard_slot else None,
                "keyboard_buffers_cleared":all(bytes(self.machine.mem_read(slot,count))==bytes(count)
                    for slot,count in self.keyboard_clear),
                "caps_guard":native_number(0,self.read_u32(self.caps_guard))if self.caps_guard else None,
                "drag_flag":native_number(0,self.read_u32(self.drag_flag)),
                "drop_bytes":bytes(self.machine.mem_read(self.drop_buffer,260)).split(b"\0",1)[0].hex()}


def inspect():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch={r["sample"]:r["entries"]for r in
        json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    folder=archive_dir("platform_controls_recovery")
    for sample in samples:
        oracle=ScreenOracle(sample,dispatch[sample["sample"]]);f=oracle.functions
        lines=[]
        for entry in dispatch[sample["sample"]]:
            if entry["hash"]not in HASHES:continue
            lines.append(f"\n{entry['name']or entry['hash']} @{entry['selected_va']:08x}")
            body=f.body(f.syscall_helper(entry),maximum_bytes=2048)
            for row in body:lines.append(f"{row.address:08x} {row.mnemonic} {row.op_str}")
            calls=[row.operands[0].imm for row in body if row.mnemonic=="call"
                   and row.operands[0].type==capstone.CS_OP_IMM]
            for at in calls[:-1]:
                lines.append(f"CALLEE {at:08x}")
                for row in f.body(at,maximum_bytes=1024):
                    lines.append(f"{row.address:08x} {row.mnemonic} {row.op_str}")
        path=folder/(folder.name[:8]+"_"+sample["sample"].replace(" ","_")+".txt")
        path.write_text("\n".join(lines),encoding="utf8");print(path.relative_to(ROOT))


if __name__=="__main__":inspect()
