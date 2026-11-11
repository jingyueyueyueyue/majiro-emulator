"""Enumerate x86 opcode selection without executing handlers or Windows APIs."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_mjo import opcode_table
from port_common import ROOT, timestamp, write_json
from compare_majiro_samples import reference_exe
from inspect_majiro_exe import engine_code_sections


def recover(path, definitions):
    import capstone
    import pefile
    import unicorn
    from unicorn import x86_const as reg

    data = Path(path).read_bytes()
    pe = pefile.PE(data=data)
    if pe.FILE_HEADER.Machine != 0x14C:
        raise ValueError("only x86 interpreter dispatch is supported")
    base = pe.OPTIONAL_HEADER.ImageBase
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    found = []
    selection_start = None
    selector_register = None
    for section in engine_code_sections(pe):
        code = section.get_data()
        for match in re.finditer(rb"\x0f\xbf.", code, re.DOTALL):
            va = base + section.VirtualAddress + match.start()
            rows = list(md.disasm(code[match.start():match.start() + 80], va))
            if not rows or rows[0].mnemonic != "movsx" or "word ptr" not in rows[0].op_str:
                continue
            if not any(i.mnemonic == "lea" and "- 0x100]" in i.op_str for i in rows[1:12]):
                continue
            bound = next((i for i in rows[1:15] if i.mnemonic == "ja"), None)
            source = rows[0].operands[1]
            if bound is None or source.type != capstone.CS_OP_MEM:
                continue
            if source.mem.index or not source.mem.base or source.mem.disp:
                continue
            found.append((rows[0].address, source.mem.base, bound.operands[0].imm))
    if not found:
        # Early V1 builds read the opcode in a standalone helper and return it
        # in EAX before the same three compare/table dispatch ranges. Recover
        # that call relationship, rather than assuming the newer inline load.
        early = []
        for section in engine_code_sections(pe):
            code = section.get_data()
            for match in re.finditer(rb"\x8d.", code, re.DOTALL):
                va = base + section.VirtualAddress + match.start()
                rows = list(md.disasm(code[match.start():match.start() + 40], va))
                if not rows or rows[0].mnemonic != "lea" or "- 0x100]" not in rows[0].op_str:
                    continue
                bound = next((i for i in rows[1:4] if i.mnemonic == "ja"), None)
                if bound is None:
                    continue
                # The call is within 32 bytes before this range reduction.
                for offset in range(max(0, match.start() - 32), match.start()):
                    if code[offset] != 0xE8:
                        continue
                    call = next(md.disasm(code[offset:offset + 5],
                                         base + section.VirtualAddress + offset), None)
                    if call is None or call.mnemonic != "call":
                        continue
                    target = call.operands[0].imm
                    helper = list(md.disasm(pe.get_data(target - base, 32), target))
                    load = next((i for i in helper[:5] if i.mnemonic == "movsx"
                                 and "word ptr" in i.op_str), None)
                    if load is None or not any(i.mnemonic == "ret" for i in helper[:8]):
                        continue
                    if load.operands[0].reg != rows[0].operands[1].mem.base:
                        continue
                    early.append((load.address, load.operands[1].mem.base,
                                  bound.operands[0].imm, call.address + call.size,
                                  load.operands[0].reg))
        if len(early) == 1:
            fetch, source_register, invalid, selection_start, selector_register = early[0]
            found = [(fetch, source_register, invalid)]
    if len(found) != 1:
        raise ValueError(f"expected one interpreter fetch pattern, found {len(found)}")
    fetch, source_register, invalid = found[0]
    machine = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
    machine.mem_map(base, (pe.OPTIONAL_HEADER.SizeOfImage + 4095) & ~4095)
    machine.mem_write(base, pe.get_memory_mapped_image())
    scratch = 0x10000000
    machine.mem_map(scratch, 0x4000)
    for name in ("EAX", "EBX", "ECX", "EDX", "ESI", "EDI", "EBP"):
        machine.reg_write(getattr(reg, "UC_X86_REG_" + name), scratch + 0x1000)
    machine.reg_write(getattr(reg, "UC_X86_REG_" + md.reg_name(source_register).upper()), scratch)
    machine.reg_write(reg.UC_X86_REG_ESP, scratch + 0x3F00)
    machine.reg_write(reg.UC_X86_REG_EFLAGS, 2)
    initial = machine.context_save()
    decoded = {}
    selected = {}
    error = None

    def choose(kind, target):
        if target != invalid and not base <= target < base + pe.OPTIONAL_HEADER.SizeOfImage:
            raise ValueError(f"opcode dispatch target outside original image: {target:08x}")
        selected.update(kind=kind, target=target, valid=target != invalid)
        machine.emu_stop()

    def hook(uc, address, size, _):
        if address == invalid:
            choose("invalid", invalid)
            return
        if address not in decoded:
            decoded[address] = next(md.disasm(bytes(uc.mem_read(address, 16)), address))
        ins = decoded[address]
        zero = bool(uc.reg_read(reg.UC_X86_REG_EFLAGS) & 64)
        if ins.mnemonic == "je" and zero:
            choose("equality", ins.operands[0].imm)
        elif (ins.mnemonic == "jne" and not zero
              and ins.operands[0].imm == invalid):
            choose("invalid", invalid)
        elif ins.mnemonic == "jne" and zero and ins.operands[0].imm == invalid:
            choose("fallthrough", address + size)
        elif ins.mnemonic == "jmp" and ins.operands[0].type == capstone.CS_OP_MEM:
            mem = ins.operands[0].mem
            at = mem.disp
            if mem.base:
                at += uc.reg_read(getattr(reg, "UC_X86_REG_" + md.reg_name(mem.base).upper()))
            if mem.index:
                at += uc.reg_read(
                    getattr(reg, "UC_X86_REG_" + md.reg_name(mem.index).upper())) * mem.scale
            choose("table", struct.unpack("<I", bytes(uc.mem_read(at, 4)))[0])
        elif ins.mnemonic == "call":
            # Selection should stop before any handler executes. A direct
            # fallthrough handler can reach this boundary without an equality.
            target = ins.operands[0].imm if ins.operands[0].type == capstone.CS_OP_IMM else address
            choose("call_boundary", target)
        elif ins.mnemonic in ("ret", "int", "int3"):
            raise ValueError("unexpected control flow before opcode selection")

    machine.hook_add(unicorn.UC_HOOK_CODE, hook)
    rows = []
    began = time.monotonic()
    # Exhaust all 16-bit values, including signed-negative values after movsx.
    for opcode in range(65536):
        machine.context_restore(initial)
        machine.mem_write(scratch, struct.pack("<H", opcode) + bytes(16))
        if selector_register is not None:
            value = opcode if opcode < 0x8000 else opcode - 0x10000
            machine.reg_write(getattr(reg, "UC_X86_REG_" + md.reg_name(selector_register).upper()), value & 0xFFFFFFFF)
        selected.clear()
        try:
            machine.emu_start(selection_start or fetch, 0, count=96)
        except Exception as caught:
            error = {"opcode": f"{opcode:04x}", "error": str(caught)}
            break
        if not selected:
            error = {"opcode": f"{opcode:04x}", "error": "dispatch instruction budget exhausted"}
            break
        if selected["valid"]:
            rows.append({
                "opcode": f"{opcode:04x}", "name": definitions.get(opcode, (None,))[0],
                "selection_kind": selected["kind"], "selected_va": selected["target"],
            })
    active = {int(row["opcode"], 16) for row in rows}
    known = set(definitions)
    return {
        "path": str(Path(path).resolve()), "exe_sha256": hashlib.sha256(data).hexdigest(),
        "fetch_va": fetch, "invalid_opcode_va": invalid,
        "selection_start_va": selection_start or fetch,
        "fetch_layout": "separate_signed_word_helper" if selection_start else "inline_signed_word",
        "tested_opcode_values": 65536 if error is None else opcode + 1,
        "recovery_status": "passed" if error is None else "incomplete",
        "handler_execution": False, "semantics_verified": False,
        "seconds": round(time.monotonic() - began, 3), "error": error,
        "active_opcodes": rows, "active_opcode_count": len(rows),
        "unknown_active_opcodes": [f"{value:04x}" for value in sorted(active - known)],
        "defined_but_absent": [f"{value:04x}" for value in sorted(known - active)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "reports/majiro_samples.json")
    args = parser.parse_args()
    inventory = json.loads(args.input.read_text(encoding="utf-8"))
    definitions = opcode_table()
    results = []
    for game in inventory["games"]:
        exe = reference_exe(game)
        try:
            result = recover(exe["path"], definitions)
        except Exception as caught:
            result = {"recovery_status": "failed", "error": str(caught)}
        result["sample"] = Path(game["directory"]).name
        results.append(result)
        print(f"{result['sample']}: {result['recovery_status']}, "
              f"active {result.get('active_opcode_count')}, "
              f"unknown {result.get('unknown_active_opcodes')}", flush=True)
    passed = all(row["recovery_status"] == "passed" and not row["unknown_active_opcodes"]
                 for row in results)
    write_json(ROOT / "reports/opcode_dispatch.json", {
        "schema": 1, "timestamp": timestamp(), "status": "passed" if passed else "failed",
        "scope": "all 16-bit dispatch values; handlers and Windows APIs never executed",
        "samples": results, "runtime_compatible": False,
    })
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
