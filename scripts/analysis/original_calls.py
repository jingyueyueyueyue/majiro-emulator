"""Original script registry, lookup and diagnostic boundaries for call tests."""
from __future__ import annotations

import struct

import capstone
import unicorn
from unicorn import x86_const as reg

from original_strings import StringOracle


class OriginalDiagnostic(RuntimeError):
    pass


class CallOracle(StringOracle):
    def __init__(self, sample):
        super().__init__(sample)
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        md.detail = True

        def rows(at, length=400):
            return list(md.disasm(bytes(self.machine.mem_read(at, length)), at))

        wrapper = rows(self.rows["call"]["selected_va"], 32)
        call_helper = next(ins.operands[0].imm for ins in wrapper if ins.mnemonic == "call")
        body = []
        for ins in rows(call_helper):
            body.append(ins)
            if ins.mnemonic == "ret":
                break
        calls = [ins.operands[0].imm for ins in body if ins.mnemonic == "call"
                 and ins.operands[0].type == capstone.CS_OP_IMM]
        if len(calls) != 3:
            raise ValueError(f"original call helper layout changed: {len(calls)} direct calls")
        self.resolve_va = calls[-1]
        heads = set()
        for ins in rows(self.resolve_va, 96):
            if ins.mnemonic == "mov" and len(ins.operands) == 2:
                operand = ins.operands[1]
                if (operand.type == capstone.CS_OP_MEM and not operand.mem.base
                        and not operand.mem.index and operand.mem.disp != self.frame_global):
                    heads.add(operand.mem.disp)
        if len(heads) != 1:
            raise ValueError("original loaded-script list head is ambiguous")
        self.script_head = heads.pop()
        # Recover the record layout from the resolver's count/table loads.
        # Earlier engines have a four-byte smaller header; using the newer
        # constants installs an invisible function table in those fixtures.
        resolver_rows = rows(self.resolve_va, 400)
        layouts = set()
        for index, first in enumerate(resolver_rows):
            if (first.mnemonic != "mov" or first.operands[1].type != capstone.CS_OP_MEM):
                continue
            count = first.operands[1].mem
            if not count.base or count.index or not 0x80 <= count.disp < 0xa0:
                continue
            for second in resolver_rows[index + 1:index + 3]:
                if second.mnemonic != "mov" or second.operands[1].type != capstone.CS_OP_MEM:
                    continue
                table = second.operands[1].mem
                if count.base == table.base and not table.index and table.disp - count.disp == 0x14:
                    layouts.add((count.disp, table.disp))
        if len(layouts) != 1:
            raise ValueError("original script registry record layout is ambiguous")
        self.function_count_offset, self.function_table_offset = layouts.pop()
        self.code_size_offset = self.function_count_offset + 4
        self.code_base_offset = self.function_table_offset + 4
        self.next_script_offset = self.function_table_offset + 8
        argument_wrapper = rows(self.rows["argcheck"]["selected_va"], 16)
        argument_helper = next(ins.operands[0].imm for ins in argument_wrapper if ins.mnemonic == "call")
        argument_rows=rows(argument_helper,256)
        diagnostics=[]
        for index,ins in enumerate(argument_rows):
            if ins.mnemonic!="call"or ins.operands[0].type!=capstone.CS_OP_IMM:continue
            preceding=argument_rows[max(0,index-3):index]
            if any(row.mnemonic=="push"and row.operands[0].type==capstone.CS_OP_IMM
                   and row.operands[0].imm>=0x400000 for row in preceding):
                diagnostics.append(ins.operands[0].imm)
        if not diagnostics:raise ValueError("original argument diagnostic boundary is missing")
        self.error_va=diagnostics[0]
        self.diagnostic = None

        def diagnostic(uc, at, size, unused):
            esp = uc.reg_read(reg.UC_X86_REG_ESP)
            pointer = self.read_u32(esp + 4)
            text = self._input(pointer, -1).raw.split(b"\0", 1)[0].decode("cp932", "strict")
            sink=getattr(self,"diagnostic_sink",None)
            if sink and sink(text):
                self._return(0)
                return
            self.diagnostic = {"address": at, "message": text}
            uc.emu_stop()

        self.machine.hook_add(unicorn.UC_HOOK_CODE, diagnostic, begin=self.error_va, end=self.error_va)

    def registry(self, scripts):
        """Install real original linked records; scripts contain code_base/functions."""
        if not scripts or len(scripts) > 8:
            raise ValueError("original script registry fixture exceeds bound")
        records = [self.DATA + 0x30000 + i * 0x200 for i in range(len(scripts))]
        for i, script in enumerate(scripts):
            functions = script.get("functions", [])
            if len(functions) > 16:
                raise ValueError("original function table fixture exceeds bound")
            table = self.DATA + 0x32000 + i * 0x100
            data = b"".join(struct.pack("<II", hash_, offset) for hash_, offset in functions)
            self.machine.mem_write(table, data + bytes(8))
            record = bytearray(0xB0)
            for offset, value in ((self.function_count_offset, len(functions)),
                                  (self.code_size_offset, script.get("code_size", 0x1000)),
                                  (self.function_table_offset, table),
                                  (self.code_base_offset, script["code_base"]),
                                  (self.next_script_offset, records[i+1] if i+1 < len(records) else 0)):
                struct.pack_into("<I", record, offset, value)
            self.machine.mem_write(records[i], bytes(record))
        self.write_u32(self.script_head, records[0])
        return records

    def execute(self, *args, **kwargs):
        self.diagnostic = None
        try:
            result = super().execute(*args, **kwargs)
        except Exception as error:
            if self.diagnostic:
                raise OriginalDiagnostic(self.diagnostic["message"]) from error
            raise
        if self.diagnostic:
            raise OriginalDiagnostic(self.diagnostic["message"])
        return result
