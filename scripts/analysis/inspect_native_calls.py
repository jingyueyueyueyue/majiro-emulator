"""Reproducible disassembly of verified syscall dispatch sites in this source EXE."""
from __future__ import annotations
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, sha256, write_json

FUNCTIONS = {
    "$page_create": (0x83D81F59, 0x44B0D0, 0x7B),
    "$page_create_withalfa": (0xF94B3586, 0x44B320, 0x170),
    "$page_set_antidata": (0xF9705332, 0x44B5A0, 0xD0),
    "$grp_copy": (0x56BBBA3A, 0x448E70, 0x126),
    "$grp_extcopy": (0x30636D6E, 0x449050, 0x14B),
}
SOURCE_EXE_SHA256 = "2bec58f9fd7f924b5ca403d894ec609a265a57efdfca25397f95a5752b3fcd46"


def run():
    import capstone
    import pefile
    executable = next(p for p in (ROOT / "bin").glob("*.exe") if p.name != "uninst.exe")
    data = executable.read_bytes()
    pe = pefile.PE(data=data)
    if pe.FILE_HEADER.Machine != 0x14C:
        raise ValueError("expected x86 source EXE")
    baseline = __import__("json").loads((ROOT / "resources/source_manifest.json").read_text(encoding="utf-8"))
    expected = next(row for row in baseline["files"] if row["path"] == executable.relative_to(ROOT).as_posix())
    if sha256(executable) != expected["sha256"] or expected["sha256"] != SOURCE_EXE_SHA256:
        raise ValueError("source EXE changed; fixed addresses must be re-established")
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    rows = []
    for name, (h, address, size) in FUNCTIONS.items():
        sites = []
        index = 0
        while (index := data.find(struct.pack("<I", h), index)) >= 0:
            sites.append(index)
            index += 4
        offset = pe.get_offset_from_rva(address - pe.OPTIONAL_HEADER.ImageBase)
        rows.append({"name": name, "hash": "%08x" % h, "handler_va": hex(address),
                     "hash_file_offsets": sites,
                     "instructions": [{"va": hex(i.address), "mnemonic": i.mnemonic, "operands": i.op_str}
                                      for i in md.disasm(data[offset:offset + size], address)]})
    report = {"schema": 1, "exe_sha256": expected["sha256"], "functions": rows,
              "findings": [
                  "page_create defaults the optional third argument to 1",
                  "page_create flag 0 enters 8-bit indexed allocation; nonzero enters 32-bit allocation",
                  "grp_copy arguments are source,x,y,width,height,destination,dx,dy; dx/dy are optional",
                  "page_set_antidata stores an associated page after checking indexed storage and matching dimensions"
              ], "status": "static_disassembly", "dynamic_original_comparison": "not_run"}
    write_json(ROOT / "reports/native_call_research.json", report)
    return report


if __name__ == "__main__":
    print(run()["status"])
