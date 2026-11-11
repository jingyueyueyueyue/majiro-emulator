"""Archive bounded original-code paths used by production feature discovery."""
from pathlib import Path
import argparse
import sys

import capstone

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from native_function import NativeFunctions
from original_opcode import OpcodeOracle
from port_common import ROOT, read_json, timestamp, write_json

EVIDENCE = ROOT / "archive/2026-11/20261111_emulator_audit_profiles"
HASHES = ("b84c9953", "efd49bc9", "72db7abf", "253982f4", "d4066e31",
          "05ea6e4d", "45815f6d", "69db4512", "90d5298a", "078a756e", "a93c9856")


def inspect(sample, entries):
    oracle = OpcodeOracle(sample)
    functions = NativeFunctions(oracle)
    paths = {}
    for entry in entries:
        if entry["hash"] not in HASHES:
            continue
        address = entry["selected_va"]
        wrapper, calls = [], []
        for _ in range(64):
            row = functions.instruction(address)
            wrapper.append(f"{row.address:08x} {row.bytes.hex()} {row.mnemonic} {row.op_str}")
            address += row.size
            if row.mnemonic == "call" and row.operands[0].type == capstone.CS_OP_IMM:
                calls.append(row.operands[0].imm)
            if row.mnemonic in ("ret", "jmp"):
                break
        bodies = {}
        for depth in range(3):
            following = []
            for target in calls:
                if target in bodies:
                    continue
                body = functions.body(target, maximum_bytes=4096)
                bodies[target] = {
                    "depth": depth,
                    "instructions": [f"{r.address:08x} {r.bytes.hex()} {r.mnemonic} {r.op_str}"
                                     for r in body],
                }
                following.extend(r.operands[0].imm for r in body if r.mnemonic == "call"
                                 and r.operands[0].type == capstone.CS_OP_IMM)
            calls = following
        paths[entry["hash"]] = {"name": entry["name"], "wrapper": wrapper,
                               "callees": {f"{k:08x}": v for k, v in bodies.items()}}
    return {"sample": sample["sample"], "exe_sha256": sample["exe_sha256"], "paths": paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="append")
    args = parser.parse_args()
    entries = {s["exe_sha256"]: s["entries"]
               for s in read_json(ROOT / "reports/syscall_dispatch.json")["samples"]}
    rows = [inspect(s, entries[s["exe_sha256"]])
            for s in read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
            if not args.sample or s["sample"] in args.sample]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    target = EVIDENCE / "20261111_original_feature_paths.json"
    write_json(target, {"timestamp": timestamp(), "samples": rows,
                        "original_files_modified": False, "device_accessed": False})
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
