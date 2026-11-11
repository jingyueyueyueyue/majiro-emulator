"""Recover numbered SE controls and cached voice names from original engines.

The engine's channel arrays, shared request scalars, arithmetic and stream
record updates execute unchanged. Only VFS membership, decoder creation,
single-thread locks and explicit fast-mode input are host boundaries.
"""
from __future__ import annotations

import struct

import capstone
import unicorn
from unicorn import x86_const as reg

from original_extsound import ExtSoundOracle
from original_opcode import native_number


class NumberedAudioOracle(ExtSoundOracle):
    def stream_field(self,modern):
        # The pre-replacement stream has the same recovered tail relative to
        # pause, but lacks three words between active and gain.
        if modern>=0x65c:return self.stream+self.pause_offset+(modern-0x680)
        return self.stream+self.handle_offset+(modern-0x620)

    def __init__(self, sample, entries):
        super().__init__(sample, entries)
        f = self.functions
        self.fast = False
        self.numbered_plays = []
        self.dialogs = 0
        self.diagnostic_sink = lambda text: True

        def absolute(memory):
            return not memory.base and not memory.index and memory.disp >= 0x400000

        def string_array(name):
            body = f.body(f.syscall_helper(self.entries[name]))
            values = set()
            for row in body:
                if row.mnemonic == "lea":
                    for operand in row.operands:
                        if (operand.type == capstone.CS_OP_MEM and operand.mem.index
                                and operand.mem.scale == 4 and operand.mem.disp >= 0x400000):
                            values.add(operand.mem.disp)
                if (row.mnemonic == "add" and row.operands[0].type == capstone.CS_OP_REG
                        and row.operands[1].type == capstone.CS_OP_IMM
                        and row.operands[1].imm >= 0x400000):
                    values.add(row.operands[1].imm)
            if len(values) != 1:
                raise ValueError(f"original {name} string array changed: {values}")
            return values.pop()

        self.sound_names = string_array("$sound_ch_get_fn$")
        self.voice_names = string_array("$voice_ch_get_fn$")
        stop = f.body(f.syscall_helper(self.entries["$sound_ch_stop"]))
        handles = {operand.mem.disp for row in stop for operand in row.operands
                   if operand.type == capstone.CS_OP_MEM and not operand.mem.base
                   and operand.mem.index and operand.mem.scale == 4
                   and operand.mem.disp >= 0x400000}
        gains = {row.operands[0].mem.disp for row in stop if row.mnemonic == "mov"
                 and row.operands[0].type == capstone.CS_OP_MEM
                 and absolute(row.operands[0].mem)
                 and row.operands[1].type == capstone.CS_OP_IMM
                 and row.operands[1].imm == 1000}
        if len(handles) != 1 or len(gains) != 1:
            raise ValueError("original numbered SE handle/gain array changed")
        self.sound_handles = handles.pop()
        self.sound_gain = gains.pop()
        self.sound_pan = None
        if "$sound_ch_pan" in self.entries:
            pan = f.body(f.syscall_helper(self.entries["$sound_ch_pan"]))
            values = {row.operands[0].mem.disp for row in pan if row.mnemonic == "mov"
                      and row.operands[0].type == capstone.CS_OP_MEM
                      and absolute(row.operands[0].mem)
                      and row.operands[1].type == capstone.CS_OP_REG}
            if len(values) != 1:
                raise ValueError("original shared numbered SE pan changed")
            self.sound_pan = values.pop()
        volume = f.body(f.syscall_helper(self.entries["$sound_ch_vol"]))
        simple = []
        for row in volume:
            if row.mnemonic != "call" or row.operands[0].type != capstone.CS_OP_IMM:
                continue
            code = f.body(row.operands[0].imm)
            if (len(code) == 2 and code[0].mnemonic == "mov" and code[0].op_str.startswith("eax,")
                    and code[0].operands[1].type == capstone.CS_OP_MEM
                    and absolute(code[0].operands[1].mem) and code[1].mnemonic == "ret"):
                simple.append(code[0].operands[1].mem.disp)
        if not simple:
            simple = [row.operands[1].mem.disp for row in volume if row.mnemonic == "mov"
                      and row.operands[0].type == capstone.CS_OP_REG
                      and row.operands[1].type == capstone.CS_OP_MEM
                      and absolute(row.operands[1].mem)
                      and row.operands[1].mem.disp not in
                      (self.frame_global, self.context_global, *self.iat)]
        if len(set(simple)) != 1:
            raise ValueError("original numbered SE volume master getter changed")
        self.control_master_slot = simple[0]
        body = f.body(f.syscall_helper(self.entries["$sound_ch"]))
        decoder = []
        for index, row in enumerate(body):
            if (row.mnemonic != "mov" or row.operands[0].type != capstone.CS_OP_MEM
                    or row.operands[0].mem.disp != self.sound_handles
                    or not row.operands[0].mem.index
                    or row.operands[1].type != capstone.CS_OP_REG
                    or row.operands[1].reg != capstone.x86.X86_REG_EAX):
                continue
            previous = [item for item in body[max(0, index-5):index]
                        if item.mnemonic == "call" and item.operands[0].type == capstone.CS_OP_IMM]
            if previous:
                call = previous[-1]
                following = f.instruction(call.address+call.size)
                if following.mnemonic != "add" or following.op_str not in ("esp, 0xc", "esp, 4", "esp, 8"):
                    raise ValueError("original numbered SE decoder calling convention changed")
                decoder.append((call.operands[0].imm, following.operands[1].imm))
        if len(set(decoder)) != 1:
            raise ValueError(f"original numbered SE decoder entry changed: {decoder}")
        self.numbered_decoder, self.numbered_cleanup = decoder[0]
        if self.numbered_decoder == self.play_va:
            self.machine.hook_del(self.play_hook)

        def play(uc, at, size, unused):
            stack = uc.reg_read(reg.UC_X86_REG_ESP)
            if self.numbered_cleanup == 12:
                filename, gain, loop = struct.unpack("<3I", uc.mem_read(stack+4, 12))
            elif self.numbered_cleanup == 8:
                filename = uc.reg_read(reg.UC_X86_REG_ECX)
                gain, loop = struct.unpack("<2I", uc.mem_read(stack+4, 8))
            else:
                filename = uc.reg_read(reg.UC_X86_REG_ECX)
                gain = uc.reg_read(reg.UC_X86_REG_EDX)
                loop = self.read_u32(stack+4)
            self.numbered_plays.append({"filename": self._input(filename, -1).value.decode("cp932"),
                                       "gain": native_number(0, gain), "loop": native_number(0, loop)})
            self._return(0, self.transport_handle)

        self.machine.hook_add(unicorn.UC_HOOK_CODE, play,
                              begin=self.numbered_decoder, end=self.numbered_decoder)
        fadeout = f.body(f.syscall_helper(self.entries["$sound_ch_fadeout"]))
        leading = f.instruction(f.syscall_helper(self.entries["$sound_ch_get_fn$"]))
        accessor = leading.operands[0].imm if leading.mnemonic == "call" else None
        calls = [row.operands[0].imm for row in fadeout if row.mnemonic == "call"
                 and row.operands[0].type == capstone.CS_OP_IMM and row.operands[0].imm != accessor]
        self.fast_va = calls[0]

        def fast(uc, at, size, unused):
            self._return(0, int(self.fast))

        self.machine.hook_add(unicorn.UC_HOOK_CODE, fast, begin=self.fast_va, end=self.fast_va)
        # Old builds display a missing-file diagnostic after the formatter.
        # The return value is not inspected; the host dialog is an explicit boundary.
        dialogs = []
        for row in body:
            if row.mnemonic != "call" or row.operands[0].type != capstone.CS_OP_IMM:
                continue
            after = f.instruction(row.address+row.size)
            if after.mnemonic == "add" and after.op_str == "esp, 0x10":
                dialogs.append(row.operands[0].imm)
        self.dialog_boundaries = list(dict.fromkeys(dialogs))

        def dialog(uc, at, size, unused):
            self.dialogs += 1
            self._return(0, 0)

        for at in self.dialog_boundaries:
            self.machine.hook_add(unicorn.UC_HOOK_CODE, dialog, begin=at, end=at)

    def _import(self, uc, at, size, unused):
        if self.import_names.get(at) in ("timeGetTime", "GetTickCount"):
            return self._return(0, 2000)
        return super()._import(uc, at, size, unused)

    def prepare_numbered(self, channel, previous=True, gain=1000, pan=0, master=1000,
                         control_master=1000, disabled=False, ultra=False, fast=False,
                         request="previous", active=True, completed=False):
        self.disabled = disabled
        self.fast = fast
        self.numbered_plays = []
        self.lookups = []
        self.reset_music(previous)
        self.machine.mem_write(self.sound_handles, bytes(20*4))
        self.write_u32(self.sound_handles+channel*4, self.music_handle if previous else 0)
        for base in (self.sound_names, self.voice_names):
            self.machine.mem_write(base, bytes(20*0x104))
            self.machine.mem_write(base+channel*0x104, request.encode("cp932")+b"\0")
        self.write_u32(self.sound_gain, gain)
        if self.sound_pan is not None:
            self.write_u32(self.sound_pan, pan)
        self.write_u32(self.master_slot, master)
        self.write_u32(self.control_master_slot, control_master)
        self.write_u32(self.enabled_slot, 1)
        if self.ultra_slot is not None:self.write_u32(self.ultra_slot, int(ultra))
        self.write_u32(self.stream_field(0x650), int(active))
        self.write_u32(self.stream_field(0x648), int(completed))
        self.write_u32(self.stream_field(0x65c), 777)
        self.write_u32(self.stream_field(0x670), 0)
        self.write_u32(self.stream_field(0x674), 0)
        self.write_u32(self.stream_field(0x688), 0)
        self.write_u32(self.stream_field(0x624), 1)
        for address in self.vfs_ready:
            self.write_u32(address, 1)

    def call_numbered(self, name, args, discard=False):
        values = []
        for index, value in enumerate(reversed(args)):
            values.append((2, self.string(value, index)) if isinstance(value, str) else (0, value))
        entry = self.entries[name]
        result = self.execute("syscallp" if discard else "syscall", values,
                              struct.pack("<IH", int(entry["hash"], 16), len(args)), budget=500000)
        if name.endswith("$"):
            result = [self.string_result(*value) for value in result]
        else:
            result = [native_number(*value) for value in result]
        channel = args[0]
        return {"result": result, "handle": self.read_u32(self.sound_handles+channel*4),
                "gain": native_number(0, self.read_u32(self.sound_gain)),
                "pan": native_number(0, self.read_u32(self.sound_pan)) if self.sound_pan else None,
                "active": bool(self.read_u32(self.stream_field(0x650))),
                "stream_gain": native_number(0, self.read_u32(self.stream_field(0x65c))),
                "pan_requested": bool(self.read_u32(self.stream_field(0x688))),
                "stream_pan": native_number(0, self.read_u32(self.stream_field(0x68c))),
                "fade_requested": bool(self.read_u32(self.stream_field(0x674))),
                "fade_duration": native_number(0, self.read_u32(self.stream_field(0x664))),
                "fade_gain": native_number(0, self.read_u32(self.stream_field(0x668)))
                             + native_number(0, self.read_u32(self.stream_field(0x66c))),
                "plays": list(self.numbered_plays), "lookups": list(self.lookups),
                "request": self._input(self.sound_names+channel*0x104, -1).value.decode("cp932")}
