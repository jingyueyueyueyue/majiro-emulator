"""Recover original execution-control and unnamed syscall boundaries."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

import capstone
import unicorn
from unicorn import x86_const as reg
from native_function import NativeFunctions
from original_calls import CallOracle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir

NAMES = ("$reset_exec", "$call_sub", "$exec_func", "$thread_atend")
HASHES = ("0542e1b7", "3670b283", "42d7c922", "c32e286f",
          "2bd1709d", "7e0b8320", "c46925cf")


class ExecutionOracle(CallOracle):
    def _import(self,uc,at,size,unused):
        if self.import_names.get(at)in("EnterCriticalSection","LeaveCriticalSection"):
            self._return(1,0);return
        return super()._import(uc,at,size,unused)

    def __init__(self, sample, entries):
        super().__init__(sample)
        self.functions = functions = NativeFunctions(self)
        self.entries = {entry["name"]: entry for entry in entries if entry["name"]}
        self.call_sub_helper = functions.syscall_helper(self.entries["$call_sub"])
        body = functions.body(self.call_sub_helper)
        table_reads = [index for index, row in enumerate(body)
                       if row.mnemonic == "mov" and len(row.operands) == 2
                       and row.operands[1].type == capstone.CS_OP_MEM
                       and row.operands[1].mem.disp == 0xA4]
        if len(table_reads) != 1:
            raise ValueError("original call_sub target-table accessor changed")
        calls = [row.operands[0].imm for row in body[:table_reads[0]]
                 if row.mnemonic == "call" and row.operands[0].type == capstone.CS_OP_IMM]
        if len(calls) < 2:
            raise ValueError("original call_sub file loader boundary missing")
        self.file_lookup = calls[-1]
        call_rows=[row for row in body[:table_reads[0]]if row.mnemonic=="call"
                   and row.operands[0].type==capstone.CS_OP_IMM]
        self.file_load = calls[-2]if call_rows[-1].address-call_rows[-2].address<32 else None
        if self.file_load is not None and any(row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM
            and row.operands[0].mem.disp==self.frame_global for row in functions.body(self.file_load)):
            self.file_load=None
        self.file_fastcall=any(row.mnemonic=="lea"and row.op_str.startswith("ecx,")for row in
                              body[max(0,table_reads[0]-12):table_reads[0]])
        self.loaded_files = []

        def loader(uc, at, size, unused):
            stack = uc.reg_read(reg.UC_X86_REG_ESP)
            pointer = uc.reg_read(reg.UC_X86_REG_ECX)if self.file_fastcall else self.read_u32(stack + 4)
            self._input(pointer, -1)
            self._return(0, 0)

        def lookup(uc,at,size,unused):
            stack=uc.reg_read(reg.UC_X86_REG_ESP)
            pointer=uc.reg_read(reg.UC_X86_REG_ECX)if self.file_fastcall else self.read_u32(stack+4)
            self.loaded_files.append(self._input(pointer,-1).value.decode("cp932"))
            self._return(0,self.records[1])
        if self.file_load is not None:self.machine.hook_add(unicorn.UC_HOOK_CODE, loader,
                              begin=self.file_load, end=self.file_load)
        self.machine.hook_add(unicorn.UC_HOOK_CODE,lookup,
            begin=self.file_lookup,end=self.file_lookup)
        # The standard /GS cookie has no game-visible state in this fixture.
        for row in body:
            if row.mnemonic != "call" or row.operands[0].type != capstone.CS_OP_IMM:
                continue
            target = row.operands[0].imm
            code = functions.body(target, maximum_bytes=128)
            if (any(item.mnemonic == "rol" and item.op_str.endswith(", 0x10") for item in code)
                or(code and code[0].mnemonic=="cmp"and code[0].op_str.startswith("ecx, dword ptr [")
                   and any("ret"in item.mnemonic for item in code))):
                self.machine.hook_add(unicorn.UC_HOOK_CODE,
                                      lambda uc, at, size, unused: self._return(0),
                                      begin=target, end=target)

    def call_sub(self, file, function_hash, parameters=(), discard=False, duplicate=False, fallback=False,
                 missing=False, returning=False):
        self.loaded_files = []
        caller_code = self.operand - 2
        callee_code = self.DATA + 0x34000
        library_code = self.DATA + 0x36000
        raw_args = [(2, self.string(file, 9)), (0, function_hash & 0xffffffff)]
        # An independent fixture owner retains the filename so the original
        # temporary argument release does not hand a synthetic block to CRT.
        self.write_u32(raw_args[0][1], 2)
        raw_args += list(parameters)
        def setup(source):
            records = source.registry([
                {"code_base": caller_code, "functions": []if missing else[(function_hash, 100)]},
                {"code_base": callee_code, "functions": [] if fallback else
                    ([]if missing else[(function_hash, 100), (function_hash, 200)] if duplicate else
                     []if missing else[(function_hash, 100)])},
                {"code_base": library_code, "functions": []if missing else[(function_hash, 300)]}])
            for record, name in zip(records, ("caller.mjo", "callee.mjo", "library.mjo")):
                source.machine.mem_write(record, name.encode("ascii") + b"\0")
                source.write_u32(record + 0x94, 0)
            source.records = records
            if returning:
                types=[kind for kind,_ in parameters]
                body=struct.pack("<HH",int(source.rows["argcheck"]["opcode"],16),len(types))+bytes(types)
                if types:
                    body+=struct.pack("<HHIh",int(source.rows["ld"]["opcode"],16),96+types[0]*256,99,-2)
                else:body+=struct.pack("<Hi",int(source.rows["ldc.i"]["opcode"],16),77)
                body+=struct.pack("<H",int(source.rows["ret"]["opcode"],16))
                for base,offset in((caller_code,100),(callee_code,100),(callee_code,200),(library_code,300)):
                    source.machine.mem_write(base+offset,body)
        result = self.execute("syscallp" if discard else "syscall",
            [(0, 71)] + list(reversed(raw_args)),
            struct.pack("<IH", int(self.entries["$call_sub"]["hash"], 16), len(raw_args)),
            setup=setup, budget=300000,instructions=4 if returning else 1)
        current = self.read_u32(self.frame_global)
        base=self.read_u32(current+4)
        args=[(self.read_u32(self.values+(base-index-2)*16+4),
               self.read_u32(self.values+(base-index-2)*16+12))for index in range(len(parameters))]if not returning else[]
        return {"stack": result, "pc": self.read_u32(current),"parameters":args,
                "frame": current, "frame_parameter_base": self.read_u32(current+4),
                "depth": self.read_u32(self.context),
                "loaded_files": list(self.loaded_files),
                "callee_code": callee_code, "caller_code": caller_code,
                "library_code": library_code}

    def lifecycle(self,name,function_hash=0x12345678,args=(),discard=False):
        caller_code=self.operand-2;callee_code=self.DATA+0x34000
        entry=self.entries[name];body=self.functions.body(self.functions.syscall_helper(entry))
        observed=[];phases=[];reset_buffer=None;mode_slot=None
        if name=="$reset_exec":
            for row in body:
                if row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM:
                    if not row.operands[0].mem.base and row.operands[1].type==capstone.CS_OP_IMM\
                       and row.operands[1].imm==4:mode_slot=row.operands[0].mem.disp
                    elif row.operands[0].mem.base and row.operands[0].mem.disp in(0x4c34,0x5034):
                        phases.append(row.operands[0].mem.disp)
            for row in body:
                if row.mnemonic!="call"or row.operands[0].type!=capstone.CS_OP_IMM:continue
                target=row.operands[0].imm;code=self.functions.body(target,maximum_bytes=300)
                if(len(code)==2 and code[0].mnemonic=="mov"and
                   code[0].operands[1].type==capstone.CS_OP_MEM and not code[0].operands[1].mem.base):
                    head_slot=code[0].operands[1].mem.disp
                    self.write_u32(head_slot,self.context)
                if any(item.mnemonic=="mov"and item.operands[0].type==capstone.CS_OP_MEM
                       and item.operands[0].size==1 for item in code):
                    immediates=[item.operands[1].imm for item in code if item.mnemonic=="mov"
                                and item.operands[1].type==capstone.CS_OP_IMM
                                and item.operands[1].imm>=0x400000]
                    if len(immediates)==1:reset_buffer=immediates[0]
        def setup(source):
            source.registry([{"code_base":callee_code,"functions":[(function_hash,100)]},
                             {"code_base":caller_code,"functions":[(function_hash,200)]}])
            if name=="$exec_func":
                # Mark several old frame fields; real context cleanup must
                # erase these rather than preserving a caller continuation.
                source.write_u32(source.frame+0x20,0x1234)
                source.write_u32(source.context+0x20,0)
            if name=="$reset_exec"and phases:
                phase=phases[0];child=source.DATA+0x8000
                source.machine.mem_write(child,bytes(0x5100))
                source.write_u32(source.context+0x1c,child)
                source.write_u32(source.context+phase,0);source.write_u32(child+phase,3)
                source.reset_child=child
        values=[(0,v&0xffffffff)for v in args]
        values=[(0,function_hash)]+values if name!="$reset_exec"else[(2,self.string("callee.mjo",9))]
        if name=="$exec_func":
            wrapper=self.functions.instruction(entry["selected_va"])
            at=entry["selected_va"]
            for _ in range(6):
                row=self.functions.instruction(at);at+=row.size
                if row.mnemonic=="call":break
            def returned(uc,addr,size,unused):
                observed.append(addr);uc.emu_stop()
            hook=self.machine.hook_add(unicorn.UC_HOOK_CODE,returned,begin=at,end=at)
        else:hook=None
        try:
            try:
                result=self.execute("syscallp"if discard else"syscall",list(reversed(values)),
                    struct.pack("<IH",int(entry["hash"],16),len(values)),setup=setup,budget=300000)
            except RuntimeError:
                if not observed:raise
                result=[]
        finally:
            if hook:self.machine.hook_del(hook)
        current=self.read_u32(self.frame_global)
        slots=[]
        # Registration is inline, so locate its context-relative function slot.
        if name=="$thread_atend":
            at=entry["selected_va"]
            for _ in range(24):
                row=self.functions.instruction(at);at+=row.size
                if row.mnemonic=="mov"and row.operands[0].type==capstone.CS_OP_MEM:
                    mem=row.operands[0].mem
                    if mem.base and mem.disp in(0x4c30,0x5030):slots.append(mem.disp)
                if row.mnemonic=="jmp":break
        return{"result":result,"pc":self.read_u32(current),"depth":self.read_u32(self.context),
               "target":callee_code+100,"at_end":self.read_u32(self.context+slots[0])if slots else None,
               "old_cleanup_field":self.read_u32(self.frame+0x20),
               "reset_script":self._input(reset_buffer,-1).value.decode("cp932")if reset_buffer else None,
               "reset_phase":self.read_u32(self.context+phases[0])if phases else None,
               "child_phase":self.read_u32(self.reset_child+phases[0])if phases else None,
               "reset_mode":self.read_u32(mode_slot)if mode_slot else None}


def inspect():
    samples = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch = {r["sample"]: r["entries"] for r in
                json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    folder = archive_dir("execution_recovery")
    for sample in samples:
        oracle = CallOracle(sample)
        functions = NativeFunctions(oracle)
        lines = []
        for entry in dispatch[sample["sample"]]:
            if entry["name"] not in NAMES and entry["hash"] not in HASHES:
                continue
            lines.append(f"\n{entry['name'] or entry['hash']} {entry['hash']} @{entry['selected_va']:08x}")
            address = entry["selected_va"]
            for _ in range(30):
                row = functions.instruction(address)
                lines.append(f"{row.address:08x} {row.mnemonic} {row.op_str}")
                if row.mnemonic in ("jmp", "ret"):
                    break
                address += row.size
            try:
                target = functions.syscall_helper(entry)
                lines.append(f"HELPER {target:08x}")
                for row in functions.body(target, maximum_bytes=2048):
                    lines.append(f"{row.address:08x} {row.mnemonic} {row.op_str}")
            except (ValueError, StopIteration) as error:
                lines.append(f"RECOVERY {error}")
        path = folder / (folder.name[:8] + "_" + sample["sample"].replace(" ", "_") + ".txt")
        path.write_text("\n".join(lines), encoding="utf8")
        print(sample["sample"], str(path.relative_to(ROOT)), flush=True)


if __name__ == "__main__":
    inspect()
