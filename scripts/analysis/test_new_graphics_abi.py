"""Targeted original ABI checks for the newly observed V1 graphics forms."""
from __future__ import annotations
import json
from pathlib import Path
import struct
import sys
import pefile
import unicorn
from unicorn import x86_const as reg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256
from audit_player_semantics import registered_providers


class FunctionFixture:
    def __init__(self, sample):
        self.sample = sample
        raw = Path(sample["path"]).read_bytes()
        assert sha256(Path(sample["path"])) == sample["exe_sha256"]
        pe = pefile.PE(data=raw)
        self.uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
        self.uc.mem_map(pe.OPTIONAL_HEADER.ImageBase, (pe.OPTIONAL_HEADER.SizeOfImage + 4095) & ~4095)
        self.uc.mem_write(pe.OPTIONAL_HEADER.ImageBase, pe.get_memory_mapped_image())
        self.uc.mem_map(0x10000000, 0x100000)
        self.uc.mem_map(0x20000000, 0x10000)
        self.uc.mem_map(0x30000000, 0x1000)
        self.top, self.result = 0x10010000, None
        self.hooks = []

    def read(self, at):
        return struct.unpack("<I", self.uc.mem_read(at, 4))[0]

    def put(self, at, value):
        self.uc.mem_write(at, struct.pack("<I", value & 0xffffffff))

    def hook(self, at, callback, cleanup=0):
        def invoke(uc, address, size, _):
            sp = uc.reg_read(reg.UC_X86_REG_ESP)
            value = callback(sp)
            if value is not None:
                uc.reg_write(reg.UC_X86_REG_EAX, value & 0xffffffff)
            uc.reg_write(reg.UC_X86_REG_EIP, self.read(sp))
            uc.reg_write(reg.UC_X86_REG_ESP, sp + 4 + cleanup)
        self.hooks.append(self.uc.hook_add(unicorn.UC_HOOK_CODE, invoke, begin=at, end=at))

    def call(self, at, values):
        for i, value in enumerate(values):
            self.put(self.top - 16 * i + 12, value)
        sp = 0x2000f000
        self.put(sp, 0x30000000)
        self.put(sp + 4, len(values))
        self.uc.reg_write(reg.UC_X86_REG_ESP, sp)
        self.uc.reg_write(reg.UC_X86_REG_EFLAGS, 2)
        self.uc.emu_start(at, 0x30000000, count=100000)
        assert self.uc.reg_read(reg.UC_X86_REG_EIP) == 0x30000000


def main():
    report = json.loads((ROOT / "reports/new_sample_scan.json").read_text(encoding="utf8"))
    kizoku = next(s for s in report["opcode_samples"] if s["sample"] == "kizoku")
    rows = []
    # Real 0x4288C0 reads/ignores optional values until argc >= 5. Isolate
    # only member lookup, image decoding, CRT path splitting and allocation.
    for args in ([0, 77, 88], [0], [0, 1, 2, 30, 40], [0, 1, 2, 30, 40, 901]):
        f = FunctionFixture(kizoku)
        text = 0x10020000
        f.uc.mem_write(text, bytes(12) + b"fixture.rct\0")
        values = [text, *args[1:]]
        f.put(0x10030000, 640)
        f.put(0x10030004, 480)
        f.hook(0x416020, lambda sp: f.top)
        f.hook(0x416040, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
        lookups = []
        def lookup(sp):
            lookups.append(1)
            return 0 if len(lookups) == 1 else 1
        f.hook(0x412050, lookup)
        f.hook(0x4122a0, lambda sp: 0x10030000)
        f.hook(0x42af70, lambda sp: 900)
        f.hook(0x42af00, lambda sp: 0)
        def split(sp):
            f.uc.mem_write(f.read(sp + 16), b"fixture\0")
            return 0
        f.hook(0x43807a, split)
        created = []
        def create(sp):
            created.extend(f.read(sp + i * 4) for i in range(1, 7))
            return 0x10040000
        f.hook(0x428710, create)
        f.hook(0x406d70, lambda sp: (_ for _ in ()).throw(ValueError("original diagnostic")))
        f.call(0x4288c0, values)
        expected = [0x10030000, *(args[1:5] if len(args) >= 5 else [0, 0, 640, 480]),
                    args[5] if len(args) >= 6 else 900]
        assert created == expected, (args, created, expected)
        lua, _, _, system = registered_providers()
        state = system["state"]
        state["frontbuffer"] = 900
        state["pages"][900] = lua.table_from({"width": 640, "height": 480, "depth": 32})
        state["pages"][800] = lua.table_from({"width": 640, "height": 480, "depth": 32})
        state["pages"][901] = lua.table_from({"width": 640, "height": 480, "depth": 32})
        system["providers"]["$page_create_file_withalfa"] = lambda vm, args: 800
        vm = lua.table()
        vm["frame"] = lambda self: lua.table_from({"script": {"name": "fixture.mjo"}}, recursive=True)
        identifier = system["providers"]["$sprite_create_file"](vm, lua.table_from(["fixture.rct", *args[1:]]))
        sprite = state["sprites"][identifier]
        actual = [0x10030000, *[sprite[key] for key in ("sx", "sy", "width", "height", "destination")]]
        assert actual == expected, (actual, expected)
        rows.append({"argc": len(args), "native_arguments": created, "status": "passed",
                     "return_sprite_created": f.result == 0x10040000})
    result = {"schema": 1, "timestamp": timestamp(), "status": "passed", "cases": rows,
              "exe_sha256": kizoku["exe_sha256"],
              "scope": "original V1 sprite_create_file ignores 2-4 supplied clip values until argc >= 5",
              "isolated_boundaries": ["resource lookup", "image decoding", "CRT splitpath", "sprite allocation"],
              "source_sha256": sha256(ROOT / "player/runtime/majiro_sprites.lua")}
    folder = archive_dir("new_graphics_abi")
    write_json(folder / "20261110_new_graphics_abi.json", result)
    write_json(ROOT / "reports/new_graphics_abi_tests.json", result)
    print("new graphics ABI: 4 original sprite creation comparisons passed")


if __name__ == "__main__":
    main()
