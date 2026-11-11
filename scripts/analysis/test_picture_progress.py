"""Original viewed-picture registration/query normalization used by CGMODE."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua, setup_runtime
from port_common import ROOT, write_json


def run():
    o = Original()
    context = o.memory(bytes(0x5100))
    values = o.memory(bytes(16 * 8))
    o.machine.mem_write(context + 0x24, struct.pack("<I", values))
    o.machine.mem_write(context + 0x10, struct.pack("<I", 0x400))
    o.machine.mem_write(0x5d1a5c, struct.pack("<I", context))
    o.machine.mem_write(0x5d1a58, struct.pack("<I", context + 0x28))
    def boundary(uc, at, size, data):
        esp = uc.reg_read(o.reg.UC_X86_REG_ESP)
        target = struct.unpack("<I", o.read(esp, 4))[0]
        arg = struct.unpack("<I", o.read(esp + 4, 4))[0]
        if at == 0x47c440:
            char = struct.unpack("<I", o.read(esp + 8, 4))[0] & 255
            text = o.read(arg, 256).split(b"\0", 1)[0]
            index = text.find(bytes([char]))
            uc.reg_write(o.reg.UC_X86_REG_EAX, arg + index if index >= 0 else 0)
        elif at == 0x47c650:
            text = o.read(arg, 256).split(b"\0", 1)[0]
            uc.mem_write(arg, text.lower() + b"\0")
        uc.reg_write(o.reg.UC_X86_REG_ESP, esp + 4)
        uc.reg_write(o.reg.UC_X86_REG_EIP, target)
    for at in (0x47c440, 0x47c650, 0x48a437):
        o.machine.hook_add(unicorn.UC_HOOK_CODE, boundary, begin=at, end=at)
    def call(address, name):
        p = o.memory(bytes(12) + name.encode("cp932") + b"\0")
        o.machine.mem_write(context + 0x30, struct.pack("<I", 1))
        o.machine.mem_write(values + 12, struct.pack("<I", p))
        o.call(address, [1])
        return struct.unpack("<i", o.read(values + 16 + 12, 4))[0]
    lua = create_lua()
    _, vm, system, _ = setup_runtime(lua, False)
    cases = []
    for registered, query in (
        ("ev001_01", "EV001_01.RCT"), ("ev002_02.rct", "ev002_02"),
        ("folder.name/ev003.rct", "FOLDER.any"),
        ("画像.rct", "画像.rc8"),
    ):
        expected = call(0x450ca0, query)
        actual = system["providers"][0x99a5de25](vm, lua.table_from([query]))
        assert expected == actual == 0
        assert call(0x451330, registered) == system["providers"][0xeb5cc468](vm, lua.table_from([registered])) == 0
        expected = call(0x450ca0, query)
        actual = system["providers"][0x99a5de25](vm, lua.table_from([query]))
        assert expected == actual == 1
        cases.append({"registered": registered, "query": query, "before": 0, "after": 1})
    write_json(ROOT / "reports/picture_progress_tests.json", {
        "status": "passed", "source_exe_sha256": Original.IDENTITY, "cases": cases,
        "scope": "original registration, hashing and query machine code; CRT text boundaries stubbed"})
    print("original viewed-picture registration/query:", len(cases), "passed")


if __name__ == "__main__":
    run()
