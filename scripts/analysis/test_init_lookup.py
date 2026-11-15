"""Execute the original startup hash/lookup path, including duplicate init rows."""
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "convert")]
from unicorn import x86_const as reg
from original_calls import CallOracle
from lua_bridge import create_lua
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


def main():
    folder = archive_dir("init_lookup")
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                  if s["sample"] == "narci_onetwo")
    assert sample["exe_sha256"] == "c573a0901b007c1db2cc96f6277aafe15401352b7c52f4a2bb16b6bc69ce8693"
    # CallOracle validates the current EXE against its recovered hash before execution.
    oracle = CallOracle(sample)
    lua = create_lua()
    mod = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text("utf8"))
    ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text("utf8"))
    portable = lua.eval("""function(mod,ops,entries)
      local source={name='start.mjo',main=0,functions=entries,
        code={{0,0x191},{16,0x191},{32,0x191}}}
      local vm=mod.new{opcodes=ops,loader=function()return source end,syscall=function()return 0 end}
      vm:begin(source.name)
      return vm:frame().script.code[vm:frame().index][1]
    end""")
    rows = []
    report = {"status": "running", "timestamp": timestamp(), "cases": rows,
              "exe_sha256": sample["exe_sha256"], "startup_begin_va": "0046055c",
              "startup_end_va": "0046057c", "device_accessed": False,
              "vm_sha256": sha256(ROOT / "player/runtime/majiro_vm.lua"),
              "evidence": str(folder.relative_to(ROOT))}
    try:
        for functions in ([(2981858012, 16)], [(2981858012, 16), (2981858012, 32)],
                          [(2981858012, 32), (2981858012, 16)]):
            base = oracle.DATA + 0x34000
            oracle.registry([{"code_base": base, "functions": functions}])
            oracle.write_u32(oracle.frame_global, oracle.frame)
            oracle.write_u32(oracle.frame, base)
            oracle.machine.reg_write(reg.UC_X86_REG_ESP, oracle.STACK + 0x8000)
            # This includes the real CRC of "$init@GLOBAL" and the startup
            # resolver, rather than substituting ordinary CALL lookup.
            oracle.machine.emu_start(0x46055c, 0x46057c, count=100000)
            assert oracle.machine.reg_read(reg.UC_X86_REG_EIP) == 0x46057c
            expected = oracle.read_u32(oracle.frame) - base
            actual = portable(mod, ops, lua.table_from(functions, recursive=True))
            rows.append({"functions": functions, "original": expected, "portable": actual})
            assert actual == expected, rows[-1]
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        write_json(folder / "report.json", report)
        write_json(ROOT / "reports/init_lookup_tests.json", report)
    print("Init lookup: 3 actual startup-path comparisons passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
