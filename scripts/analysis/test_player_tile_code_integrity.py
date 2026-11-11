"""Compare every recovered tile instruction and constant with the original EXE."""
from collections import Counter
from pathlib import Path
import sys

import capstone
from lua_bridge import create_lua, table_value
from native_function import NativeFunctions
from original_x86 import Original
from original_graphics_remaining import GraphicsOracle
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, sha256, timestamp, write_json


def run():
    oracle = Original()
    functions = NativeFunctions(oracle)
    lua = create_lua()
    sample = next(row for row in json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
                  if row["exe_sha256"] == oracle.IDENTITY)
    entries = next(row["entries"] for row in json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
                   if row["exe_sha256"] == oracle.IDENTITY)
    initialized = GraphicsOracle(sample, entries)
    report = {"status": "passed", "timestamp": timestamp(),
              "original_exe_sha256": oracle.IDENTITY, "tables": [], "failures": []}
    for relative, legacy in (("player/runtime/majiro_tile_program.lua", False),
                             ("player/tables/tile_ai.lua", True)):
        data = lua.execute((ROOT / relative).read_text(encoding="utf8"))
        instructions = data["instructions" if legacy else "code"]
        operations = Counter()
        narrow = []
        for at, saved in instructions.items():
            actual = table_value(saved)
            row = functions.instruction(at)
            expected = [row.mnemonic, at+row.size] if legacy else [at+row.size, row.mnemonic]
            for operand in row.operands:
                if operand.type == capstone.CS_OP_IMM:
                    converted = ["i", operand.imm]
                elif operand.type == capstone.CS_OP_REG:
                    converted = ["r", row.reg_name(operand.reg)]
                    if not legacy:
                        converted.append(operand.size)
                else:
                    mem = operand.mem
                    base, index = row.reg_name(mem.base) if mem.base else "", row.reg_name(mem.index) if mem.index else ""
                    converted = (["m", operand.size, base, index, mem.scale, mem.disp] if legacy else
                                 ["m", base, index, mem.scale, mem.disp, operand.size])
                expected.append(converted)
                if operand.size < 4 and row.mnemonic in ("cmp", "test", "add", "sub", "inc", "dec", "shl", "shr"):
                    narrow.append({"address": hex(at), "instruction": row.mnemonic+" "+row.op_str})
            if actual != expected:
                report["failures"].append({"path": relative, "address": hex(at),
                                            "actual": actual, "expected": expected})
            operations[row.mnemonic] += 1
        constant_count = 0
        for at, value in data["constants"].items():
            # The AI generator captured initialized CRT/import data from
            # GraphicsOracle; its exact generation boundary is part of
            # provenance, separately from raw instruction-byte equality.
            observed = (initialized.read_u32(at) if legacy else
                        int.from_bytes(oracle.read(at, 4), "little"))
            if int(value) & 0xffffffff != observed:
                report["failures"].append({"path": relative, "constant": hex(at),
                                            "actual": value, "expected": observed})
            constant_count += 1
        report["tables"].append({"path": relative, "sha256": sha256(ROOT / relative),
                                 "instruction_count": sum(operations.values()), "operations": dict(operations),
                                 "constant_count": constant_count, "narrow_arithmetic": narrow})
    if report["failures"]:
        report["status"] = "failed"
    folder = archive_dir("tile_code_integrity")
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/player_tile_code_integrity_tests.json", report)
    print(report["status"], [(r["instruction_count"], r["constant_count"],
                              len(r["narrow_arithmetic"])) for r in report["tables"]], flush=True)
    return report


if __name__ == "__main__":
    raise SystemExit(run()["status"] != "passed")
