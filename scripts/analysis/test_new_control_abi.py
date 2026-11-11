"""Original state/argument checks for remaining new ABI control forms."""
from __future__ import annotations
import json
from pathlib import Path
import struct
import sys
import unicorn
from unicorn import x86_const as reg
import capstone
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256
from test_new_graphics_abi import FunctionFixture
from native_function import NativeFunctions
from audit_player_semantics import registered_providers


def main():
    scan = json.loads((ROOT / "reports/new_sample_scan.json").read_text(encoding="utf8"))
    happy = json.loads((ROOT / "reports/happy_wrapper_recovery.json").read_text(encoding="utf8"))
    rows = []

    def fixture(label):
        sample = next(s for s in scan["opcode_samples"] if s["sample"] == label)
        if label == "happy_princess":
            sample = dict(sample, path=happy["initialized_reference"], exe_sha256=happy["initialized_reference_sha256"])
        f = FunctionFixture(sample)
        f.machine = f.uc
        f.functions = NativeFunctions(f)
        f.entries = {e["name"] or e["hash"]: e for s in scan["syscall_samples"]
                     if s["sample"] == label for e in s["entries"]}
        return f

    # The script requests its return value only in the new Oh! Pants sample.
    f = fixture("oh_pants")
    f.hook(0x426010, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
    f.hook(0x416b80, lambda sp: 0)
    lua, _, _, system = registered_providers()
    for depth in (0, 1, 2, -1, 2147483647):
        f.put(0x4c1f30, depth)
        f.uc.reg_write(reg.UC_X86_REG_EBP, 0)
        f.call(0x416fbf, [])
        state = system["state"]["settings"]
        state["fast_disable_depth"] = depth
        got = system["providers"]["$fast_mode_disable"](lua.table(), lua.table())
        assert got == f.result
        assert (int(state["fast_disable_depth"]) & 0xffffffff) == f.read(0x4c1f30)
    rows.append({"sample": "oh_pants", "hash": "81ce0485", "argc": 0, "opcode": "syscall",
                 "status": "passed", "cases": 5, "fix": "retain signed disable depth and first-disable return"})
    # Original reset-ultra returns the previous actual mode and clears its
    # related playback mode fields when it was active.
    f = fixture("happy_princess")
    def reset_end(uc, address, size, unused):
        sp = uc.reg_read(reg.UC_X86_REG_ESP)
        f.result = f.read(sp)
        uc.reg_write(reg.UC_X86_REG_ESP, sp + 8)
        uc.reg_write(reg.UC_X86_REG_EIP, f.read(sp + 4))
    f.uc.hook_add(unicorn.UC_HOOK_CODE, reset_end, begin=0x41208c, end=0x41208c)
    for previous in (0, 1):
        f.put(0x49ae24, previous)
        f.uc.reg_write(reg.UC_X86_REG_EBX, 0)
        f.call(0x41193a, [])
        settings = system["state"]["settings"]
        settings["ultra_fast_mode"] = bool(previous)
        settings["fast_mode"] = bool(previous)
        got = system["providers"]["$reset_ultra_fast_mode"](lua.table(), lua.table())
        assert got == f.result and f.read(0x49ae24) == int(bool(settings["ultra_fast_mode"]))
    rows.append({"sample": "happy_princess", "hash": "f3d276f1", "argc": 0, "opcode": "syscall",
                 "status": "passed", "cases": 2, "scope": "previous mode return and mode reset"})
    f = fixture("kizoku")
    f.hook(0x416040, lambda sp: setattr(f, "result", f.read(sp + 4)) or 0)
    for value in (0, 1):
        f.put(0x481108, value)
        f.call(0x40e110, [])
        system["state"]["current_line_read"] = bool(value)
        assert system["providers"][0xf6416e87](lua.table(), lua.table()) == f.result
    rows.append({"sample": "kizoku", "hash": "f6416e87", "argc": 0, "opcode": "syscall",
                 "status": "passed", "cases": 2, "scope": "current-line read marker return"})
    # Run actual 7-argument wrappers: their two metadata values are consumed
    # and the five payload values are forwarded to the real event core.
    for label, root, frame_global, context_global, pop, crc, core in (
        ("kizoku", 0x406510, 0x4fcc44, 0x4fcc48, 0x415ff0, 0x42daf0, 0x406350),
        ("reconquista", 0x4097c0, 0x56e1a4, 0x56e1a8, 0x41c710, 0x43e5b0, 0x409610)):
        for owner in (-1, 1):
            f = fixture(label)
            frame, context, values, string = 0x10010000, 0x10020000, 0x10030000, 0x10040000
            f.put(frame_global, frame)
            f.put(context_global, context)
            f.put(frame + 8, 7)
            f.put(context + 0x24, values)
            f.put(context + 4, 1)
            f.uc.mem_write(string, bytes(12) + b"ABI_EVENT\0")
            args = [owner, string, 10, 20, 30, 40, 50]
            for i, value in enumerate(reversed(args)):
                f.put(values + i * 16 + 12, value)
            f.hook(pop, lambda sp: f.put(frame + 8, f.read(frame + 8) - 1) or 0)
            f.hook(crc, lambda sp: 0x12345678)
            forwarded = []
            f.hook(core, lambda sp: forwarded.extend(f.read(sp + i * 4) for i in range(1, 5)) or 0)
            # The helper receives argc then keep-result flag from dispatch.
            sp = 0x2000f000
            f.put(sp, 0x30000000)
            f.put(sp + 4, 7)
            f.put(sp + 8, 0)
            f.uc.reg_write(reg.UC_X86_REG_ESP, sp)
            f.uc.emu_start(root, 0x30000000, count=100000)
            assert forwarded == [0x12345678, 1, 5, 0], forwarded
        vm = lua.eval("""function()
          return {thread_id=1, argument_slice=function(self,args,first)
            local out={}for i=first,#args do out[#out+1]=args[i]end return out end,
            resume=function(self,action)self.action=action end}
        end""")()
        assert system["providers"]["$do_event_single"](vm, lua.table_from([-1, "ABI_EVENT", 10, 20, 30, 40, 50]), False) == (None, True)
        # No hooks registered: the sequence is empty but retains the exact
        # wants/discard continuation. Event core execution has separate tests.
        assert vm["action"]["done"]["wants"] is False
        rows.append({"sample": label, "hash": "29f1ac40", "argc": 7, "opcode": "syscallp",
                     "status": "passed", "cases": 2,
                     "scope": "argc-2 payload forwarding, current-thread -1 alias and discard flag",
                     "limits": "cross-thread frame injection/continuation is validated in reports/cross_thread_event_tests.json"})
    f = fixture("kizoku")
    frame, context, values = 0x10070000, 0x10071000, 0x10072000
    f.put(0x4fcc44, frame);f.put(0x4fcc48, context)
    f.put(frame + 8, 4);f.put(context + 0x24, values);f.put(context, 0)
    f.uc.mem_write(0x10030000, bytes(12) + b"fixture.mjo\0")
    f.uc.mem_write(0x10030100, bytes(12) + b"TARGET\0")
    f.hook(0x416020, lambda sp: f.top)
    f.hook(0x415ff0, lambda sp: f.put(frame + 8, f.read(frame + 8) - 1) or 0)
    seen = []
    f.hook(0x416040, lambda sp: seen.append(("count", f.read(sp + 4))) or 0)
    f.hook(0x4165c0, lambda sp: 0)
    def load(sp):
        text = bytes(f.uc.mem_read(f.read(sp + 4), 64)).split(b"\0")[0]
        seen.append(("load", text))
        return 0x10060000
    f.hook(0x4070a0, load)
    f.put(0x100600a0, 0x10080000);f.put(0x10060090, 0)
    f.hook(0x407620, lambda sp: seen.append(("resolve", f.read(sp + 4))) or 0x10080032)
    f.call(0x406880, [0x10030000, 0x10030100, 17, 23])
    hash_ = zlib.crc32(b"TARGET")
    assert seen == [("count", 2), ("load", b"fixture.mjo"), ("resolve", hash_)]
    assert f.read(frame) == 0x10080032 and f.read(frame + 8) == 2
    vm = lua.eval("""function(hash)
      local script={name='fixture.mjo',functions={{hash,50}}}
      return {functions={},load=function(self,name)assert(name=='fixture.mjo')return script end,
        argument_slice=function(self,args,first)local out={}
          for i=first,#args do out[#out+1]=args[i]end return out end,
        enter=function(self,s,offset,args,wants)self.observed={s.name,offset,args,wants}end}
    end""")(hash_)
    assert system["providers"]["$call_sub_str"](vm, lua.table_from(["fixture.mjo", "TARGET", 17, 23]), False) == (None, True)
    assert vm["observed"][1] == "fixture.mjo" and vm["observed"][2] == 50
    assert list(vm["observed"][3].values()) == [17, 23] and vm["observed"][4] is False
    rows.append({"sample": "kizoku", "hash": "edefb0b0", "argc": 4, "opcode": "syscallp",
                 "status": "passed", "cases": 1,
                 "scope": "real name hash, target filename, argc-2 payload and discard continuation",
                 "isolated_boundaries": ["script load", "VM frame creation", "function lookup target"]})
    result = {"schema": 1, "timestamp": timestamp(), "status": "passed", "cases": rows,
              "scope": "changed wrapper ABI/state forms, without game execution",
              "runtime_sha256": {name: sha256(ROOT / name) for name in (
                  "player/runtime/majiro_settings.lua", "player/runtime/majiro_syscalls.lua")}}
    folder = archive_dir("new_control_abi")
    write_json(folder / "20261110_new_control_abi.json", result)
    write_json(ROOT / "reports/new_control_abi_tests.json", result)
    print("new control ABI:", len(rows), "forms passed")


if __name__ == "__main__":
    main()
