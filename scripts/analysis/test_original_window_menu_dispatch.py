"""Temporary original-only command dispatch probe; never runs source menus."""
from pathlib import Path
import json
import re
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts" / p) for p in ("analysis", "convert")]
import capstone
import pefile
import unicorn
from unicorn import x86_const as reg
from native_function import NativeFunctions
from original_opcode import OpcodeOracle
from port_common import archive_dir, read_json, write_json, timestamp

def main():
    folder = archive_dir("original_window_menu_matrix")
    rows = []
    for sample in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]:
        row = {"sample": sample["sample"], "exe_sha256": sample["exe_sha256"], "cases": []}
        try:
            pe = pefile.PE(sample["path"])
            data = pe.get_memory_mapped_image()
            base = pe.OPTIONAL_HEADER.ImageBase
            matches = list(re.finditer(re.escape(struct.pack("<I", zlib.crc32(b"LOADMENU"))), data))
            assert len(matches) == 1, "LOADMENU hash is not unique"
            off = matches[0].start()
            window = max(0, off - 2048)
            pads = list(re.finditer(rb"\xc3[\x90\xcc]{2,}", data[window:off]))
            assert pads, "command dispatcher boundary is not established"
            start = base + window + pads[-1].end()
            oracle = OpcodeOracle(sample)
            uc = oracle.machine
            functions = NativeFunctions(oracle)
            md = functions.md
            prefix = list(md.disasm(data[start - base:off - 1], start))
            reads = []
            for ins in prefix:
                if ins.mnemonic not in ("mov", "cmp"):
                    continue
                for operand in ins.operands:
                    if (operand.type == capstone.CS_OP_MEM and not operand.mem.base
                            and not operand.mem.index and operand.mem.disp not in reads):
                        reads.append(operand.mem.disp)
            assert len(reads) == 2, f"command lock/pending fields are ambiguous: {reads}"
            guard, pending = reads
            dispatch = list(md.disasm(data[off - 1:off + 5], base + off - 1))[0]
            assert dispatch.mnemonic in ("push", "mov")
            fastcall = dispatch.mnemonic == "mov"
            targets = functions.body(base + off - 1, minimum_address=start)
            calls = [ins.operands[0].imm for ins in targets
                     if ins.mnemonic == "call" and ins.operands[0].type == capstone.CS_OP_IMM]
            assert len(calls) == 1, f"event emission boundary is ambiguous: {calls}"
            emit = calls[0]
            returns = [ins for ins in functions.body(emit) if ins.mnemonic == "ret"]
            assert returns and all(not ins.operands for ins in returns), "event core stack-return contract changed"
            row.update(dispatcher=start, menu_guard=guard, pending_command=pending,
                       event_dispatch=emit, abi="fastcall" if fastcall else "cdecl")
            captured = []

            def emission(uc, at, size, unused):
                esp = uc.reg_read(reg.UC_X86_REG_ESP)
                values = ([uc.reg_read(reg.UC_X86_REG_ECX), uc.reg_read(reg.UC_X86_REG_EDX),
                           oracle.read_u32(esp + 4)] if fastcall else
                          [oracle.read_u32(esp + i * 4) for i in (1, 2, 3)])
                captured.append(values)
                uc.reg_write(reg.UC_X86_REG_EAX, 0)
                uc.reg_write(reg.UC_X86_REG_EIP, oracle.read_u32(esp))
                # This optimized build passes ECX/EDX plus a caller-cleaned stack
                # argument. Its actual event core ends in plain ret, not ret 4.
                uc.reg_write(reg.UC_X86_REG_ESP, esp + 4)

            hook = uc.hook_add(unicorn.UC_HOOK_CODE, emission, begin=emit, end=emit)
            stop = oracle.DATA + 0xff000
            try:
                for command, event in ((0, None), (1, "LOADMENU"), (3, "SAVEMENU"), (99, "CONFIGMENU")):
                    for locked in (False, True):
                        captured.clear()
                        oracle.write_u32(guard, int(locked))
                        oracle.write_u32(pending, command)
                        esp = oracle.STACK + 0x8000
                        uc.mem_write(esp, struct.pack("<I", stop))
                        uc.reg_write(reg.UC_X86_REG_ESP, esp)
                        uc.emu_start(start, stop, count=20000, timeout=1000000)
                        assert uc.reg_read(reg.UC_X86_REG_EIP) == stop, "dispatcher did not return"
                        expected = [] if locked or event is None else [[zlib.crc32(event.encode()), 0, 0]]
                        assert captured == expected, (command, locked, captured, expected)
                        after = oracle.read_u32(pending)
                        assert after == (command if locked else 0), ("pending not retained/consumed", after)
                        row["cases"].append({"command": command, "event": event, "locked": locked,
                                             "event_arguments": list(captured), "pending_after": after,
                                             "status": "passed"})
            finally:
                uc.hook_del(hook)
            row["status"] = "passed"
            text = "\n".join(f"{r.address:08x} {r.mnemonic:9} {r.op_str}" for r in prefix + targets)
            (folder / (sample["sample"].replace(" ", "_") + "_dispatch.txt")).write_text(text, encoding="utf8")
        except Exception as error:
            row.update(status="incomplete", error=str(error))
        rows.append(row)
        print(json.dumps({"sample": row["sample"], "status": row["status"],
                          "cases": len(row["cases"]), "error": row.get("error")}), flush=True)

    report = {"timestamp": timestamp(), "cases": rows, "device_accessed": False,
              "scope": "actual original pending window-menu command dispatcher with only event-call boundary captured; script/Android menu execution remains separate",
              "status": "passed" if all(row["status"] == "passed" for row in rows) else "incomplete",
              "native_cases": sum(len(row["cases"]) for row in rows),
              "evidence": str(folder.relative_to(ROOT))}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/original_window_menu_matrix.json", report)
    print(folder)
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
