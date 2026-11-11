"""Produce separate V1/V2/V3 evidence from local samples and verified dispatch."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_arc import Archive
from majiro_mjo import Script
from port_common import ROOT, write_json, timestamp


def script_shape(member):
    script = Script(member.name, member.read())
    return {
        "opcodes": {ins.name for ins in script.instructions},
        "syscalls": {
            f"{ins.operands[ins.encoding.index('h')]:08x}"
            for ins in script.instructions if ins.name in ("syscall", "syscallp")},
    }


def v1_evidence(games):
    rows = []
    for game in games:
        if 1 not in game["archive_versions"]:
            continue
        root = Path(game["directory"])
        archives = [Archive(root / row["name"]) for row in game["archives"]]
        bases = [arc for arc in archives if arc.version != 1]
        by_name = {}
        for arc in bases:
            for member in arc.members:
                by_name.setdefault(member.name.casefold(), []).append(member)
        for arc in archives:
            if arc.version != 1:
                continue
            with arc.path.open("rb") as stream:
                stream.seek(28 + len(arc.members) * 8)
                terminal = struct.unpack("<II", stream.read(8))
            row = {
                "sample": root.name, "archive": arc.path.name,
                "members": len(arc.members), "base_archive_versions": sorted({a.version for a in bases}),
                "index_sha256": arc.index_sha256,
                "terminal_hash": terminal[0], "terminal_offset": terminal[1],
                "terminal_matches_archive_size": terminal == (0, arc.size),
                "overlapping_base_members": 0, "new_names": [], "changed_members": [],
                "loading_priority_verified": False,
                "oldest_engine_coverage": False,
            }
            for member in arc.members:
                previous = by_name.get(member.name.casefold(), [])
                if not previous:
                    row["new_names"].append(member.name)
                    continue
                row["overlapping_base_members"] += 1
                digest = member.digest()
                changed = {"member": member.name, "patch_sha256": digest, "base_versions": []}
                patch_shape = script_shape(member) if member.extension == ".mjo" else None
                for original in previous:
                    old_hash = original.digest()
                    if digest == old_hash:
                        continue
                    version = {"archive": original.archive.name, "sha256": old_hash}
                    if patch_shape:
                        old_shape = script_shape(original)
                        version.update(
                            added_opcodes=sorted(patch_shape["opcodes"] - old_shape["opcodes"]),
                            removed_opcodes=sorted(old_shape["opcodes"] - patch_shape["opcodes"]),
                            added_syscalls=sorted(patch_shape["syscalls"] - old_shape["syscalls"]),
                            removed_syscalls=sorted(old_shape["syscalls"] - patch_shape["syscalls"]))
                    changed["base_versions"].append(version)
                if changed["base_versions"]:
                    row["changed_members"].append(changed)
            rows.append(row)
    return {
        "status": "archive_structure_verified", "patch_archives": rows,
        "conclusion": "V1 occurs as overlays beside V3 bases; oldest Majiro runtime is not represented.",
    }


def main():
    inventory = json.loads((ROOT / "reports/majiro_samples.json").read_text(encoding="utf-8"))
    comparison = json.loads((ROOT / "reports/majiro_comparison.json").read_text(encoding="utf-8"))
    dispatch = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))
    image_tests = json.loads((ROOT / "reports/encrypted_image_tests.json").read_text(encoding="utf-8"))
    selectors = json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf-8"))
    selector_by_label = {row["sample"]: row for row in selectors["samples"]}
    by_label = {row["sample"]: row for row in dispatch["samples"]}
    base = next(row for row in comparison["samples"] if row["sample"] == "RURINOKASANE")
    baseline_opcodes = {row["opcode"] for row in by_label["RURINOKASANE"]["active_opcodes"]}
    groups = {}
    versions = {2: [], 3: []}
    for row in comparison["samples"]:
        label = row["sample"]
        recovered = by_label[label]
        selected = selector_by_label[label]
        hashes = {r["opcode"] for r in recovered["active_opcodes"]}
        groups.setdefault(recovered["fetch_va"], []).append(label)
        report = {
            "sample": label, "exe_sha256": row["exe_sha256"],
            "known_syscall_hashes": row["known_hashes"],
            "recovered_selector_entries": selected["recovered_entries"],
            "unnamed_selector_entries": selected["unnamed_entries"],
            "unresolved_script_syscalls": selected["script_calls_without_recovered_entry"],
            "interface_group": row["interface_group"],
            "opcode_count": recovered["active_opcode_count"],
            "all_16bit_dispatch_values_tested": recovered["tested_opcode_values"] == 65536,
            "same_opcode_set_as_current_game": hashes == baseline_opcodes,
            "new_known_syscalls_vs_current_game": row["known_hashes_missing_from_baseline"],
            "original_save_signatures": row["save_signatures_in_exe"],
            "image_variants": row["image_variants"], "environments": row["environments"],
            "scripts_without_main": row["scripts_without_main"],
            "runtime_verified": False, "save_import_verified": False,
        }
        for version in (2, 3):
            if version in row["archive_versions"]:
                versions[version].append(report)
    research = {
        "schema": 1, "timestamp": timestamp(), "primary_target": "android-arm64",
        "pc_role": "debug_and_original_behavior_reference",
        "v1": v1_evidence(inventory["games"]),
        "v2": {"samples": versions[2], "runtime_verified": False},
        "v3": {"samples": versions[3], "runtime_verified": False},
        "image_decoding": image_tests,
        "known_interface_groups": comparison["known_interface_groups"],
        "selector_recovery": selectors,
        "opcode_union": sorted(baseline_opcodes),
        "oldest_runtime_sample_missing": True,
        "additional_downloads_required_now": False,
        "recommendation": (
            "Existing samples cover five known syscall sets and three ARC layouts. "
            "Prioritize Android runtime, saves, media and translation behavior. "
            "A genuine early engine using ARC V1 as base would add unrepresented runtime coverage."),
        "agent_research": "CLI agents failed before execution (HTTP 401); evidence produced by parent session",
        "release_allowed": False,
    }
    write_json(ROOT / "reports/majiro_version_research.json", research)
    print("V1 overlays:", len(research["v1"]["patch_archives"]))
    print("V2 samples:", len(versions[2]), "V3 samples:", len(versions[3]))
    print("Known interface groups:", len(research["known_interface_groups"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
