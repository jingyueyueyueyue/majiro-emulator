"""Recover newly discovered local Majiro base-archive builds, without gameplay."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json
from majiro_mjo import opcode_table
from compare_majiro_samples import reference_exe
from scan_majiro_samples import scan
from recover_majiro_dispatch import recover as recover_opcodes
from recover_majiro_syscalls import recover as recover_syscalls


def main():
    discovery = json.loads((ROOT / "reports/engine_generations.json").read_text(encoding="utf8"))
    known_directories = {str(Path(row["directory"]).resolve()) for row in json.loads(
        (ROOT / "config/player_samples.json").read_text(encoding="utf8"))["games"]}
    candidates = [row for row in discovery["local_samples"]
                  if str(Path(row["directory"]).resolve()) not in known_directories
                  and row["base_archive_versions"]
                  and any(e["role"] == "unclassified_majiro_executable"
                          and any(s.startswith("MajiroSav") for s in e["signatures"])
                          for e in row["executables"])]
    definitions = opcode_table()
    names = json.loads((ROOT / "references/majiro-py/known_hashes.json").read_text(
        encoding="utf8"))["syscalls"]
    report = {"schema": 1, "timestamp": timestamp(), "games": [], "opcode_samples": [],
              "syscall_samples": [], "errors": [], "gameplay_executed": False,
              "windows_api_executed": False, "android_runtime_verified": False}
    for candidate in candidates:
        game = scan(Path(candidate["directory"]), definitions, True)
        report["games"].append(game)
        label = Path(game["directory"]).name
        try:
            engine = reference_exe(game)
            opcode = recover_opcodes(engine["path"], definitions)
            opcode["sample"] = label
            report["opcode_samples"].append(opcode)
        except Exception as error:
            report["errors"].append({"sample": label, "stage": "opcode_dispatch", "error": str(error)})
        try:
            report["syscall_samples"].append(recover_syscalls(game, names))
        except Exception as error:
            report["errors"].append({"sample": label, "stage": "syscall_dispatch", "error": str(error)})
        write_json(ROOT / "reports/early_engine_samples.json", report)
        print(label, "ARC", game["archive_versions"],
              "MJO", f'{game["parsed_scripts"]}/{len(game["scripts"])}',
              "new errors", [r for r in report["errors"] if r["sample"] == label], flush=True)
    folder = archive_dir("early_engine_recovery")
    write_json(folder / "20261110_early_engine_samples.json", report)
    print("Recovered", len(candidates), "additional local builds; errors", len(report["errors"]))


if __name__ == "__main__":
    main()
