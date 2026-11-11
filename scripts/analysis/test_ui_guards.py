"""Original inline mouse/previous-selection guards and disabled main input."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua, setup_runtime
from model_session import Session
from port_common import ROOT, write_json


def run():
    source = Original()
    context = source.memory(bytes(0x5100))
    source.machine.mem_write(0x5d1a5c, struct.pack("<I", context))
    results = []
    def boundary(uc, at, size, data):
        esp = uc.reg_read(source.reg.UC_X86_REG_ESP)
        if at == 0x43dc9f:
            value = struct.unpack("<i", source.read(esp, 4))[0]
            results.append(value)
            uc.reg_write(source.reg.UC_X86_REG_EIP, source.STOP)
            return
        target = struct.unpack("<I", source.read(esp, 4))[0]
        if at == 0x48b0d0:
            dest, value, count = struct.unpack("<3I", source.read(esp+4, 12))
            uc.mem_write(dest, bytes([value & 255]) * count)
        uc.reg_write(source.reg.UC_X86_REG_ESP, esp+4)
        uc.reg_write(source.reg.UC_X86_REG_EIP, target)
    for at in (0x458070, 0x43dc9f, 0x48b0d0):
        source.machine.hook_add(unicorn.UC_HOOK_CODE, boundary, begin=at, end=at)
    lua = create_lua()
    _, vm, system, _ = setup_runtime(lua, False)
    cases = []
    for name, address in (
        ("$mouse_disable", 0x440104), ("$mouse_disable", 0x440104),
        ("$mouse_enable", 0x43fc9c), ("$mouse_disable", 0x440104),
        ("$mouse_enable", 0x43fc9c),
        ("$prevsel_enable", 0x43e93c), ("$prevsel_disable", 0x43e008),
        ("$prevsel_disable", 0x43e008), ("$prevsel_enable", 0x43e93c),
    ):
        source.call(address, [])
        actual = system["providers"][name](vm, lua.table())
        assert actual == results[-1], (name, actual, results[-1])
        mouse_disabled = struct.unpack("<I", source.read(0x533c0c, 4))[0]
        prev_enabled = struct.unpack("<I", source.read(0x4dc33c, 4))[0]
        assert (system["state"]["settings"]["mouse"] is False) == bool(mouse_disabled)
        assert bool(system["state"]["settings"]["prevsel"]) == bool(prev_enabled)
        cases.append({"command": name, "result": actual, "mouse_disabled": mouse_disabled,
                      "previous_selection": prev_enabled})
    source.machine.mem_write(0x533c0c, struct.pack("<I", 1))
    source.machine.mem_write(0x533c08, struct.pack("<I", 1))
    source.machine.mem_write(0x533d20, bytes([1]) * 256)
    source.machine.mem_write(0x534220, bytes([1]) * 1024)
    source.call(0x44c670, [])
    assert source.read(0x533d20, 256) == bytes(256)
    assert source.read(0x534220, 1024) == bytes(1024)
    assert source.read(0x533c08, 4) == bytes(4)
    system["providers"]["$mouse_disable"](vm, lua.table())
    system["input"]["sample"](lua.table_from(
        {"x": 100, "y": 200, "buttons": 1, "held_buttons": 1,
         "pressed": {13: True}, "down": {17: True}}, recursive=True))
    assert system["state"]["input"]["buttons"] == 0
    assert not list(system["state"]["input"]["pressed"].keys())
    assert not list(system["state"]["input"]["down"].keys())
    # Exercise an actual title menu, not only a scalar guard.
    s = Session(False)
    assert s.advance() == "ui"
    s.system["providers"]["$mouse_disable"](s.vm, s.lua.table())
    s.event(x=172, y=242, buttons=1, pressed={13: True})
    s.vm["run"](s.vm, 2000)
    assert "00プロローグ01.mjo" not in s.vm["scripts"]
    s.system["providers"]["$mouse_enable"](s.vm, s.lua.table())
    s.system["state"]["ui_waiting"] = False
    assert s.advance() == "ui"
    assert s.click(1) == "text", s.summary()
    report = {"status": "passed", "source_exe_sha256": Original.IDENTITY,
              "cases": cases, "disabled_main_keyboard_cleared": True,
              "title_disabled_and_reenabled": True,
              "scope": "actual original inline wrappers, main input snapshot clearing and original title controls"}
    write_json(ROOT / "reports/ui_guard_tests.json", report)
    print("UI guards:", len(cases), "original nested commands; disabled input/title recovery passed")


if __name__ == "__main__":
    run()
