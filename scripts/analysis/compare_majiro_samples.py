"""Compare sampled Majiro interfaces without equating static sets with runtime ABI."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, timestamp, write_json


def reference_exe(game):
    candidates = [
        e for e in game.get("executables", [])
        if not e.get("boxed_patch_candidate")
        and not e.get("embedded_engine_container")
        and not any(tag in Path(e["path"]).stem.lower() for tag in ("_cn", "_chs"))
        and any(s.startswith("MajiroObj") for s in e.get("signatures", []))
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"{game['directory']}: expected one reference EXE; found {len(candidates)}")
    return candidates[0]


def compare(inventory, baseline="RURINOKASANE"):
    games = inventory["games"]
    base = next(g for g in games if Path(g["directory"]).name == baseline)
    baseline_exe = reference_exe(base)
    baseline_hashes = {r["hash"] for r in baseline_exe["known_hash_candidates"]}
    baseline_ops = set(base["opcodes"])
    groups = {}
    summaries = []
    for game in games:
        exe = reference_exe(game)
        hashes = {r["hash"] for r in exe["known_hash_candidates"]}
        names = {r["hash"]: r["name"] for r in exe["known_hash_candidates"]}
        signature = hashlib.sha256("\n".join(sorted(hashes)).encode("ascii")).hexdigest()
        group = groups.setdefault(signature, {
            "id": signature[:12], "known_hashes": len(hashes), "members": [],
            "meaning": "identical recognized hash set, not proof of identical handlers",
        })
        label = Path(game["directory"]).name
        group["members"].append(label)
        summaries.append({
            "sample": label, "directory": game["directory"],
            "reference_executable": exe["path"], "exe_sha256": exe["sha256"],
            "reference_provenance": "user_supplied; unmodified vendor provenance not established",
            "interface_group": signature[:12], "known_hashes": len(hashes),
            "archive_versions": game["archive_versions"],
            "archive_count": len(game["archives"]),
            "script_members": len(game["scripts"]),
            "parsed_scripts": game["parsed_scripts"],
            "failed_scripts": game["failed_scripts"],
            "script_instructions": sum(s.get("instructions", 0) for s in game["scripts"]),
            "script_opcode_variants": len(game["opcodes"]),
            "additional_script_opcodes": sorted(set(game["opcodes"]) - baseline_ops),
            "script_syscall_hashes": len(game["syscalls"]),
            "known_hashes_missing_from_baseline": [
                {"hash": h, "name": names[h]} for h in sorted(hashes - baseline_hashes)],
            "baseline_known_hashes_absent_here": sorted(baseline_hashes - hashes),
            "unknown_compare_candidates": len(exe.get("unknown_compare_candidates", [])),
            "save_signatures_in_exe": [
                s for s in exe["signatures"] if s.startswith(("MajiroSav", "MajiroSys"))],
            "image_variants": game.get("image_variants", {}),
            "environments": game.get("environments", []),
            "scripts_without_main": game.get("scripts_without_main", []),
            "movies": game.get("movies", []),
            "structural_status": game["structural_status"],
            "runtime_verified": False,
        })
    return {
        "schema": 1, "timestamp": timestamp(), "baseline": baseline,
        "samples": summaries,
        "known_interface_groups": sorted(groups.values(), key=lambda g: g["known_hashes"]),
        "limits": [
            "Counts include archive members before overlay resolution; updates may repeat scripts and images.",
            "Known hash groups exclude unnamed hashes and do not establish parameter or handler equivalence.",
            "Successful parsing does not establish playability, save import, or translation compatibility.",
        ],
        "sources_modified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "reports/majiro_samples.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/majiro_comparison.json")
    args = parser.parse_args()
    result = compare(json.loads(args.input.read_text(encoding="utf-8")))
    write_json(args.output, result)
    for sample in result["samples"]:
        print(f"{sample['sample']}: ARC {sample['archive_versions']}, "
              f"MJO {sample['parsed_scripts']}/{sample['script_members']}, "
              f"recognized hashes {sample['known_hashes']}, "
              f"images {sample['image_variants']}")
    print("Interface groups:", len(result["known_interface_groups"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
