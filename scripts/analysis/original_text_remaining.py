"""Recover original font-output stack and text configuration interfaces."""
from __future__ import annotations

from pathlib import Path
import struct
import sys

import capstone
import unicorn
from unicorn import x86_const as reg

from original_text import TextOracle
from original_opcode import native_number

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))


class FontoutOracle(TextOracle):
    def __init__(self, sample, entries):
        super().__init__(sample, entries)
        self.registry_values = {}
        self.registry_reads = []
        self.default_page = self.DATA + 0x4c000
        f = self.functions
        body = self.entry_body("$fontout_push")
        if not any(row.mnemonic == "rep movsd" for row in body):
            children = [f.body(row.operands[0].imm) for row in body
                        if row.mnemonic == "call" and row.operands[0].type == capstone.CS_OP_IMM]
            copies = [child for child in children if any(row.mnemonic == "rep movsd" for row in child)]
            if len(copies) == 1:
                body = copies[0]
        copies = [i for i, row in enumerate(body) if row.mnemonic == "rep movsd"]
        if len(copies) != 1:
            raise ValueError("original font-output template copy changed")
        copy = copies[0]
        sources = [row.operands[1].imm for row in body[:copy] if row.mnemonic == "mov"
                   and row.op_str.startswith("esi,") and row.operands[1].type == capstone.CS_OP_IMM]
        lengths = [row.operands[1].imm * 4 for row in body[:copy] if row.mnemonic == "mov"
                   and row.op_str.startswith("ecx,") and row.operands[1].type == capstone.CS_OP_IMM]
        if len(sources) != 1 or len(lengths) != 1 or lengths[0] not in (0x6c, 0x70):
            raise ValueError("original font-output template layout changed")
        self.template, self.node_size = sources[0], lengths[0]
        self.link_offset = self.node_size - 4
        self.context_offset = None
        for row in body[copy+1:]:
            for operand in row.operands:
                if (operand.type == capstone.CS_OP_MEM and operand.mem.base
                        and operand.mem.disp in (0x4428, 0x4c28, 0x5028)):
                    if self.context_offset not in (None, operand.mem.disp):
                        raise ValueError("font-output context layout is ambiguous")
                    self.context_offset = operand.mem.disp
        if self.context_offset is None:
            raise ValueError("original font-output context field missing")
        allocation = next(row.operands[0].imm for row in body[:copy] if row.mnemonic == "call")
        if allocation != self.allocator_va:
            self.machine.hook_add(unicorn.UC_HOOK_CODE, self._allocate, begin=allocation, end=allocation)
        later_calls = [row.operands[0].imm for row in body[copy+1:]
                       if row.mnemonic == "call" and row.operands[0].type == capstone.CS_OP_IMM]
        if len(later_calls) in (1, 2):
            page_getter = later_calls[0]
            def page_hook(uc, at, size, unused):
                self._return(0, self.default_page)
            self.machine.hook_add(unicorn.UC_HOOK_CODE, page_hook, begin=page_getter, end=page_getter)
            self.page_slot = None
        elif not later_calls:
            page_slots = [row.operands[1].mem.disp for row in body[:copy]
                          if row.mnemonic == "mov" and row.operands[1].type == capstone.CS_OP_MEM
                          and not row.operands[1].mem.base and not row.operands[1].mem.index]
            if len(page_slots) != 1:
                raise ValueError("original inline font-output page getter changed")
            self.page_slot = page_slots[0]
        else:
            raise ValueError("original font-output page initialization changed")
        pop = self.entry_body("$fontout_pop")
        frees = [row.operands[0].imm for row in pop if row.mnemonic == "call"
                 and row.operands[0].type == capstone.CS_OP_IMM]
        if not frees:
            raise ValueError("original font-output pop deallocator missing")
        def free_hook(uc, at, size, unused):
            pointer = self.read_u32(uc.reg_read(reg.UC_X86_REG_ESP) + 4)
            if pointer not in self.guest_allocations:
                raise ValueError("font-output pop freed an unknown node")
            self.guest_allocations.pop(pointer)
            self._return(0)
        self.machine.hook_add(unicorn.UC_HOOK_CODE, free_hook, begin=frees[0], end=frees[0])
        locate = self.entry_body("$fontout_locate")
        validations = []
        for index, row in enumerate(locate[:-2]):
            if (row.mnemonic == "call" and row.operands[0].type == capstone.CS_OP_IMM
                    and locate[index+1].mnemonic == "add" and locate[index+1].op_str == "esp, 4"
                    and locate[index+2].mnemonic == "test" and locate[index+2].op_str == "eax, eax"):
                validations.append(row.operands[0].imm)
        self.valid_pages_head = None
        if len(validations) == 1:
            # TextOracle already intercepts this same page-validity helper.
            # A second callback would return twice and corrupt the guest stack.
            console = self.functions.body(self.functions.syscall_helper(self.entries["$console_set"]))
            if not any(row.mnemonic == "call" and row.operands[0].type == capstone.CS_OP_IMM
                       and row.operands[0].imm == validations[0] for row in console):
                raise ValueError("font-output and console destination validators differ")
        elif not validations:
            candidates = {operand.mem.disp for row in locate for operand in row.operands
                if operand.type == capstone.CS_OP_MEM and not operand.mem.base
                and not operand.mem.index and operand.mem.disp not in (
                    self.frame_global, self.context_global, self.page_slot)}
            if len(candidates) != 1:
                raise ValueError("font-output inline destination list changed")
            self.valid_pages_head = candidates.pop()
        else:
            raise ValueError("font-output destination validation boundary changed")
        self.log_voice_slots = None
        if "$is_log_no_voice" in self.entries:
            slots = []
            for row in self.entry_body("$is_log_no_voice"):
                code = self.functions.body(row.operands[0].imm) if (
                    row.mnemonic == "call" and row.operands[0].type == capstone.CS_OP_IMM) else [row]
                if len(code) > 4:
                    continue
                for item in code:
                    for operand in item.operands:
                        if (operand.type == capstone.CS_OP_MEM and not operand.mem.base
                                and not operand.mem.index and operand.mem.disp not in self.iat):
                            slots.append(operand.mem.disp)
            slots = list(dict.fromkeys(slots))
            if len(slots) == 1 and self.production_slot not in slots:
                slots.insert(0, self.production_slot)
            if len(slots) != 2 or self.production_slot not in slots:
                raise ValueError("original missing-voice logging configuration layout changed")
            self.log_voice_slots = {"production": self.production_slot,
                "value": next(slot for slot in slots if slot != self.production_slot)}
        self.reset()

    def _import(self, uc, at, size, unused):
        name = self.import_names.get(at)
        stack = uc.reg_read(reg.UC_X86_REG_ESP)
        if name == "RegOpenKeyExA":
            parent, key, reserved, access, output = struct.unpack("<5I", uc.mem_read(stack + 4, 20))
            self.write_u32(output, 0x1357)
            self._return(5, 0)
            return
        if name == "RegQueryValueExA":
            key, value_name, reserved, kind, output, length = struct.unpack(
                "<6I", uc.mem_read(stack + 4, 24))
            value = self._input(value_name, -1).value.decode("cp932").lower()
            self.registry_reads.append(value)
            if value not in self.registry_values:
                self._return(6, 2)
                return
            raw = str(self.registry_values[value]).encode("cp932") + b"\0"
            capacity = self.read_u32(length)
            self.write_u32(length, len(raw))
            if kind:
                self.write_u32(kind, 1)
            if output:
                if capacity < len(raw):
                    self._return(6, 234)
                    return
                uc.mem_write(output, raw)
            self._return(6, 0)
            return
        if name == "RegCloseKey":
            self._return(1, 0)
            return
        return super()._import(uc, at, size, unused)

    def reset(self):
        self.write_u32(self.context + self.context_offset, 0)
        if self.orientation_slot:
            self.write_u32(self.orientation_slot, 0)
        if self.page_slot:
            self.write_u32(self.page_slot, self.default_page)
        if self.valid_pages_head:
            self.write_u32(self.valid_pages_head, self.default_page)
            self.write_u32(self.default_page + 0x38, self.default_page + 0x100)
            self.write_u32(self.default_page + 0x138, 0)
        # Preserve CRT objects allocated during StringOracle startup.
        # Reset only the temporary font-output nodes between cases.
        self.heap = getattr(self,"fixture_heap_start",self.DATA + 0x70000)
        self.guest_allocations = dict(getattr(self,"permanent_allocations",{}))

    def call_fontout(self, name, args=(), discard=False):
        values = []
        for index, arg in enumerate(args):
            values.append((2, self.string(arg, index)) if isinstance(arg, str)
                          else (0, int(arg) & 0xffffffff))
        entry = self.entries[name]
        return self.execute("syscallp" if discard else "syscall", list(reversed(values)),
            struct.pack("<IH", int(entry["hash"], 16), len(values)),
            budget=500000, preserve_heap=True)

    def stack(self):
        result = []
        at = self.read_u32(self.context + self.context_offset)
        while at:
            if len(result) >= 32:
                raise ValueError("font-output fixture stack exceeds bound")
            number = lambda offset: native_number(0, self.read_u32(at + offset))
            result.append({"face": self._input(at, -1).value.decode("cp932"),
                "height": number(0x28), "width": number(0x2c), "step": number(0x30),
                "style": number(0x20), "color": number(0x34), "shadow": number(0x38),
                "page": number(0x44), "x": number(0x48), "y": number(0x4c)})
            at = self.read_u32(at + self.link_offset)
        return result

