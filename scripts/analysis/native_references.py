"""Bounded x86 data references; candidates still require CFG validation."""
from __future__ import annotations

import struct

import capstone
import pefile


class NativeReferences:
    def __init__(self,oracle,path):
        self.oracle=oracle
        pe=pefile.PE(path)
        handlers=[row["selected_va"]for row in getattr(oracle,"rows",{}).values()]
        self.sections=[(pe.OPTIONAL_HEADER.ImageBase+section.VirtualAddress,section.get_data())
                       for section in pe.sections if section.Characteristics&0x20000000 or any(
                           pe.OPTIONAL_HEADER.ImageBase+section.VirtualAddress<=at<
                           pe.OPTIONAL_HEADER.ImageBase+section.VirtualAddress+section.Misc_VirtualSize
                           for at in handlers)]
        self.md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32);self.md.detail=True

    def instructions(self,value):
        found={}
        needle=struct.pack("<I",value)
        for base,raw in self.sections:
            cursor=0
            while True:
                at=raw.find(needle,cursor)
                if at<0:break
                cursor=at+1
                for start in range(max(0,at-11),at+1):
                    row=next(self.md.disasm(raw[start:start+16],base+start),None)
                    if row is None or row.address+row.size<base+at+4:continue
                    if any(operand.type==capstone.CS_OP_MEM and operand.mem.disp==value
                           for operand in row.operands)and row.disp_offset==at-start:
                        found[row.address]=row
        return[found[key]for key in sorted(found)]

    def functions(self,value,functions):
        result={}
        for reference in self.instructions(value):
            for base,raw in self.sections:
                if not base<=reference.address<base+len(raw):continue
                offset=reference.address-base
                for start in range(max(0,offset-160),offset+1):
                    prologue=raw[start:start+3]==b"\x55\x8b\xec"
                    aligned=(base+start)%16==0 and start>0 and raw[start-1]in(0xcc,0xc3,0x90)
                    if not(prologue or aligned):continue
                    address=base+start
                    try:body=functions.body(address,maximum_bytes=1024,maximum_instructions=250)
                    except(StopIteration,ValueError):continue
                    if any(row.address==reference.address for row in body):
                        result[address]=body
        return result
