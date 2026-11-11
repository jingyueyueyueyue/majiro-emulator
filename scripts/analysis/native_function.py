"""Bounded native CFG reads for original engine interface recovery."""
from __future__ import annotations

from collections import deque

import capstone


class NativeFunctions:
    def __init__(self, oracle):
        self.oracle = oracle
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True
        self.cache = {}

    def instruction(self, address):
        if address not in self.cache:
            self.cache[address] = next(self.md.disasm(
                bytes(self.oracle.machine.mem_read(address,16)),address))
        return self.cache[address]

    def body(self, address, maximum_bytes=8192, maximum_instructions=1800, minimum_address=None):
        lower=address if minimum_address is None else minimum_address
        pending=deque([address]);result={}
        while pending:
            at=pending.popleft()
            while at not in result and lower<=at<lower+maximum_bytes:
                row=self.instruction(at);result[at]=row
                if len(result)>maximum_instructions:
                    raise ValueError("native function recovery exceeds instruction bound")
                if row.mnemonic in("ret","int3","int","hlt"):break
                following=at+row.size
                if row.mnemonic=="jmp":
                    if row.operands[0].type==capstone.CS_OP_IMM:
                        at=row.operands[0].imm
                        continue
                    break
                if row.mnemonic.startswith("j")and row.operands[0].type==capstone.CS_OP_IMM:
                    pending.append(row.operands[0].imm)
                at=following
        return[result[key]for key in sorted(result)]

    def calls(self, address):
        return[row for row in self.body(address)if row.mnemonic=="call"
               and row.operands[0].type==capstone.CS_OP_IMM]

    def syscall_helper(self, entry):
        address=entry["selected_va"]
        for _ in range(12):
            row=self.instruction(address)
            if row.mnemonic=="call"and row.operands[0].type==capstone.CS_OP_IMM:
                return row.operands[0].imm
            if row.mnemonic in("ret","jmp"):break
            address+=row.size
        raise ValueError("original syscall helper layout is unrecognized")
