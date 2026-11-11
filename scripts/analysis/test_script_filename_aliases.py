"""Compare script filename aliases with the original loader and the shared VM."""
from pathlib import Path
import hashlib
import struct
import sys

from unicorn import x86_const as reg

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from lua_bridge import create_lua
from original_strings import StringOracle
from port_common import ROOT, archive_dir, read_json, timestamp, write_json


def main():
    sample = next(s for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
                  if s["sample"] == "kizoku")
    oracle = StringOracle(sample)
    folder = archive_dir("script_filename_aliases")
    cases = ("J06cChikan.mjs", "J06cChikan", "J06cChikan.MJO",
             "folder\\J06cChikan.mjs", "folder/J06cChikan.mjs",
             "C:\\folder\\J06cChikan.mjs", "C:J06cChikan.mjs",
             "name.other", "multi.part.mjs", "folder.with.dots/name.mjs",
             "\u811a\u672c.mjs", "trailing.", "name.mjs\0ignored")
    native = []
    for name in cases:
        pointer, stack = oracle.DATA + 0x90000, oracle.STACK + 0x8000
        oracle.machine.mem_write(pointer, name.encode("cp932") + b"\0")
        oracle.machine.mem_write(stack, struct.pack("<II", 0, pointer))
        oracle.machine.reg_write(reg.UC_X86_REG_ESP, stack)
        # Execute the unmodified loader prefix, including the real CRT
        # _splitpath, strcat and uppercase routines, before resource I/O.
        oracle.machine.emu_start(0x4070a0, 0x407102, count=200000)
        assert oracle.machine.reg_read(reg.UC_X86_REG_EIP) == 0x407102
        esp = oracle.machine.reg_read(reg.UC_X86_REG_ESP)
        result = bytes(oracle.machine.mem_read(esp + 0x24, 256)).split(b"\0")[0].decode("cp932")
        native.append({"input": name, "original": result})
    for runtime in ("player/runtime",):
        lua = create_lua()
        module = lua.execute((ROOT / runtime / "majiro_vm.lua").read_text(encoding="utf8"))
        requested = []

        def load(name):
            requested.append(name)
            return lua.table_from({"name": name, "main": 0, "functions": [],
                                   "code": [[0, 0x191]]}, recursive=True)
        vm = module["new"](lua.table_from({"loader": load, "opcodes": lua.table(),
                                           "syscall": lambda *args: 0}))
        for row in native:
            value = vm["load"](vm, row["input"])
            assert value["name"].upper() == row["original"], (runtime, row, value["name"])
        assert all(not name.endswith(".mjs.mjo") for name in requested)
    report = {"status": "passed", "timestamp": timestamp(), "cases": native,
              "runtime_copies": ["player/runtime"],
              "original_exe_sha256": sample["exe_sha256"],
              "original_prefix_sha256": hashlib.sha256(
                  bytes(oracle.machine.mem_read(0x4070a0, 0x62))).hexdigest(),
              "device_accessed": False,
              "scope": "original filename/extension normalization; story entry regression is a separate stage"}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/script_filename_aliases.json", report)
    print("Script filename aliases:", len(native), "original comparisons against the independent VM passed")


if __name__ == "__main__":
    main()
