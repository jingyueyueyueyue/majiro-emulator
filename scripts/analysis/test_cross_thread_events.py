"""Original target-frame injection plus portable waiting/ownership regressions."""
from __future__ import annotations
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json, sha256
from test_new_graphics_abi import FunctionFixture
from recover_happy_wrapper import invoke
from audit_player_semantics import registered_providers


def main():
    scan = json.loads((ROOT / "reports/new_sample_scan.json").read_text(encoding="utf8"))
    sample = next(s for s in scan["opcode_samples"] if s["sample"] == "kizoku")
    f = FunctionFixture(sample)
    source, target, values, target_values, event = (
        0x10010000, 0x10020000, 0x10030000, 0x10040000, 0x10050000)
    for context, identifier, stack in ((source, 1, values), (target, 2, target_values)):
        f.uc.mem_write(context, bytes(0x5000))
        f.put(context + 4, identifier);f.put(context + 0x10, 64);f.put(context + 0x24, stack)
    f.put(0x4fcc48, source);f.put(0x4fcc44, source + 0x28);f.put(source + 0x30, 5)
    for i, value in enumerate((50, 40, 30, 20, 10)):
        f.uc.mem_write(values + i * 16, struct.pack("<4I", 0, 0, 0, value))
    f.put(target + 0x28, 0x401234)
    f.put(target + 0x28 + 0x24, 123456)
    f.put(0x4f3bd0, target);f.put(0x4810d8, event)
    f.put(event + 8, 0x12345678);f.put(event + 12, 0x402345);f.put(event + 16, 0)
    f.hook(0x42b170, lambda sp: 0x10060000)
    f.hook(0x4374ab, lambda sp: 0)
    invoke(f, 0x406350, [0x12345678, 2, 5, 0])
    assert f.read(source) == 0 and f.read(source + 0x30) == 0
    assert f.read(target) == 1 and f.read(target + 0x28 + 68) == 0x402345
    assert f.read(target + 0x28 + 0x24) == 123456
    assert f.read(0x4fcc48) == source and f.read(0x4fcc44) == source + 0x28
    assert [f.read(target_values + i * 16 + 12) for i in range(6)] == [50, 40, 30, 20, 10, 5]
    checks = ["original V1 event core creates target frame, copies five payload values, preserves parent and restores sender"]
    lua, _, _, system = registered_providers()
    module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
    ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf8"))
    make = lua.eval("""function(mod,ops,system)
      local script={name='cross-event.mjo',source_sha256='fixture-source',main=0,
        functions={{0x55,200},{0x77,100},{0x88,120}},
        code={{0,0x191},{2,0x82c,0},
          {100,0x835,0x1234,0},{104,0x800,0},{110,0x82b},
          {120,0x835,0x1235,0},{124,0x800,0},{130,0x82b},
          {200,0x191},{202,0x82c,200}}}
      local log={}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=function(vm,hash,args,wants)
          if hash==0x1234 or hash==0x1235 then
            log[#log+1]={hash=hash,owner=vm.thread_id,args=vm:frame().args,types=vm:frame().arg_types}
            return 0
          end
          return system.invoke(vm,hash,args,wants)
        end}
      vm:begin(script.name);assert(vm:spawn(0x55,{},false)==2)
      vm:select_thread(2);vm.waiting={kind='time',until_ms=5000};vm:select_thread(1)
      system.state.events.ABI_EVENT={{script=script.name,offset=100}}
      return vm,log
    end""")
    vm, log = make(module, ops, system)
    args = lua.table_from([2, "ABI_EVENT", 10, 20, 30, 40, 50])
    sender_frames = len(vm["frames"])
    assert system["providers"]["$do_event_single"](vm, args, True) == 0
    assert vm["thread_id"] == 1 and len(vm["frames"]) == sender_frames
    assert len(vm["threads"][2]["frames"]) == 2 and vm["threads"][2]["waiting"] is None
    vm["run"](vm, 512)
    assert len(log) == 1 and log[1]["owner"] == 2
    assert list(log[1]["args"].values()) == [10, 20, 30, 40, 50]
    assert vm["threads"][2]["waiting"]["until_ms"] == 5000
    assert len(vm["threads"][2]["frames"]) == 1 and len(vm["threads"][2]["stack"]) == 0
    checks.append("cross-thread callback runs in target, sender returns immediately, target parent wait resumes without stack leak")
    vm, log = make(module, ops, system)
    assert system["providers"]["$do_event_single"](vm, args, False) == 0
    codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text(encoding="utf8"))
    saved = codec["clone"](vm["export_state"](vm))
    vm["import_state"](vm, saved)
    vm["run"](vm, 512)
    assert len(log) == 1 and vm["threads"][2]["waiting"]["until_ms"] == 5000
    checks.append("queued callback and suspended wait survive an exported/imported VM checkpoint")
    vm, log = make(module, ops, system)
    assert system["providers"]["$do_event_single"](vm, args, False) == 0
    system["state"]["events"]["ABI_EVENT"][1]["offset"] = 120
    assert system["providers"]["$do_event_single"](vm, args, False) == 0
    vm["run"](vm, 512)
    assert [log[i]["hash"] for i in range(1, len(log) + 1)] == [0x1235, 0x1234]
    assert vm["threads"][2]["waiting"]["until_ms"] == 5000
    checks.append("a later injection interrupts the existing event frame, then both continuations restore the original wait")
    vm, log = make(module, ops, system)
    for target_id in (99, 2):
        if target_id == 2:
            vm["threads"][2]["finished"] = True
        dead_args = lua.table_from([target_id, "ABI_EVENT", 10, 20, 30, 40, 50])
        assert system["providers"]["$do_event_single"](vm, dead_args, False) == 0
    assert len(vm["threads"][2]["frames"]) == 1 and len(log) == 0
    checks.append("missing and finished native target identities consume the request without reviving a thread")
    vm, log = make(module, ops, system)
    system["state"]["events"]["ABI_EVENT"][1]["offset"] = 100
    assert system["providers"]["$do_event_single"](vm, args, False) == 0
    vm["terminate"](vm, 2)
    vm["run"](vm, 512)
    assert vm["threads"][2]["finished"] and not vm["threads"][2]["waiting"]
    checks.append("termination unwinds event frames and does not restore the suspended parent wait")
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "checks": checks,
              "original_exe_sha256": sample["exe_sha256"], "original_core_va": 0x406350,
              "runtime_sha256": {name: sha256(ROOT / name) for name in (
                  "player/runtime/majiro_vm.lua", "player/runtime/majiro_syscalls.lua")},
              "scope": "original V1 integer payload/frame injection plus portable continuation/ownership/save behavior",
              "limits": "actual OS input delivery, native dialog interruption and gameplay timing remain separate host coverage"}
    folder = archive_dir("cross_thread_events")
    write_json(folder / "20261110_cross_thread_events.json", report)
    write_json(ROOT / "reports/cross_thread_event_tests.json", report)
    print("cross-thread events:", len(checks), "native/model checks passed")


if __name__ == "__main__":
    main()
