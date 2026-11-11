"""Original Ruri movie wrappers with file/config/UI/decoder boundaries."""
from __future__ import annotations

import struct
import unicorn
from unicorn import x86_const as reg

from original_x86 import Original


class MovieCompletionOracle(Original):
    FUNCTIONS = {"$movie_play_fromto": 0x44AA00, "$movie_play_repeat": 0x44AC00,
                 "$movie_texture_play": 0x44AE40, "$movie_texture": 0x44AE30,
                 "$movie_texture_volume": 0x44AF70, "$movie_texture_stop": 0x44AF60}

    def __init__(self):
        super().__init__()
        self.events = []
        self.page = 321
        self.context = self.DATA+0x90000
        self.write32(0x5D1A58, self.context)
        self.write32(self.context+0x14, 0)
        self.tops = 0
        def ret(uc, at, size, unused):
            value = 0
            if at == 0x44EC90: value = self.tops
            elif at == 0x488F86: value = 1
            elif at == 0x4551A0: value = 1280
            elif at == 0x455170: value = 720
            elif at == 0x482EA0: value = self.page
            esp = uc.reg_read(reg.UC_X86_REG_ESP)
            target = self.word(esp)
            uc.reg_write(reg.UC_X86_REG_EAX, value)
            uc.reg_write(reg.UC_X86_REG_ESP, esp+4)
            uc.reg_write(reg.UC_X86_REG_EIP, target)
        for at in (0x44EC90, 0x4559A0, 0x455E70, 0x455D90, 0x474210, 0x4740C0,
                   0x488F86, 0x488D0C, 0x46C3C0, 0x47F790, 0x476F40, 0x473E00,
                   0x4551A0, 0x455170, 0x44EAB0, 0x48A437, 0x482EA0):
            self.machine.hook_add(unicorn.UC_HOOK_CODE, ret, begin=at, end=at)
        def transport(uc, at, size, unused):
            esp = uc.reg_read(reg.UC_X86_REG_ESP)
            count = {0x47E5B0: 7, 0x47E640: 5, 0x483000: 4, 0x4838E0: 0}[at]
            values = [self.word(esp+4+i*4) for i in range(count)]
            if values:
                raw = bytes(uc.mem_read(values[0], 512)).split(b"\0", 1)[0]
                self.events.append({"filename": raw.decode("cp932"), "arguments": values[1:]})
            else:
                self.events.append({"action": "texture_stop"})
                self.page = 0
            ret(uc, at, size, unused)
        for at in (0x47E5B0, 0x47E640, 0x483000, 0x4838E0):
            self.machine.hook_add(unicorn.UC_HOOK_CODE, transport, begin=at, end=at)

    def write32(self, at, value):
        self.machine.mem_write(at, struct.pack("<I", value & 0xFFFFFFFF))

    def word(self, at):
        return struct.unpack("<I", self.machine.mem_read(at, 4))[0]

    def invoke(self, name, values):
        self.cursor = self.DATA
        args = []
        for value in values:
            if isinstance(value, str):
                raw = value.encode("cp932")+b"\0"
                value = self.memory(struct.pack("<3I", 64, 0, len(raw))+raw)
            args.append(value)
        slots = self.memory(bytes(16*(len(args)+2)))
        self.tops = slots+16*(len(args)-1)
        for index, value in enumerate(args):
            self.machine.mem_write(self.tops-index*16, struct.pack("<4I", 0, 0, 0, value & 0xFFFFFFFF))
        self.events = []
        result = self.call(self.FUNCTIONS[name], [len(args)], count=300000)
        return {"events": list(self.events), "native_eax": result,
                "texture_volume": self.word(0x4C9EC0)}
