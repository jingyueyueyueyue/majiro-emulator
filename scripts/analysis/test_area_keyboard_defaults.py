"""Original area-default page-key dispatch and production input regression."""
from pathlib import Path
import struct
import sys

import unicorn
from unicorn import x86_const as reg
from lupa.lua51 import LuaRuntime

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json, sha256
from original_opcode import OpcodeOracle
from test_menu_focus_semantics import HARNESS

KEYS = ((33, 1), (34, 2), (132, 8), (133, 16), (147, 4))
COMBINATIONS = [(key,) for key, _ in KEYS] + [tuple(key for key, _ in KEYS)]


def original(sample, keys):
    assert sample["exe_sha256"] == "139d1559bd83da7fa54cc8c4d30ccd45401bfba5e542c4762a5cf0e1e3b539a0"
    oracle = OpcodeOracle(sample)
    uc = oracle.machine
    captured, pushed = [], []

    def host(uc, address, size, unused):
        esp = uc.reg_read(reg.UC_X86_REG_ESP)
        value = 0
        if address == 0x460780:
            value = 12
        elif address == 0x460790:
            value = 15
        elif address == 0x4607f0:
            value = int(oracle.read_u32(esp + 4) in keys)
        elif address == 0x463640:
            pushed.append(oracle.read_u32(esp + 4))
        elif address == 0x455cb0:
            captured.append({"callback": oracle.read_u32(esp + 4),
                             "argc": oracle.read_u32(esp + 8),
                             "arguments": list(reversed(pushed[-3:]))})
        uc.reg_write(reg.UC_X86_REG_EAX, value)
        uc.reg_write(reg.UC_X86_REG_EIP, oracle.read_u32(esp))
        uc.reg_write(reg.UC_X86_REG_ESP, esp + 4)

    hooks = [uc.hook_add(unicorn.UC_HOOK_CODE, host, begin=address, end=address)
             for address in (0x463640, 0x44d9e0, 0x460780, 0x460790,
                             0x460840, 0x461170, 0x4607f0, 0x455cb0)]
    oracle.write_u32(oracle.frame + 0x34, 400)
    stop, esp = oracle.DATA + 0xff000, oracle.STACK + 0x8000
    uc.mem_write(esp, struct.pack("<3I", stop, 0, 0))
    uc.reg_write(reg.UC_X86_REG_ESP, esp)
    try:
        uc.emu_start(0x476f40, stop, count=20000, timeout=1000000)
        assert uc.reg_read(reg.UC_X86_REG_EIP) == stop
    finally:
        for hook in hooks:
            uc.hook_del(hook)
    flags = sum(bit for key, bit in KEYS if key in keys)
    expected = {"callback": 400, "argc": 3, "arguments": [12, 15, flags]}
    assert captured == [expected], captured
    return expected


def runtime(source, keys, expected):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals()["VM_SOURCE"] = (ROOT / "player/runtime/majiro_vm.lua").read_text("utf8")
    lua.globals()["INPUT_SOURCE"] = source
    lua.globals()["KEYS"] = lua.table_from({key: True for key in keys})
    lua.globals()["FLAGS"] = expected["arguments"][2]
    lua.execute(HARNESS + """
      call('$areaevent_setdefault',{0,0,0,400})
      poll({x=12.75,y=15.5,pressed=KEYS});check()
      assert(#calls==1 and calls[1].offset==40,'missing page-key callback')
      local args=calls[1].args
      assert(#args==3 and args[1]==12 and args[2]==15 and args[3]==FLAGS)
      calls={};check()
      assert(#calls==0,'a consumed page-key edge must not replay forever')
    """)


def invariants(source):
    checks = {
        "keyboard_before_hover_and_preserves_click": """
          setarea(1);setarea(2);thread.areas[2].inside=true
          call('$areaevent_setdefault',{400,500,0,400})
          poll({x=12,y=15,buttons=1,pressed={[33]=true}});check()
          assert(#calls==1 and calls[1].offset==40 and #calls[1].args==3)
          assert(thread.areas[2].inside and not thread.areas[1].inside)
          assert(state.input.buttons==1)
        """,
        "disabled_keyboard": """
          state.settings.keyboard_disabled=true
          call('$areaevent_setdefault',{0,0,0,400})
          poll({pressed={[33]=true}});check();assert(#calls==0)
        """,
        "disabled_mouse_context": """
          state.settings.mouse=false
          call('$areaevent_setdefault',{0,0,0,400})
          poll({pressed={[33]=true}});check();assert(#calls==0)
        """,
        "absent_default_retains_key_for_other_source_read": """
          call('$areaevent_setdefault',{0,0})
          poll({pressed={[33]=true}});check()
          assert(#calls==0 and call('$kb_read',{33})==1)
        """,
        "missing_callback_is_error": """
          call('$areaevent_setdefault',{0,0,0,999999})
          poll({pressed={[33]=true}})
          local ok,why=pcall(check)
          assert(not ok and tostring(why):find('area callback not present',1,true))
        """,
    }
    for body in checks.values():
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals()["VM_SOURCE"] = (ROOT / "player/runtime/majiro_vm.lua").read_text("utf8")
        lua.globals()["INPUT_SOURCE"] = source
        lua.execute(HARNESS + body)
    return list(checks)


def main():
    folder = archive_dir("area_keyboard_defaults")
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                  if s["sample"] == "magical_kanon2")
    report = {"timestamp": timestamp(), "status": "running", "cases": [],
              "device_accessed": False, "original_exe_sha256": sample["exe_sha256"],
              "scope": "actual original keyboard default boundary, exact flags/coordinates and portable key ownership",
              "evidence": str(folder.relative_to(ROOT))}
    try:
        expected = [original(sample, keys) for keys in COMBINATIONS]
        for name in ("player/runtime/majiro_input.lua", "runtime/majiro_input.lua"):
            source = (ROOT / name).read_text("utf8")
            for keys, native in zip(COMBINATIONS, expected):
                runtime(source, keys, native)
                report["cases"].append({"runtime": name, "keys": keys,
                                        "original": native, "status": "passed"})
            report.setdefault("invariants", {})[name] = invariants(source)
            report.setdefault("source_sha256", {})[name] = sha256(ROOT / name)
        source = (ROOT / "player/runtime/majiro_input.lua").read_text("utf8")
        old = source.replace("keyboard_flags=keyboard_flags+entry[2]", "keyboard_flags=0", 1)
        assert old != source
        try:
            runtime(old, COMBINATIONS[0], expected[0])
        except Exception as error:
            report["negative_control"] = {"missing_dispatch_rejected": True, "error": str(error)}
        else:
            raise AssertionError("missing keyboard default incorrectly passed")
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        write_json(folder / "report.json", report)
        write_json(ROOT / "reports/area_keyboard_defaults.json", report)
    print("Area keyboard defaults: 6 original cases, 12 runtime comparisons and 10 input invariants passed")


if __name__ == "__main__":
    main()
