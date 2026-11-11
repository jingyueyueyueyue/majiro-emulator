"""Bounded original graphics syscall recovery and page fixtures."""
from __future__ import annotations

import argparse
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

NAMES = ("$make_mosaic", "$grp_mulcopy", "$fuchidori_copy", "$grp_overlayboxfill")


class GraphicsOracle(CallOracle):
    def __init__(self, sample, entries):
        super().__init__(sample)
        self.diagnostic_sink=lambda text:True
        self.entries = {row["name"]: row for row in entries if row["name"]}
        self.functions = NativeFunctions(self)
        self.cursor = self.DATA + 0x30000
        f=self.functions
        root=f.syscall_helper(self.entries.get("$grp_mulcopy",self.entries["$grp_copy"]))
        self.optional_mulcopy_coordinates="$grp_mulcopy" in self.entries and any(row.mnemonic=="cmp"and
            len(row.operands)==2 and row.operands[1].type==capstone.CS_OP_IMM
            and row.operands[1].imm==7 for row in f.body(root))
        candidates=[]
        for target in [root]+[call.operands[0].imm for call in f.calls(root)]:
            body=f.body(target)
            if (target!=root and len(body)>30)or not any(row.mnemonic=="mov"and len(row.operands)==2
                and row.operands[1].type==capstone.CS_OP_MEM
                and row.operands[1].mem.disp in(0x30,0x34,0x38) for row in body):continue
            globals_=[row.operands[1].mem.disp for row in body if row.mnemonic=="mov"
                and len(row.operands)==2 and row.operands[1].type==capstone.CS_OP_MEM
                and not row.operands[1].mem.base and not row.operands[1].mem.index]
            globals_=[value for value in globals_ if value not in(self.frame_global,self.context_global)]
            if len(set(globals_))==1:
                link=next(row.operands[1].mem.disp for row in body if row.mnemonic=="mov"
                    and len(row.operands)==2 and row.operands[1].type==capstone.CS_OP_MEM
                    and row.operands[1].mem.base and row.operands[1].mem.disp in(0x30,0x34,0x38))
                candidates.append((globals_[0],link))
        if len(set(candidates))!=1:raise ValueError("original page validation list changed")
        self.page_head,self.page_link=candidates[0];self.write_u32(self.page_head,0)
        self.pixel_offset=self.page_link-12
        self.registered_pages=[]
        root=f.syscall_helper(self.entries["$make_mosaic"])
        roots=f.body(root);rand_calls={}
        for call in roots:
            if call.mnemonic=="call"and call.operands[0].type==capstone.CS_OP_IMM:
                target=call.operands[0].imm
                rand_calls[target]=rand_calls.get(target,0)+1
        candidates=[]
        for target,count in rand_calls.items():
            if count<4:continue
            body=f.body(target)
            if any(row.mnemonic=="imul"and "0x343fd"in row.op_str for row in body):
                tls=next(row.operands[0].imm for row in body if row.mnemonic=="call")
                candidates.append(tls)
        if len(candidates)!=1:raise ValueError("original mosaic random stream changed")
        modern=getattr(self,"modern_crt_evidence",None)
        if modern and modern["thread_get"]==candidates[0]:
            # StringOracle already owns this CRT TLS getter. Installing a
            # second returning hook pops two guest frames and skips rand.
            self.random_state=self.DATA+0x6a000
        else:
            self.random_state=self.DATA+0x6e000
            def thread_data(uc,at,size,unused):self._return(0,self.random_state)
            self.machine.hook_add(unicorn.UC_HOOK_CODE,thread_data,begin=candidates[0],end=candidates[0])
        self.seed(1)

    def memory(self, data):
        address = (self.cursor + 15) & ~15
        self.cursor = address + len(data)
        if self.cursor > self.DATA + 0x60000:
            raise ValueError("original graphics fixture exceeds memory bound")
        self.machine.mem_write(address, bytes(data))
        return address

    def page(self, width, height, data, depth=32, mask=None):
        self.cursor = max(self.cursor, self.DATA + 0x30000)
        if depth==32 and self.pixel_offset!=0x2c:
            data=b"".join(data[at:at+3]for at in range(0,len(data),4))
            depth=24
        stride = width * (depth // 8)
        if len(data) != stride * height:
            raise ValueError("original fixture pixel size mismatch")
        pixels = self.memory(data)
        page = bytearray(0x3c)
        struct.pack_into("<3I", page, 0, width, height, stride)
        struct.pack_into("<I", page, 0x14, depth)
        struct.pack_into("<I", page, 0x10, int(depth==8))
        struct.pack_into("<I", page, self.pixel_offset, pixels)
        mask_record = None
        if mask is not None:
            mask_record = self.page(width, height, mask, 8)
            struct.pack_into("<I", page, self.pixel_offset-12, mask_record["page"])
        address=self.memory(page)
        self.write_u32(address+self.page_link,self.registered_pages[-1]if self.registered_pages else 0)
        self.registered_pages.append(address);self.write_u32(self.page_head,address)
        return {"page": address, "pixels": pixels, "size": len(data), "mask": mask_record,
                "depth":depth}

    def seed(self,value):self.write_u32(self.random_state+0x14,value)
    def random_seed(self):return self.read_u32(self.random_state+0x14)

    def extcopy_profile(self):
        cursor,registered,head=self.cursor,list(self.registered_pages),self.read_u32(self.page_head)
        try:
            self.cursor=self.DATA+0x30000;self.registered_pages=[];self.write_u32(self.page_head,0)
            source=self.page(1,1,b"\x7f",8);target=self.page(1,1,b"\x03",8)
            self.call_graphics("$grp_extcopy",[source["page"],0,0,1,1,target["page"],0,0,0])
            value=bytes(self.machine.mem_read(target["pixels"],1))
            if value not in(b"\x03",b"\x7f"):raise ValueError("unclassified indexed extcopy")
            return {"color_bytes":4 if self.pixel_offset==0x2c else 3,
                    "indexed_extcopy":value==b"\x7f"}
        finally:
            self.cursor=cursor;self.registered_pages=registered;self.write_u32(self.page_head,head)

    def call_graphics(self, name, args, discard=False):
        entry = self.entries[name]
        return self.execute("syscallp" if discard else "syscall",
            [(0, int(value) & 0xffffffff) for value in reversed(args)],
            struct.pack("<IH", int(entry["hash"], 16), len(args)), budget=1_000_000)


def inspect():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", default="RURINOKASANE")
    parser.add_argument("--name", choices=NAMES)
    args = parser.parse_args()
    samples = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch = json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    sample = next(row for row in samples if row["sample"] == args.sample)
    entries = next(row["entries"] for row in dispatch if row["sample"] == args.sample)
    oracle = GraphicsOracle(sample, entries)
    lines = []
    for name in (args.name,) if args.name else NAMES:
        if name not in oracle.entries:
            continue
        entry = oracle.entries[name]
        at = oracle.functions.syscall_helper(entry)
        lines.append(f"{sample['sample']} {name} {entry['hash']} {at:08x}")
        body = oracle.functions.body(at)
        for row in body:
            lines.append(f"{row.address:08x} {row.mnemonic} {row.op_str}")
        for target in dict.fromkeys(row.operands[0].imm for row in body if row.mnemonic == "call"
                                   and row.op_str.startswith("0x")):
            lines.append(f"CHILD {target:08x}")
            for row in oracle.functions.body(target):
                lines.append(f"{row.address:08x} {row.mnemonic} {row.op_str}")
    folder = archive_dir("graphics_remaining")
    path = folder / (folder.name[:8] + "_disassembly.txt")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(path.relative_to(ROOT))
    print("\n".join(lines))


if __name__ == "__main__":
    inspect()
