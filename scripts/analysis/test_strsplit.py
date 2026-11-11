"""Original comma-field whitespace/quote rules used by CG and replay menus."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua, setup_runtime
from port_common import ROOT, write_json


def original(text, index):
    o = Original()
    p = o.memory(bytes(12) + text.encode("cp932") + b"\0")
    result = []
    def boundary(uc, at, size, data):
        esp = uc.reg_read(o.reg.UC_X86_REG_ESP)
        target = struct.unpack("<I", o.read(esp, 4))[0]
        arg = struct.unpack("<I", o.read(esp + 4, 4))[0]
        if at == 0x44eb30:
            result.append(o.read(arg, 2048).split(b"\0", 1)[0].decode("cp932"))
        elif at == 0x489c60:
            dest, src, count = struct.unpack("<3I", o.read(esp + 4, 12))
            uc.mem_write(dest, o.read(src, count))
        elif at == 0x48b0d0:
            dest, value, count = struct.unpack("<3I", o.read(esp + 4, 12))
            uc.mem_write(dest, bytes([value & 255]) * count)
        elif at == 0x47c440:
            string, char = struct.unpack("<2I", o.read(esp + 4, 8))
            raw = o.read(string, 2048).split(b"\0", 1)[0]
            position = raw.find(bytes([char & 255]))
            uc.reg_write(o.reg.UC_X86_REG_EAX, string + position if position >= 0 else 0)
        uc.reg_write(o.reg.UC_X86_REG_ESP, esp + 4)
        uc.reg_write(o.reg.UC_X86_REG_EIP, target)
    for at in (0x44eb30, 0x489c60, 0x48b0d0, 0x47c440):
        o.machine.hook_add(unicorn.UC_HOOK_CODE, boundary, begin=at, end=at)
    o.syscall(0x430a00, [p, index])
    assert len(result) == 1
    return result[0]


def run():
    lua = create_lua()
    _, vm, system, _ = setup_runtime(lua, False)
    cases = []
    for text, index in (
        ("cgmode_off, cgmode_on, cgmode_push", 1),
        ('  "mode off" , "mode on" ', 0),
        (", ,CG, ", 1), (", ,CG, ", 2),
        ("space　, 全角　 ", 1), ("A,B", 5), ("A,B", -1),
    ):
        expected = original(text, index)
        actual = system["providers"]["$strsplit$"](vm, lua.table_from([text, index]))
        assert actual == expected, (text, index, expected, actual)
        cases.append({"text": text, "index": index, "original_and_runtime": actual})
    report = {"status": "passed", "source_exe_sha256": Original.IDENTITY, "cases": cases,
              "scope": "original field-selection/trim/quote machine code; string output and CRT boundaries stubbed"}
    write_json(ROOT / "reports/strsplit_tests.json", report)
    print("original comma fields:", len(cases), "passed")
    return report


if __name__ == "__main__":
    run()
