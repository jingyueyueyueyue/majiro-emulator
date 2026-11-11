"""Original primary-thread end guard and portable child/exit separation."""
from pathlib import Path
import struct
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json
from original_calls import CallOracle
from audit_player_semantics import registered_providers


def main():
    samples = read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
    dispatch = {s["sample"]: s["entries"] for s in read_json(ROOT / "reports/syscall_dispatch.json")["samples"]}
    cases = []
    for sample in samples:
        oracle = CallOracle(sample)
        warnings = []
        oracle.diagnostic_sink = lambda text: warnings.append(text) or True
        entry = next(e for e in dispatch[sample["sample"]] if e["name"] == "$thread_end")
        for supplied in (False, True):
            for discard in (False, True):
                before = len(warnings)
                values = [(0, 0)] if supplied else []
                result = oracle.execute("syscallp" if discard else "syscall", values,
                                        struct.pack("<IH", int(entry["hash"], 16), len(values)),
                                        context_fields={4: 0}, budget=200000)
                assert result == ([] if discard else [(0, 0)])
                assert len(warnings) == before + 1
                cases.append({"sample": sample["sample"], "explicit_main_id": supplied,
                              "discard": discard, "native_guard_reached": True})
    lua, _, _, system = registered_providers()
    mod = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text("utf8"))
    ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text("utf8"))
    lua.eval("""function(mod,ops,system)
      local script={name='thread-guard.mjo',main=0,functions={{99,0}},
        code={{0,0x191},{2,0x82c,0}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
      vm:begin(script.name)
      for _,args in ipairs{{},{0},{1}}do
        assert(system.providers['$thread_end'](vm,args)==0)
        assert(not vm.threads[1].terminate_requested)
      end
      local child=vm:spawn(99,{},true)
      assert(system.providers['$thread_end'](vm,{child})==0)
      assert(vm.threads[child].terminate_requested)
      assert(not vm.threads[1].terminate_requested)
      vm:exit_context()
      assert(vm.threads[1].terminate_requested)
    end""")(mod, ops, system)
    report = {"timestamp": timestamp(), "status": "passed", "comparisons": len(cases),
              "cases": cases, "portable_child_and_exit_checks": True,
              "scope": "primary-context protection, no-op result and native diagnostic boundary",
              "device_accessed": False}
    folder = archive_dir("primary_thread_guard")
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/player_primary_thread_tests.json", report)
    print("Primary thread:", len(cases), "original guard cases and portable child/exit checks passed")


if __name__ == "__main__":
    main()
