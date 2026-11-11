"""Record original EXE references and bounds for native save-format research."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

import capstone
import pefile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def references(pe, raw, address):
    """Accept only a complete x86 instruction containing this exact immediate."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    found = {}
    for section in pe.sections:
        if not section.Characteristics & 0x20000000:
            continue
        data, base = section.get_data(), pe.OPTIONAL_HEADER.ImageBase + section.VirtualAddress
        needle = struct.pack("<I", address)
        start = 0
        while (at := data.find(needle, start)) >= 0:
            start = at + 1
            for begin in range(max(0, at - 10), at + 1):
                ins = next(md.disasm(data[begin:begin+16], base+begin), None)
                if ins is None or not ins.imm_size:
                    continue
                if (begin + ins.imm_offset == at and ins.imm_size == 4
                        and any(op.type == capstone.CS_OP_IMM and op.imm == address for op in ins.operands)):
                    found[ins.address] = ins
    return list(found.values())


def recover():
    inventory = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf8"))
    evidence = archive_dir("native_save_layouts")
    rows = []
    for sample in inventory["samples"]:
        raw = Path(sample["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sample["exe_sha256"]
        pe = pefile.PE(data=raw)
        base = pe.OPTIONAL_HEADER.ImageBase
        signatures = []
        text = []
        for signature in (b"MajiroSysV1.100", b"MajiroSavV1.103", b"MajiroSavV1.105",
                          b"savedata\\majiro_readmark.mss"):
            offset = raw.find(signature + b"\0")
            if offset < 0:
                continue
            address = base + pe.get_rva_from_offset(offset)
            refs = references(pe, raw, address)
            signatures.append({"signature": signature.decode("ascii"), "string_va": f"{address:08x}",
                               "reference_vas": [f"{ins.address:08x}" for ins in refs]})
            for ins in refs:
                start = max(base, ins.address - 160)
                offset_ = pe.get_offset_from_rva(start - base)
                block = raw[offset_:offset_+2200]
                md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
                text.append(signature.decode("ascii") + f" reference {ins.address:08x}")
                text.extend(f"{x.address:08x} {x.mnemonic:8s} {x.op_str}" for x in md.disasm(block, start))
        path = evidence / f"20261110_{sample['sample'].replace(' ', '_')}_native_save_layouts.txt"
        path.write_text("\n".join(text) + "\n", encoding="utf8")
        rows.append({"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
                     "signatures": signatures, "evidence": str(path.relative_to(ROOT)),
                     "runtime_reader_differential": sample["sample"] == "narci_onetwo"})
    report = {"schema": 1, "timestamp": timestamp(), "samples": rows,
              "scope": "bounded static x86 signature references; only onetwo has dynamic reader comparison",
              "story_slot_restore_verified": False}
    write_json(ROOT / "reports/native_save_layouts.json", report)
    write_json(evidence / "20261110_native_save_layouts.json", report)
    print(f"native save layout references: {len(rows)} original EXEs")
    return report


if __name__ == "__main__":
    recover()
