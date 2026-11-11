"""Read-only complete archive/MJO/EXE dispatch scan for newly supplied games."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json
from majiro_mjo import opcode_table
from scan_majiro_samples import scan
from compare_majiro_samples import reference_exe
from recover_majiro_dispatch import recover as recover_opcodes
from recover_majiro_syscalls import recover as recover_syscalls
from audit_player_semantics import registered_providers, call_sites


def main():
    manifest_path = ROOT / "config/player_new_samples.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    old_ops = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf8"))
    old_calls = json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf8"))
    previous_opcodes = {r["opcode"] for s in old_ops["samples"] for r in s["active_opcodes"]}
    previous_hashes = {e["hash"] for s in old_calls["samples"] for e in s["entries"]}
    audit = json.loads((ROOT / "reports/player_semantics.json").read_text(encoding="utf8"))
    previous_signatures = {
        (row["hash"], signature["argc"], signature["opcode"])
        for row in audit["required_syscalls"] for signatures in row.get("signatures", {}).values()
        for signature in signatures
    }
    early_file = ROOT / "reports/early_engine_samples.json"
    early = json.loads(early_file.read_text(encoding="utf8")) if early_file.exists() else {}
    cached_games = {r["directory"]: r for r in early.get("games", [])}
    cached_opcodes = {r["exe_sha256"]: r for r in early.get("opcode_samples", [])}
    cached_syscalls = {r["exe_sha256"]: r for r in early.get("syscall_samples", [])}
    names = json.loads((ROOT / "references/majiro-py/known_hashes.json").read_text(encoding="utf8"))["syscalls"]
    lua, _, registered, system = registered_providers()
    available = set()
    for key in registered:
        if isinstance(key, (int, float)):
            available.add(f"{int(key):08x}")
        else:
            import zlib
            available.add(f"{zlib.crc32(key.encode('cp932')):08x}")
    available.update(hash_ for hash_, name in names.items() if name in registered)
    report = {
        "schema": 1, "timestamp": timestamp(), "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "games": [], "opcode_samples": [], "syscall_samples": [], "differences": [],
        "errors": [], "new_providers": [], "gameplay_executed": False,
        "android_device_accessed": False, "source_game_files_modified": False,
        "compatibility_verified": False,
        "previous_dispatch_union": len(previous_hashes),
        "call_sites": {},
    }
    for item in manifest["games"]:
        root = Path(item["directory"])
        if not any(root.glob("*.arc")):
            raise ValueError("manifest must name the real game root: " + str(root))
        game = scan(root, opcode_table(), True)
        report["games"].append(game)
        sites = call_sites(game, opcode_table())
        report["call_sites"][item["id"]] = sites
        try:
            engine = reference_exe(game)
        except Exception as error:
            report["errors"].append({"sample": item["id"], "stage": "reference_exe", "error": str(error)})
            continue
        sha = engine["sha256"]
        opcodes, syscalls = None, None
        try:
            opcodes = cached_opcodes.get(sha)
            if opcodes is None or opcodes.get("unknown_active_opcodes"):
                opcodes = recover_opcodes(engine["path"], opcode_table())
            opcodes["sample"] = item["id"]
            report["opcode_samples"].append(opcodes)
        except Exception as error:
            report["errors"].append({"sample": item["id"], "stage": "opcode_dispatch", "error": str(error)})
        try:
            syscalls = cached_syscalls.get(sha) or recover_syscalls(game, names)
            syscalls["sample"] = item["id"]
            report["syscall_samples"].append(syscalls)
        except Exception as error:
            report["errors"].append({"sample": item["id"], "stage": "syscall_dispatch", "error": str(error)})
        difference = {
            "sample": item["id"], "directory": str(root), "exe_sha256": sha,
            "archive_versions": game["archive_versions"], "script_members": len(game["scripts"]),
            "parsed_scripts": game["parsed_scripts"], "failed_scripts": game["failed_scripts"],
            "save_signatures": [s for s in engine["signatures"] if s.startswith(("MajiroSav", "MajiroSys"))],
            "new_script_hashes": sorted(set(game["syscalls"]) - previous_hashes),
            "script_hashes_without_provider": sorted(set(game["syscalls"]) - available),
            "new_call_signatures": [row for row in sites["signatures"]
                                    if (row["hash"], row["argc"], row["opcode"]) not in previous_signatures],
        }
        if opcodes:
            active = {r["opcode"] for r in opcodes["active_opcodes"]}
            difference.update(
                valid_opcodes=opcodes["active_opcode_count"],
                new_opcodes=sorted(active - previous_opcodes),
                previous_opcodes_absent=sorted(previous_opcodes - active),
                opcode_recovery_status=opcodes["recovery_status"],
                fetch_layout=opcodes.get("fetch_layout", "inline_signed_word"))
        if syscalls:
            difference.update(
                reachable_syscalls=syscalls["recovered_entries"],
                unnamed_syscalls=syscalls["unnamed_entries"],
                syscall_recovery_status=syscalls["status"],
                new_dispatch_hashes=[e for e in syscalls["entries"] if e["hash"] not in previous_hashes],
                unresolved_script_hashes=syscalls["script_calls_without_recovered_entry"])
            difference["dispatch_hashes_without_provider"] = sorted(
                e["hash"] for e in syscalls["entries"] if e["hash"] not in available)
            difference["unnamed_dispatch_entries"] = [
                e["hash"] for e in syscalls["entries"] if e["name"] is None]
        if engine.get("container_path"):
            difference["embedded_engine"] = {key: engine[key] for key in
                ("container_path", "container_sha256", "embedded_offset", "embedded_size")}
        report["differences"].append(difference)
        write_json(ROOT / "reports/new_sample_scan.json", report)
        print(item["id"], "ARC", game["archive_versions"], "MJO", f'{game["parsed_scripts"]}/{len(game["scripts"])}',
              "opcodes", difference.get("valid_opcodes"), "syscalls", difference.get("reachable_syscalls"),
              "new", len(difference.get("new_dispatch_hashes", [])), flush=True)
    rows = ["# Newly supplied Majiro build scan", "", "Date: 2026-11-10. Android is the delivery target; no device tests were run.",
            "", "| Sample | ARC | MJO parsed | Valid opcodes | Reachable syscalls | New hashes |",
            "| --- | --- | ---: | ---: | ---: | ---: |"]
    for d in report["differences"]:
        rows.append(f'| {d["sample"]} | {d["archive_versions"]} | {d["parsed_scripts"]}/{d["script_members"]} | '
                    f'{d.get("valid_opcodes","unrecovered")} | {d.get("reachable_syscalls","unrecovered")} | '
                    f'{len(d.get("new_dispatch_hashes",[]))} |')
    rows += ["", "Archive parsing, selector recovery and handler semantics remain separate evidence.",
             "No missing implementation is replaced by a zero/success fallback.", ""]
    for d in report["differences"]:
        rows += [f'## {d["sample"]}', "",
                 f'Original EXE SHA-256: `{d["exe_sha256"]}`.',
                 f'Save signatures: {", ".join(d["save_signatures"])}.',
                 f'Unnamed entries: {d.get("unnamed_syscalls", "unrecovered")}; '
                 f'all dispatch entries without a runtime provider: {len(d.get("dispatch_hashes_without_provider", []))}; '
                 f'new script argument/return forms: {len(d["new_call_signatures"])}.',
                 f'Previous opcode numbers absent here: {d.get("previous_opcodes_absent", [])}.', ""]
        if d.get("embedded_engine"):
            e = d["embedded_engine"]
            rows += [f'The original engine is a complete embedded PE at file offset {e["embedded_offset"]}, '
                     f'{e["embedded_size"]} bytes, in container SHA-256 `{e["container_sha256"]}`.',
                     "Only that PE payload was copied to dated analysis evidence; source files were untouched.", ""]
        if d["new_call_signatures"]:
            rows += ["Newly observed forms require their own ABI/behavior checks:", ""]
            rows += [f'- `{s["hash"]}`: {s["argc"]} arguments, `{s["opcode"]}` ({s["count"]} calls).'
                     for s in d["new_call_signatures"]]
            rows += [""]
    if report["errors"]:
        rows += ["Unresolved scan stages:", ""] + [
            f'- {e["sample"]}: {e["stage"]}: {e["error"]}' for e in report["errors"]]
    (ROOT / "reports/new_sample_scan.md").write_text("\n".join(rows) + "\n", encoding="utf8")
    folder = archive_dir("new_sample_scan")
    write_json(folder / "20261110_new_sample_scan.json", report)
    print("Evidence:", folder)


if __name__ == "__main__":
    main()
