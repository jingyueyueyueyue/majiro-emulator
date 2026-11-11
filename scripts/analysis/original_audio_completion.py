"""Original replacement requests, completion latch and asynchronous audio waits."""
from __future__ import annotations

import struct
import capstone
import unicorn
from unicorn import x86_const as reg

from original_audio_remaining import NumberedAudioOracle
from original_opcode import native_number


class AudioCompletionOracle(NumberedAudioOracle):
    def __init__(self, sample, entries):
        super().__init__(sample, entries)
        self.clock = 2000
        f = self.functions
        self.recovered = {}
        for name in ("$music_replace", "$extmusic_replace", "$music_fadeout_wait",
                     "$music_stat_reset", "$sound_ch_wait"):
            if name not in self.entries:
                continue
            address = f.syscall_helper(self.entries[name])
            self.recovered[name] = address
        wait_body = f.body(self.recovered["$sound_ch_wait"])
        candidates = []
        for row in wait_body:
            if row.mnemonic != "call" or row.operands[0].type != capstone.CS_OP_IMM:
                continue
            at = row.operands[0].imm
            if at == self.fast_va:
                continue
            body = f.body(at)
            # Reuse the existing recovered fast-mode boundary whenever direct.
            if any(item.address == self.fast_va for item in body[:1]):
                candidates.append(at)
        if self.fast_va not in [row.operands[0].imm for row in wait_body
                                if row.mnemonic == "call" and row.operands[0].type == capstone.CS_OP_IMM]:
            # wait's second logical query follows the stream status call.
            calls = [row.operands[0].imm for row in wait_body if row.mnemonic == "call"
                     and row.operands[0].type == capstone.CS_OP_IMM]
            status_index = next((i for i, at in enumerate(calls)
                                 if any(item.mnemonic == "mov" and "0x650" in item.op_str
                                        for item in f.body(at))), None)
            if status_index is not None and status_index+1 < len(calls):
                self.wait_fast_va = calls[status_index+1]
            else:
                # All supported builds have a direct fast query whose body
                # reads the same input globals as the ordinary wait helper.
                self.wait_fast_va = self.fast_va
            if self.wait_fast_va != self.fast_va:
                def fast(uc, at, size, unused): self._return(0, int(self.fast))
                self.machine.hook_add(unicorn.UC_HOOK_CODE, fast,
                                      begin=self.wait_fast_va, end=self.wait_fast_va)
        self.tick_global = None
        # New builds inline the script tick; older ones call a 2-instruction getter.
        for row in wait_body:
            if row.mnemonic == "mov" and len(row.operands) == 2:
                op = row.operands[1]
                if op.type == capstone.CS_OP_MEM and not op.mem.base and not op.mem.index:
                    after = f.instruction(row.address+row.size)
                    if after.mnemonic == "add" and after.op_str.endswith("0xa"):
                        self.tick_global = op.mem.disp
        if self.tick_global is None:
            for index, row in enumerate(wait_body):
                if row.mnemonic != "call" or row.operands[0].type != capstone.CS_OP_IMM:
                    continue
                getter = f.body(row.operands[0].imm)
                if (len(getter) == 2 and getter[0].mnemonic == "mov"
                        and getter[0].operands[1].type == capstone.CS_OP_MEM
                        and not getter[0].operands[1].mem.base):
                    after = wait_body[index+1:index+5]
                    if any(item.mnemonic == "add" and item.op_str.endswith("0xa") for item in after):
                        self.tick_global = getter[0].operands[1].mem.disp
        self.last_frame_wait = None
        self.direct_stop = 0x33000000
        self.machine.mem_map(self.direct_stop, 4096)

    def _import(self, uc, at, size, unused):
        if self.import_names.get(at) in ("timeGetTime", "GetTickCount"):
            return self._return(0, self.clock)
        return super()._import(uc, at, size, unused)

    def prepare_completion(self, present=True, active=True, completed=False, fast=False):
        self.prepare_numbered(7, previous=present, active=active, completed=completed, fast=fast)
        self.reset_music(present)
        self.write_u32(self.sound_handles+7*4, self.music_handle if present else 0)
        self.write_u32(self.stream+0x650, int(active))
        self.write_u32(self.stream+0x648, int(completed))
        self.write_u32(self.stream+0x624, 1)
        self.write_u32(self.stream+0x654, 0)
        self.write_u32(self.stream+0x658, 0xABCD)
        self.write_u32(self.stream+0x67C, 1)
        self.write_u32(self.stream+0x698, 0)
        self.write_u32(self.stream+0x674, 0)
        self.write_u32(self.stream+0x65C, 777)
        if self.tick_global:
            self.write_u32(self.tick_global, self.clock)
        for at in self.vfs_ready:
            self.write_u32(at, 1)
        self.files = {"fixture.wav", "loop.ogg"}

    def call_completion(self, name, arguments, discard=False, deadline=0):
        values = [(2, self.string(value, index)) if isinstance(value, str) else (0, value)
                  for index, value in enumerate(reversed(arguments))]
        entry = self.entries[name]
        wait_offset = self.frame_stride-8
        if name in self.recovered:
            uc = self.machine
            uc.context_restore(self.initial)
            uc.mem_write(self.context, bytes(0x28))
            uc.mem_write(self.frame, bytes(0x50))
            uc.mem_write(self.values, bytes(256))
            self.write_u32(self.frame_global, self.frame)
            self.write_u32(self.context_global, self.context)
            self.write_u32(self.context+0x24, self.values)
            self.write_u32(self.context+0x10, 1024)
            self.write_u32(self.frame+8, len(values))
            self.write_u32(self.frame+wait_offset, deadline)
            for i, (tag, bits) in enumerate(values):
                uc.mem_write(self.values+i*16, struct.pack("<4I", 0, tag, 0, bits & 0xFFFFFFFF))
            stack = self.STACK+0xF000
            uc.mem_write(stack, struct.pack("<II", self.direct_stop, len(values)))
            uc.reg_write(reg.UC_X86_REG_ESP, stack)
            uc.reg_write(reg.UC_X86_REG_ECX, len(values))
            uc.emu_start(self.recovered[name], self.direct_stop, count=100000)
            if uc.reg_read(reg.UC_X86_REG_EIP) != self.direct_stop:
                raise ValueError("original channel wait exceeded bounded helper")
            pending = name in ("$sound_ch_wait", "$music_fadeout_wait") and uc.reg_read(reg.UC_X86_REG_EAX) == 1
            result = [] if pending or discard else [(0, 0)]
        else:
            result = self.execute("syscallp" if discard else "syscall", values,
                                  struct.pack("<IH", int(entry["hash"], 16), len(arguments)),
                                  frame_fields={wait_offset: deadline}, budget=700000)
            pending = name == "$music_fadeout_wait" and bool(self.read_u32(self.frame+wait_offset))
        first = bytes(self.machine.mem_read(self.stream+0x410, 260)).split(b"\0", 1)[0]
        second = bytes(self.machine.mem_read(self.stream+0x514, 260)).split(b"\0", 1)[0]
        return {"result": [(tag, native_number(tag, bits)) for tag, bits in result],
                "pending": pending,
                "wait_deadline": self.read_u32(self.frame+wait_offset),
                "music_handle": self.read_u32(self.music_slot),
                "sound_handle": self.read_u32(self.sound_handles+7*4),
                "active": bool(self.read_u32(self.stream+0x650)),
                "completed": bool(self.read_u32(self.stream+0x648)),
                "replace_requested": bool(self.read_u32(self.stream+0x654)),
                "replace_flags": native_number(0, self.read_u32(self.stream+0x658)),
                "replace_first": first.decode("cp932"), "replace_second": second.decode("cp932"),
                "replacement_finished": self.read_u32(self.stream+0x67C),
                "fade_requested": bool(self.read_u32(self.stream+0x674)),
                "fade_duration": native_number(0, self.read_u32(self.stream+0x664))}
