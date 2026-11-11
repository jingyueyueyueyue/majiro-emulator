"""Execute one recovered opcode in each original interpreter.

Only the next interpreter-loop boundary is intercepted. Operand reads, stack
updates, arithmetic and conversions execute the unmodified x86 instructions.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct

import capstone
import pefile
import unicorn
from unicorn import x86_const as reg


class OpcodeOracle:
    DATA = 0x10000000
    STACK = 0x20000000

    def __init__(self, sample):
        raw = Path(sample["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sample["exe_sha256"]:
            raise ValueError("opcode recovery does not match the original EXE")
        from original_reference import comparison_bytes
        raw, self.reference_provenance = comparison_bytes(sample, raw)
        pe = pefile.PE(data=raw)
        self.machine = uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
        uc.mem_map(pe.OPTIONAL_HEADER.ImageBase,
                   (pe.OPTIONAL_HEADER.SizeOfImage + 4095) & ~4095)
        uc.mem_write(pe.OPTIONAL_HEADER.ImageBase, pe.get_memory_mapped_image())
        uc.mem_map(self.DATA, 0x100000)
        uc.mem_map(self.STACK, 0x10000)
        self.rows = {row["name"]: row for row in sample["active_opcodes"]}
        self.loop = self.rows["nop.191"]["selected_va"]
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        md.detail = True
        if sample.get("fetch_layout") == "separate_signed_word_helper":
            # In early engines nop.191 has its own no-op call wrapper. Enter
            # at the verified fetch-helper call, not that wrapper; all later
            # opcode handlers return to this same call through the scheduler.
            start = sample["selection_start_va"] - 5
            call = next(md.disasm(bytes(uc.mem_read(start, 5)), start))
            helper = list(md.disasm(bytes(uc.mem_read(call.operands[0].imm, 32)),
                                   call.operands[0].imm)) if call.mnemonic == "call" else []
            if not any(row.address == sample["fetch_va"] and row.mnemonic == "movsx" for row in helper):
                raise ValueError("early original fetch-helper call no longer matches")
            self.loop = start
        mul = self.rows["mul.i"]["selected_va"]
        first = next(md.disasm(bytes(uc.mem_read(mul, 16)), mul))
        if first.mnemonic == "call" and first.operands[0].type == capstone.CS_OP_IMM:
            accessor = first.operands[0].imm
        elif first.mnemonic == "mov":
            accessor = mul  # The onetwo build inlines the operand-stack accessor.
        else:
            raise ValueError("unrecognized original stack accessor")
        # Some builds put arithmetic in a helper before calling the same
        # stack accessor. Follow only a bounded chain of leading direct calls.
        for _ in range(3):
            leading = next(md.disasm(bytes(uc.mem_read(accessor, 16)), accessor))
            if leading.mnemonic != "call":
                break
            if leading.operands[0].type != capstone.CS_OP_IMM:
                raise ValueError("indirect original stack-accessor wrapper")
            accessor = leading.operands[0].imm
        globals_ = []
        for ins in md.disasm(bytes(uc.mem_read(accessor, 80)), accessor):
            if ins.mnemonic == "ret":
                break
            if ins.mnemonic == "mov" and len(ins.operands) == 2:
                value = ins.operands[1]
                if (value.type == capstone.CS_OP_MEM and not value.mem.base
                        and not value.mem.index):
                    if value.mem.disp not in globals_:
                        globals_.append(value.mem.disp)
                    if len(globals_) == 2:
                        break
        if len(globals_) != 2 or globals_[0] == globals_[1]:
            raise ValueError("original frame/context globals are ambiguous")
        self.frame_global, self.context_global = globals_
        return_address = self.rows["ret"]["selected_va"]
        first_return = next(md.disasm(bytes(uc.mem_read(return_address,16)), return_address))
        if first_return.mnemonic != "call":
            raise ValueError("original return wrapper is unrecognized")
        return_address = first_return.operands[0].imm
        strides = set()
        for ins in md.disasm(bytes(uc.mem_read(return_address,240)), return_address):
            if ins.mnemonic == "mov" and len(ins.operands) == 2:
                source = ins.operands[1]
                if source.type == capstone.CS_OP_MEM and source.mem.base and source.mem.disp in (0x4c,0x54,0x58):
                    strides.add(source.mem.disp-8)
        if len(strides) != 1:
            raise ValueError("original frame stride is ambiguous")
        self.frame_stride = strides.pop()
        self.context = self.DATA
        self.frame = self.context + 0x28
        self.values = self.DATA + 0x10000
        self.operand = self.DATA + 0x20000
        self.write_u32(self.frame_global, self.frame)
        self.write_u32(self.context_global, self.context)
        self.write_u32(self.context + 0x24, self.values)
        # SSE conversion uses the original default rounding mode.
        uc.reg_write(reg.UC_X86_REG_MXCSR, 0x1F80)
        self.initial = uc.context_save()
        self.loop_visits = 0
        self.stop_after = 1

        def loop_boundary(machine, address, size, unused):
            self.loop_visits += 1
            if self.loop_visits == self.stop_after + 1:
                machine.emu_stop()

        uc.hook_add(unicorn.UC_HOOK_CODE, loop_boundary, begin=self.loop, end=self.loop)

    def write_u32(self, address, value):
        self.machine.mem_write(address, struct.pack("<I", value & 0xFFFFFFFF))

    def read_u32(self, address):
        return struct.unpack("<I", self.machine.mem_read(address, 4))[0]

    def execute(self, name, values, operands=b"", budget=2000,
                frame_fields=None, context_fields=None, setup=None, instructions=1):
        """values are (native type tag, raw 32-bit payload) pairs."""
        uc = self.machine
        uc.context_restore(self.initial)
        uc.mem_write(self.context, bytes(0x28))
        uc.mem_write(self.context + 0x5034, bytes(16))
        uc.mem_write(self.frame, bytes(0x50))
        uc.mem_write(self.values, bytes(16 * 16))
        instruction = struct.pack("<H", int(self.rows[name]["opcode"], 16)) + operands
        uc.mem_write(self.operand - 2, instruction + bytes(256))
        self.write_u32(self.frame, self.operand - 2)
        self.write_u32(self.frame_global, self.frame)
        self.write_u32(self.context_global, self.context)
        self.write_u32(self.context + 0x24, self.values)
        self.write_u32(self.context + 0x10, 1024)
        self.write_u32(self.frame + 8, len(values))
        for index, (type_tag, payload) in enumerate(values):
            uc.mem_write(self.values + index * 16,
                         struct.pack("<4I", 0, type_tag, 0, payload & 0xFFFFFFFF))
        for offset, value in (frame_fields or {}).items():
            self.write_u32(self.frame + offset, value)
        for offset, value in (context_fields or {}).items():
            self.write_u32(self.context + offset, value)
        if setup:
            setup(self)
        uc.reg_write(reg.UC_X86_REG_ESP, self.STACK + 0xF000)
        uc.mem_write(self.STACK, bytes(0x10000))
        uc.reg_write(reg.UC_X86_REG_EFLAGS, 2)
        # Enter through the real fetch loop: optimized builds keep live frame
        # registers across dispatch and their handlers may depend on them.
        self.loop_visits = 0
        if not 1 <= instructions <= 256:
            raise ValueError("original fixture instruction count exceeds bound")
        self.stop_after = instructions
        # The instruction budget is the bound. A per-case watchdog thread
        # adds a Windows timer quantum to thousands of tiny pure functions.
        uc.emu_start(self.loop, 0, count=budget)
        if self.loop_visits != instructions + 1 or uc.reg_read(reg.UC_X86_REG_EIP) != self.loop:
            raise RuntimeError(f"{name}: original instruction budget exhausted")
        count = self.read_u32(self.frame + 8)
        if count > 16:
            raise ValueError(f"{name}: invalid original operand-stack depth {count}")
        return [
            (self.read_u32(self.values + i * 16 + 4),
             self.read_u32(self.values + i * 16 + 12))
            for i in range(count)
        ]


def f32_bits(value):
    try:
        return struct.unpack("<I", struct.pack("<f", value))[0]
    except OverflowError:
        return 0xFF800000 if value < 0 else 0x7F800000


def native_number(tag, payload):
    if tag == 0:
        return struct.unpack("<i", struct.pack("<I", payload))[0]
    if tag == 1:
        return struct.unpack("<f", struct.pack("<I", payload))[0]
    raise ValueError(f"not a numeric native value: {tag}")
