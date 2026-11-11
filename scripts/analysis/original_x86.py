"""Bounded execution of original pure functions for independent semantic checks."""
from __future__ import annotations
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, sha256


class Original:
    BASE = 0x400000
    DATA = 0x10000000
    STACK = 0x20000000
    STOP = 0x30000000
    IDENTITY = "2bec58f9fd7f924b5ca403d894ec609a265a57efdfca25397f95a5752b3fcd46"

    def __init__(self):
        import pefile
        import unicorn
        from unicorn import x86_const
        self.reg = x86_const
        self.path = next(p for p in (ROOT / "bin").glob("*.exe") if p.name != "uninst.exe")
        if sha256(self.path) != self.IDENTITY:
            raise ValueError("original EXE identity changed")
        pe = pefile.PE(str(self.path))
        self.machine = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
        size = (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF
        self.machine.mem_map(self.BASE, size)
        self.machine.mem_write(self.BASE, pe.get_memory_mapped_image())
        self.machine.mem_map(self.DATA, 0x100000)
        self.machine.mem_map(self.STACK, 0x100000)
        self.machine.mem_map(self.STOP, 0x1000)
        self.cursor = self.DATA

    def memory(self, data: bytes, alignment=16):
        address = (self.cursor + alignment - 1) & -alignment
        if address + len(data) >= self.DATA + 0x100000:
            raise MemoryError("original fixture data budget")
        self.machine.mem_write(address, data)
        self.cursor = address + len(data)
        return address

    def call(self, address, args, count=100000):
        stack = self.STACK + 0x80000
        self.machine.mem_write(stack, struct.pack("<" + "I" * (len(args) + 1),
                                                 self.STOP, *(x & 0xFFFFFFFF for x in args)))
        self.machine.reg_write(self.reg.UC_X86_REG_ESP, stack)
        self.machine.reg_write(self.reg.UC_X86_REG_EBP, 0)
        self.machine.emu_start(address, self.STOP, count=count, timeout=1000000)
        if self.machine.reg_read(self.reg.UC_X86_REG_EIP) != self.STOP:
            raise RuntimeError("original function exceeded bounded execution")
        return self.machine.reg_read(self.reg.UC_X86_REG_EAX)

    def read(self, address, length):
        return bytes(self.machine.mem_read(address, length))

    def syscall(self,address,args,invalid_pages=False):
        import unicorn
        # Majiro 16-byte value slots: first argument is the top stack value.
        values=self.memory(bytes(16*(len(args)+2)))
        top=values+16*(len(args)-1)
        for index,value in enumerate(args):
            self.machine.mem_write(top-16*index,struct.pack("<4I",0,0,0,value&0xffffffff))

        def skip(uc,at,size,_):
            esp=uc.reg_read(self.reg.UC_X86_REG_ESP)
            return_to=struct.unpack("<I",bytes(uc.mem_read(esp,4)))[0]
            if at==0x44EC90:uc.reg_write(self.reg.UC_X86_REG_EAX,top)
            elif at==0x474AA0:uc.reg_write(self.reg.UC_X86_REG_EAX,int(invalid_pages))
            uc.reg_write(self.reg.UC_X86_REG_ESP,esp+4)
            uc.reg_write(self.reg.UC_X86_REG_EIP,return_to)

        hooks=[]
        try:
            for target in (0x44EC90,0x474AA0,0x4710E0,0x44EAB0,0x48A437):
                hooks.append(self.machine.hook_add(unicorn.UC_HOOK_CODE,skip,begin=target,end=target))
            return self.call(address,[len(args)],count=1000000)
        finally:
            for hook in hooks:self.machine.hook_del(hook)
