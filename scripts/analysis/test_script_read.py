"""Original $is_read matches the existence of a script read-record, not a text bit."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua, setup_runtime
from port_common import ROOT, write_json


def original(name, records):
    o = Original()
    tail = 0
    for record in records:
        node = bytearray(0x94)
        encoded = record.encode("cp932")
        node[:len(encoded)] = encoded
        struct.pack_into("<I", node, 0x8c, tail)
        tail = o.memory(bytes(node))
    o.machine.mem_write(0x4dc354, struct.pack("<I", tail))
    request = o.memory(bytes(12) + name.encode("cp932") + b"\0")
    outputs = []
    def boundary(uc, at, size, data):
        esp = uc.reg_read(o.reg.UC_X86_REG_ESP)
        target = struct.unpack("<I", o.read(esp, 4))[0]
        arg = struct.unpack("<I", o.read(esp + 4, 4))[0]
        if at == 0x489a05:
            stem = struct.unpack("<I", o.read(esp + 16, 4))[0]
            text = o.read(arg, 512).split(b"\0", 1)[0].decode("cp932")
            base = text.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            uc.mem_write(stem, base.encode("cp932") + b"\0")
        elif at == 0x47c8b0:
            uc.mem_write(arg, o.read(arg, 512).split(b"\0", 1)[0].lower() + b"\0")
        elif at == 0x47c550:
            other = struct.unpack("<I", o.read(esp + 8, 4))[0]
            a = o.read(arg, 256).split(b"\0", 1)[0].lower()
            b = o.read(other, 256).split(b"\0", 1)[0].lower()
            uc.reg_write(o.reg.UC_X86_REG_EAX, 0 if a == b else 1)
        elif at == 0x44eab0:
            outputs.append(arg)
            return  # Original.syscall supplies this boundary's return.
        uc.reg_write(o.reg.UC_X86_REG_ESP, esp + 4)
        uc.reg_write(o.reg.UC_X86_REG_EIP, target)
    for at in (0x489a05, 0x47c8b0, 0x47c550, 0x44eab0):
        o.machine.hook_add(unicorn.UC_HOOK_CODE, boundary, begin=at, end=at)
    o.syscall(0x441af0, [request])
    assert len(outputs) == 1
    return outputs[0]


def run():
    lua = create_lua()
    _, vm, system, _ = setup_runtime(lua, False)
    cases = []
    for name, records, expected in (
        ("staffroom", [], 0), ("staffroom", ["staffroom.mjo"], 1),
        ("folder/STAFFROOM.mjo", ["other.mjo", "staffroom.mjo"], 1),
        ("未読.mjo", ["staffroom.mjo"], 0),
    ):
        actual = original(name, records)
        assert actual == expected
        system["state"]["progress"]["scripts"] = lua.table_from({r: True for r in records})
        assert system["providers"]["$is_read"](vm, lua.table_from([name])) == expected
        cases.append({"request": name, "records": records, "result": actual})
    write_json(ROOT / "reports/script_read_tests.json", {
        "status": "passed", "source_exe_sha256": Original.IDENTITY, "cases": cases,
        "scope": "actual original script-record traversal and boolean; CRT path and string boundaries stubbed"})
    print("original script read-record query:", len(cases), "passed")


if __name__ == "__main__":
    run()
