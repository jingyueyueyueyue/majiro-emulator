"""Original event-owner/stack order versus actual portable syscall execution."""
from itertools import product
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json, sha256
from original_event_dispatch import original_frame_case
from audit_player_semantics import registered_providers
from lua_bridge import table_value

COMBINATIONS = list(product(
    ((1,), (2,), (1, 1), (2, 1), (1, 2)), (None, 1, 2), (False, True)))


def runtime_case(owners, target, wants, port=False, checkpoint=False, syscall_source=None):
    lua, _, _, system = registered_providers()
    if port or syscall_source:
        # Supply the actual corresponding implementation to the same host
        # boundary fixture. Neither source code nor a game is patched on disk.
        def module(name):
            path = ROOT / name if port else ROOT / "player" / name
            return lua.execute(path.read_text("utf8"))
        source = syscall_source or (ROOT / "runtime/majiro_syscalls.lua").read_text("utf8")
        options = lua.table_from({"names": {}, "width": 1280, "height": 720,
                                 "config_files": {}, "config_lines": {}, "environment": {},
                                 "resource_names": {}, "image_metadata": {}, "movies": {}},
                                recursive=True)
        options["modules"] = module
        options["now"] = lambda: 0
        system = lua.execute(source)["new"](options)
    module = lua.execute((ROOT / ("runtime" if port else "player/runtime") / "majiro_vm.lua").read_text("utf8"))
    ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text("utf8"))
    vm, log = lua.eval("""function(mod,ops,system)
      local script={name='event-dispatch-fixture.mjo',source_sha256='fixture-only',main=0,
        functions={{0x55,200}},
        code={{0,0x191},{2,0x82c,0},
          {100,0x835,0x1234,0},{104,0x800,71},{110,0x82b},
          {120,0x835,0x1235,0},{124,0x800,93},{130,0x82b},
          {200,0x191},{202,0x82c,200}}}
      local log={}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=function(vm,hash,args,wants)
          log[#log+1]={hash=hash,owner=vm.thread_id}
          return 0
        end}
      vm:begin(script.name);vm:spawn(0x55,{},false)
      vm:each_thread(function()vm.waiting={kind='time',until_ms=5000}end)
      return vm,log
    end""")(module, ops, system)
    callbacks = [{"owner": owner, "script": "event-dispatch-fixture.mjo",
                  "offset": 100 + index * 20}
                 for index, owner in enumerate(owners)]
    system["state"]["events"]["SAVEMENU"] = lua.table_from(callbacks, recursive=True)
    name = "$do_event" if target is None else "$do_event_single"
    args = ["SAVEMENU"] if target is None else [target, "SAVEMENU"]
    value, deferred = (None, False)
    result = system["providers"][name](vm, lua.table_from(args), wants)
    if isinstance(result, tuple):
        value, deferred = result
    else:
        value = result
    # Mirror the VM syscall opcode's immediate-return path for foreign targets.
    if wants and not deferred:
        vm["push"](vm, value)
    expected_pcs = {owner: ([100 + i * 20 for i, who in enumerate(owners) if who == owner]
                             if target is None else
                             list(reversed([100 + i * 20 for i in range(len(owners))])))
                    for owner in (1, 2)}
    if target is not None:
        expected_pcs[3 - target] = []
    vm["save_thread"](vm)
    for owner in (1, 2):
        thread = vm["threads"][owner]
        frames = list(thread["frames"].values())
        pcs = [frame["script"]["code"][frame["index"]][1] for frame in frames[1:]]
        assert pcs == expected_pcs[owner], (owners, target, owner, pcs, expected_pcs)
    if checkpoint:
        codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text("utf8"))
        exported = codec["clone"](vm["export_state"](vm))
        vm["import_state"](vm, exported)
    vm["run"](vm, 1024)
    actual = {}
    for _, item in log.items():
        actual.setdefault(item["owner"], []).append(item["hash"])
    expected = {owner: [0x1234 + (offset - 100) // 20 for offset in reversed(pcs)]
                for owner, pcs in expected_pcs.items() if pcs}
    assert actual == expected, (actual, expected)
    for owner in (1, 2):
        thread = vm["threads"][owner]
        assert len(thread["frames"]) == 1
        assert thread["waiting"]["until_ms"] == 5000
        assert len(thread["stack"]) == int(wants and owner == 1)
        if wants and owner == 1:
            assert thread["stack"][1] == 0, "callback return leaked into syscall result"
    return {"owners": owners, "target": target, "wants": wants,
            "checkpoint": checkpoint, "runtime": "port" if port else "player", "status": "passed"}


def main():
    folder = archive_dir("event_dispatch_semantics")
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                  if s["sample"] == "narci_himeko")
    report = {"timestamp": timestamp(), "status": "running", "original": [], "runtime": [],
              "scope": "actual native context/frame routing and portable owners/order/results/wait restore/checkpoints",
              "device_accessed": False, "evidence": str(folder.relative_to(ROOT))}
    try:
        for args in COMBINATIONS:
            report["original"].append(original_frame_case(sample, *args))
            for port in (False, True):
                report["runtime"].append(runtime_case(*args, port=port))
        report["checkpoint"] = [runtime_case(owners, target, True, checkpoint=True)
                                for owners, target in (((1, 1), None), ((2, 1), None), ((1, 2), 2))]
        report["status"] = "passed"
        report["source_sha256"] = {str(p.relative_to(ROOT)): sha256(p)
                                   for prefix in ("player/runtime", "runtime")
                                   for p in (ROOT / prefix).glob("majiro_*")
                                   if p.name in ("majiro_vm.lua", "majiro_syscalls.lua")}
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        write_json(folder / "report.json", report)
        write_json(ROOT / "reports/event_dispatch_semantics.json", report)
    print("Event dispatch: 30 full native cases, 60 runtime comparisons and 3 checkpoint cases passed")


if __name__ == "__main__":
    main()
