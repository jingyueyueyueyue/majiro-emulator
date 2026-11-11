"""Execute original file-read token conversion, including 32-bit overflow."""
from pathlib import Path
import struct
import sys
import capstone

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, read_json, timestamp, write_json
from original_strings import StringOracle
from original_opcode import native_number
from original_fixture_scope import profile_for
from native_function import NativeFunctions
from audit_player_semantics import registered_providers


def main():
    samples = read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
    profiles = read_json(ROOT / "config/player_engine_profiles.json")
    dispatch = {row["exe_sha256"]: {e["name"]: e for e in row["entries"] if e["name"]}
                for row in read_json(ROOT / "reports/syscall_dispatch.json")["samples"]}
    tokens = ["0", "-1", "2147483647", "+2147483648", "-2147483649",
              "4294967297", "9007199254740993", "999999999999999999999999999",
              "-999999999999999999999999999", "123suffix", "not-a-number", ' "73" ']
    report = {"timestamp": timestamp(), "status": "running", "samples": [], "failures": [],
              "scope": "original file_read parser/CRT against the independent production syscall",
              "device_accessed": False}
    for sample in samples:
        profile = profile_for(profiles, sample, report, required=("integer_parse",))
        assert profile is not None
        oracle = StringOracle(sample)
        functions = NativeFunctions(oracle)
        entries = dispatch[sample["exe_sha256"]]
        atoi_entry = entries["$atoi"]
        atoi_helper = functions.syscall_helper(atoi_entry)
        conversion = (functions.calls(atoi_helper)[1].operands[0].imm
                      if functions.instruction(atoi_entry["selected_va"]).mnemonic == "call"
                      else atoi_helper)
        reader = functions.syscall_helper(entries["$file_read"])
        assert conversion in [row.operands[0].imm for row in functions.calls(reader)]
        buffer_offsets = {row.operands[1].mem.disp for row in functions.body(reader)
                          if row.mnemonic == "lea" and row.operands[1].type == capstone.CS_OP_MEM
                          and row.operands[1].mem.base not in (
                              capstone.x86.X86_REG_ESP, capstone.x86.X86_REG_EBP)
                          and row.operands[1].mem.disp in (4, 8)}
        assert len(buffer_offsets) == 1, (sample["sample"], buffer_offsets)
        buffer_offset = buffer_offsets.pop()
        count = 0
        for token in tokens:
            pointer = oracle.DATA + 0x50000
            oracle.machine.mem_write(pointer, bytes(4096))
            oracle.machine.mem_write(pointer + buffer_offset, token.encode("ascii") + b"\0")
            expected = oracle.execute("syscall", [(0, pointer)], struct.pack(
                "<IH", int(entries["$file_read"]["hash"], 16), 1), budget=200000)
            lua, _, _, system = registered_providers(overrides={
                "integer_parse": profile["integer_parse"],
                "file_read": lambda name, value=token: value})
            vm = lua.table()
            handle = system["providers"]["$file_open"](vm, lua.table_from(["numbers.cfg"]))
            actual = system["providers"]["$file_read"](vm, lua.table_from([handle]))
            assert len(expected) == 1
            if actual != native_number(*expected[0]):
                report["failures"].append({"sample": sample["sample"], "token": token,
                                           "original": native_number(*expected[0]), "actual": actual})
            count += 1
        report["samples"].append({"sample": sample["sample"], "cases": count,
                                  "reader": hex(reader), "shared_atoi": hex(conversion)})
        print(sample["sample"], count, "file numeric reads", flush=True)
    report["comparisons"] = sum(row["cases"] for row in report["samples"])
    report["status"] = "failed" if report["failures"] else "passed"
    folder = archive_dir("player_file_numbers")
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/player_file_numbers.json", report)
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
