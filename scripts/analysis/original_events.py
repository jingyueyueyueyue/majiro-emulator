"""Recover and inspect the original event list without replacing its algorithms."""
from __future__ import annotations

import struct

import capstone

from original_calls import CallOracle


class EventOracle(CallOracle):
    def __init__(self, sample, entries):
        super().__init__(sample)
        self.entries = {entry["name"]: entry for entry in entries if entry["name"]}
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        md.detail = True
        address = self.entries["$event_release"]["selected_va"]
        wrapper = list(md.disasm(bytes(self.machine.mem_read(address, 32)), address))
        address = next(row.operands[0].imm for row in wrapper if row.mnemonic == "call")
        rows = []
        for row in md.disasm(bytes(self.machine.mem_read(address, 300)), address):
            rows.append(row)
            if row.mnemonic == "ret":
                break
        calls = [row.operands[0].imm for row in rows if row.mnemonic == "call"
                 and row.operands[0].type == capstone.CS_OP_IMM]
        # Some original builds wire release to the hook-registration core.
        # Recover that alias from the actual call targets, not a sample name.
        hook_entry = self.entries["$event_hook"]["selected_va"]
        hook_wrapper = list(md.disasm(bytes(self.machine.mem_read(hook_entry, 32)), hook_entry))
        hook_helper = next(row.operands[0].imm for row in hook_wrapper if row.mnemonic == "call")
        hook_calls = []
        for row in md.disasm(bytes(self.machine.mem_read(hook_helper, 300)), hook_helper):
            if row.mnemonic == "call" and row.operands[0].type == capstone.CS_OP_IMM:
                hook_calls.append(row.operands[0].imm)
            if row.mnemonic == "ret":
                break
        self.release_registers_hook = len(calls) == 4 and calls == hook_calls
        # cdecl builds call stack_top, CRC, release and push_int. The
        # optimized fastcall build inlines stack_top and calls three helpers.
        if self.resolve_va in calls:
            core = address  # Older builds inline the list-removal loop.
        elif len(calls) in (3, 4):
            core = calls[-2]
        else:
            raise ValueError("original event-release wrapper changed")
        rows = list(md.disasm(bytes(self.machine.mem_read(core, 400)), core))
        reads = []
        writes = set()
        for row in rows:
            if row.mnemonic == "mov" and len(row.operands) == 2:
                destination=row.operands[0]
                if(destination.type==capstone.CS_OP_MEM and not destination.mem.base
                        and not destination.mem.index):
                    writes.add(destination.mem.disp)
                source = row.operands[1]
                if (source.type == capstone.CS_OP_MEM and not source.mem.base and not source.mem.index
                        and source.mem.disp not in (self.context_global, self.frame_global)):
                    if source.mem.disp not in reads:
                        reads.append(source.mem.disp)
            if row.mnemonic == "ret":
                break
        if self.release_registers_hook:
            pair = sorted(reads)
            if len(pair) != 2 or pair[1] != pair[0] + 4 or not set(pair).issubset(writes):
                raise ValueError(f"original aliased event registration globals are ambiguous: {reads}")
            self.event_head, self.event_tail = pair
            self.release_lock = None
        elif len(reads)!=2 or not{reads[0],reads[0]+4}.issubset(writes)or reads[1]in(reads[0],reads[0]+4):
            raise ValueError(f"original event globals are ambiguous: {reads}")
        else:
            self.event_head, self.release_lock = reads
            self.event_tail = self.event_head+4
        self.release_va = core

    def reset_events(self):
        for address in (self.event_head,self.event_tail,self.release_lock):
            if address is not None:
                self.write_u32(address,0)
        self.heap = self.fixture_heap_start
        self.guest_allocations = dict(self.permanent_allocations)

    def event_rows(self):
        result, visited = [], set()
        node = self.read_u32(self.event_head)
        previous = 0
        while node:
            if node in visited or len(visited) > 128:
                raise ValueError("original event list cycle or fixture bound exceeded")
            visited.add(node)
            owner, protected, hash_, target, before, after = struct.unpack(
                "<6I", self.machine.mem_read(node, 24))
            if before != previous:
                raise ValueError("original event list backlink mismatch")
            result.append({"owner": owner, "protected": protected, "event_hash": hash_,
                           "offset": target-(self.operand-2), "node": node})
            previous, node = node, after
        if self.read_u32(self.event_tail) != previous:
            raise ValueError("original event list tail mismatch")
        return result

    def call_event_api(self, name, event, function, owner=1, locked=False, discard=False):
        string = self.string(event, 0)

        def setup(source):
            source.registry([{"code_base": source.operand-2, "functions": [
                (0x12345678, 100), (0x12345679, 120), (0x1234567A, 100)]}])
            if source.release_lock is not None:
                source.write_u32(source.release_lock, int(locked))

        entry = self.entries[name]
        return self.execute("syscallp"if discard else"syscall", [(0, function), (2, string)],
            struct.pack("<IH", int(entry["hash"], 16), 2), setup=setup,
            context_fields={4: owner}, preserve_heap=True, budget=200000)
