"""Resolve original hashed syscall dispatch to handlers and print selected code."""
from __future__ import annotations
import argparse
from pathlib import Path
import struct
import sys

from original_x86 import Original
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, read_json, write_json


class Research:
    def __init__(self):
        import capstone
        import pefile
        self.original = Original()
        self.data = self.original.path.read_bytes()
        self.pe = pefile.PE(data=self.data)
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)

    def instructions(self, address, size=300):
        offset = self.pe.get_offset_from_rva(address - Original.BASE)
        return list(self.md.disasm(self.data[offset:offset + size], address))

    def handler(self, hash_value):
        import unicorn
        found = []
        pattern = b"\x81\xfa" + struct.pack("<I", hash_value)
        offset = self.data.find(pattern)
        if offset < 0:
            raise ValueError("dispatch comparison not found: %08x" % hash_value)
        address = Original.BASE + self.pe.get_rva_from_offset(offset)
        machine = self.original.machine
        entered = []

        def hook(uc, at, size, _):
            ins = next(self.md.disasm(bytes(uc.mem_read(at, size)), at))
            if ins.mnemonic=='je' and not entered:
                entered.append(int(ins.op_str,16))
            if ins.mnemonic == "call":
                if not ins.op_str.startswith("0x"):
                    raise RuntimeError("unexpected indirect syscall dispatcher")
                target=int(ins.op_str,16)
                found.append(entered[0] if target==0x44ec90 and entered else target)
                uc.emu_stop()

        handle = machine.hook_add(unicorn.UC_HOOK_CODE, hook)
        try:
            machine.reg_write(self.original.reg.UC_X86_REG_EDX, hash_value)
            machine.reg_write(self.original.reg.UC_X86_REG_ESP, Original.STACK + 0x80000)
            machine.reg_write(self.original.reg.UC_X86_REG_EBP, Original.STACK + 0x90000)
            machine.emu_start(address, 0, count=100, timeout=100000)
        finally:
            machine.hook_del(handle)
        if len(found) != 1:
            raise ValueError("cannot resolve syscall handler: %08x" % hash_value)
        return found[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--address", type=lambda s: int(s, 0))
    parser.add_argument("--bytes", type=int, default=300)
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    research = Research()
    names = {int(k, 16): v for k, v in read_json(ROOT / "references/majiro-py/known_hashes.json")["syscalls"].items()}
    if args.inventory:
        rows = []
        inventory = read_json(ROOT / "reports/syscall_inventory.json")
        for h in sorted({int(row["hash"], 16) for row in inventory["calls"]}):
            try:
                address = research.handler(h)
                rows.append({"hash": "%08x" % h, "name": names.get(h), "handler": address})
            except Exception as error:
                rows.append({"hash": "%08x" % h, "name": names.get(h), "error": str(error)})
        write_json(ROOT / "reports/syscall_handlers.json", {"exe_sha256": Original.IDENTITY, "handlers": rows})
        print("handlers resolved", sum("handler" in row for row in rows), "/", len(rows))
    addresses = []
    for name in args.names:
        h = next((h for h, n in names.items() if n == name), None)
        if h is None:
            h = int(name, 16)
        addresses.append((name, research.handler(h)))
    if args.address:
        addresses.append(("address", args.address))
    for name, address in addresses:
        print(name, hex(address))
        for ins in research.instructions(address, args.bytes):
            print("%08x %-9s %s" % (ins.address, ins.mnemonic, ins.op_str))


if __name__ == "__main__":
    main()
