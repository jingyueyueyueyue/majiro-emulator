"""Separate archive layouts, executable builds and recovered runtime profiles.

Searches only local inputs; never downloads or runs games. V1 patch archives,
toolchain programs and synthetic fixtures cannot satisfy early-engine coverage.
The Android registry contains metadata only, keyed by original EXE SHA-256.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_arc import Archive
from port_common import ROOT, archive_dir, timestamp, write_json
from compare_majiro_samples import reference_exe
from recover_numeric_profiles import lua_value


def fingerprint(values):
    return hashlib.sha256("\n".join(sorted(set(values))).encode("utf8")).hexdigest()


def archive_role(name):
    stem = Path(name).stem.casefold()
    if stem.startswith(("update", "patch")):
        return "overlay_by_filename"
    if re.fullmatch(r"(?:data|scenario|fastdata|stream|voice)\d*", stem):
        return "base_resource_by_filename"
    return "unclassified"


def archive_probe(path):
    with path.open("rb") as stream:
        header = stream.read(28)
    match = re.fullmatch(rb"MajiroArcV([123])\.000\0", header[:16])
    if not match:
        return None
    row = {"path": str(path.resolve()), "name": path.name,
           "version": int(match[1]), "bytes": path.stat().st_size,
           "role": archive_role(path.name)}
    try:
        arc = Archive(path)
        row.update(index_status="parsed", index_sha256=arc.index_sha256,
                   members=len(arc.members),
                   mjo_members=sum(m.extension == ".mjo" for m in arc.members))
        scripts = Counter()
        for member in arc.members:
            if member.extension == ".mjo":
                with path.open("rb") as stream:
                    stream.seek(member.offset)
                    scripts[stream.read(16).rstrip(b"\0").decode("ascii", "replace")] += 1
        row["script_signatures"] = dict(scripts)
    except (ValueError, UnicodeError, OSError) as error:
        row.update(index_status="failed", error=str(error))
    return row


def executable_probe(path, recovered):
    import pefile

    data = path.read_bytes()
    if b"Majiro" not in data:
        return None
    row = {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
           "bytes": len(data),
           "signatures": sorted({s.decode("ascii") for s in re.findall(
               rb"Majiro(?:ArcV[123]\.000|Obj[XV]1\.000|SavV1\.\d+|SysV1\.\d+)", data)})}
    try:
        pe = pefile.PE(data=data, fast_load=True)
        row.update(machine=f"{pe.FILE_HEADER.Machine:04x}",
                   sections=[s.Name.rstrip(b"\0").decode("ascii", "replace") for s in pe.sections])
    except pefile.PEFormatError as error:
        row.update(role="invalid_pe", error=str(error))
        return row
    dispatch = recovered.get(row["sha256"])
    if dispatch:
        row.update(role="known_game_engine", sample=dispatch["sample"],
                   opcode_count=dispatch["active_opcode_count"],
                   opcode_dispatch_recovered=True)
    elif b"MajiroObjScripter" in data:
        row.update(role="script_editor_tool", opcode_dispatch_recovered=False)
    elif not any(s.startswith("MajiroObj") for s in row["signatures"]):
        row.update(role="archive_or_other_tool_candidate", opcode_dispatch_recovered=False)
    else:
        row.update(role="unclassified_majiro_executable", opcode_dispatch_recovered=False)
    return row


def discover(roots):
    present = [p.resolve() for p in roots if p.is_dir()]
    if not present:
        return [], []
    # rg supplies local filename discovery; only 28-byte matching headers proceed
    # to index parsing. Player-generated archives and saved data are excluded.
    command = ["rg", "--files", "--hidden", "-g", "*.arc",
               "-g", "!archive/**", "-g", "!savedata/**", "-g", "!save_chs/**",
               "-g", "!node_modules/**", "-g", "!.git/**", *map(str, present)]
    result = subprocess.run(command, capture_output=True, encoding="utf8")
    if result.returncode not in (0, 1):
        raise RuntimeError("local archive discovery failed: " + result.stderr[:1000])
    paths = []
    for line in result.stdout.splitlines():
        path = Path(line).resolve()
        if path.name.casefold() == "savedata.arc":
            continue
        try:
            relative = path.relative_to(ROOT).parts
        except ValueError:
            relative = ()
        if relative and relative[0].casefold() in {
                "archive", "resources", "references", "runtime", "player", "projects"}:
            continue
        paths.append(path)
    return sorted(set(paths)), list(map(str, present))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-root", type=Path, action="append",
                        help="Additional local root to inspect without extracting or running games.")
    options = parser.parse_args()
    config = json.loads((ROOT / "config/player_samples.json").read_text(encoding="utf8"))
    inventory = json.loads((ROOT / "reports/majiro_samples.json").read_text(encoding="utf8"))
    opcodes = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf8"))
    syscalls = json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf8"))
    profiles = json.loads((ROOT / "config/player_engine_profiles.json").read_text(encoding="utf8"))
    new_manifest = ROOT / "config/player_new_samples.json"
    if new_manifest.exists():
        config["games"] += json.loads(new_manifest.read_text(encoding="utf8"))["games"]
    new_scan = ROOT / "reports/new_sample_scan.json"
    if new_scan.exists():
        additional = json.loads(new_scan.read_text(encoding="utf8"))
        inventory["games"] += additional["games"]
        opcodes["samples"] += additional["opcode_samples"]
        syscalls["samples"] += additional["syscall_samples"]
    by_hash = {row["exe_sha256"]: row for row in opcodes["samples"]}
    selectors = {row["exe_sha256"]: row for row in syscalls["samples"]}
    roots = [Path(game["directory"]) for game in config["games"]]
    roots += options.search_root or [
        Path("D:/mjdev"), Path("D:/Games"), Path("D:/Downloads"),
        Path("D:/BaiduNetdiskDownload"), Path("C:/Users/jingyue/Downloads"),
        Path("D:/SteamLibrary/steamapps/common"), Path("D:/迅雷下载")]
    paths, searched = discover(roots)
    grouped = defaultdict(list)
    for path in paths:
        row = archive_probe(path)
        if row:
            grouped[path.parent].append(row)
    local_samples = []
    for directory, archives in sorted(grouped.items()):
        exes = list(directory.glob("*.exe")) + list((directory / "bin").glob("*.exe"))
        executable_rows = []
        for path in sorted(set(exes)):
            if path.stat().st_size <= 128 * 1024 * 1024:
                row = executable_probe(path, by_hash)
                if row:
                    executable_rows.append(row)
        base_versions = sorted({r["version"] for r in archives
                                if r["role"] == "base_resource_by_filename"})
        v1_base = any(r["version"] == 1 and r["role"] == "base_resource_by_filename"
                      and r["index_status"] == "parsed" for r in archives)
        engine_rows = [e for e in executable_rows if e["role"] == "known_game_engine"]
        early_dispatch = v1_base and bool(engine_rows)
        # Co-location is a candidate relationship, never proof of matching
        # vendor build. Recovered opcode/ABI behavior is separate evidence.
        local_samples.append({
            "directory": str(directory), "base_archive_versions": base_versions,
            "archives": archives, "executables": executable_rows,
            "v1_base_candidate": v1_base,
            "matching_early_engine_dispatch_recovered": early_dispatch,
            "full_early_runtime_verified": False,
            "known_engine_builds": len(engine_rows),
        })
    builds = {}
    for game in inventory["games"]:
        engine = reference_exe(game)
        sha = engine["sha256"]
        dispatch = by_hash[sha]
        label = dispatch["sample"]
        base_versions = sorted({a["version"] for a in game["archives"]
                                if archive_role(a["name"]) == "base_resource_by_filename"})
        selector = selectors[sha]
        selector_hashes = [e["hash"] for e in selector["entries"]]
        profile = profiles["executables"].get(sha)
        builds[sha] = {
            "sample": label, "base_archive_versions": base_versions,
            "overlay_archive_versions": sorted({a["version"] for a in game["archives"]
                                               if archive_role(a["name"]) == "overlay_by_filename"}),
            "script_signatures": sorted(game["script_signatures"]),
            "opcode_count": dispatch["active_opcode_count"],
            "opcode_set_sha256": fingerprint(e["opcode"] for e in dispatch["active_opcodes"]),
            "syscall_entries": selector["recovered_entries"],
            "syscall_set_sha256": fingerprint(selector_hashes),
            "save_signatures": sorted(s for s in engine["signatures"]
                                      if s.startswith(("MajiroSav", "MajiroSys"))),
            "numeric": profile["numeric"] if profile else "profile_recovery_required",
            "text_controls": profile["text_controls"] if profile else {},
            "behavior_profile_available": profile is not None,
            "fetch_layout": dispatch.get("fetch_layout", "inline_signed_word"),
            "exe_container_sha256": engine.get("container_sha256"),
            "thread_model": "per_build_semantics_require_separate_validation",
            "full_runtime_verified": False,
        }
    sources_file = ROOT / "reports/engine_generation_sources.json"
    sources = json.loads(sources_file.read_text(encoding="utf8")) if sources_file.exists() else {}
    candidates = [s["directory"] for s in local_samples if s["v1_base_candidate"]]
    early_dispatch = [s["directory"] for s in local_samples if s["matching_early_engine_dispatch_recovered"]]
    report = {
        "schema": 1, "timestamp": timestamp(), "primary_target": "android-arm64",
        "windows_role": "original_behavior_reference_and_tests",
        "searched_roots": searched, "matching_majiro_archives": sum(len(s["archives"]) for s in local_samples),
        "local_samples": local_samples, "engine_builds": builds,
        "v1_base_candidates": candidates, "early_engine_dispatch_samples": early_dispatch,
        "fully_verified_early_engine_samples": [],
        "early_engine_status": "base_and_executable_dispatch_recovered_runtime_pending"
        if early_dispatch else "missing_genuine_base_and_matching_executable",
        "archive_v1_overlay_does_not_imply_early_engine": True,
        "external_sources": sources,
        "notes": [
            "ARC layout, MJO signature, executable build, text behavior and thread semantics are separate axes.",
            "Filename role is explicitly heuristic. Directory co-location alone cannot establish vendor provenance.",
            "LunaHook Majiro/Majiro2/Majiro3/Majiro2Ex labels are text hook patterns, not ARC version declarations.",
            "No games were downloaded or executed; archive indexes and executable metadata were read only.",
            "MJO parsing and recovered dispatch sets do not imply complete Android runtime compatibility.",
        ],
    }
    write_json(ROOT / "reports/engine_generations.json", report)
    output = ROOT / "player/runtime/majiro_engine_generations.lua"
    output.write_text("-- Generated by recover_engine_generations.py; independent version axes.\n"
                      + "return " + lua_value(builds) + "\n", encoding="utf8")
    folder = archive_dir("engine_generation_discovery")
    write_json(folder / "20261110_engine_generations.json", report)
    print("Known EXE builds:", len(builds), "local Majiro archives:", report["matching_majiro_archives"])
    print("V1 base candidates:", len(candidates), "early dispatch samples:", len(early_dispatch),
          "fully verified early engines: 0")


if __name__ == "__main__":
    main()
