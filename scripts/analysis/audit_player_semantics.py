"""Inventory real runtime providers and every sample call site before gameplay.

Registration, executable entry recovery and behavior comparison are distinct
evidence. An untested provider never becomes verified merely by being present.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

from lua_bridge import create_lua
from player_paths import PLAYER,player_path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_arc import Archive
from majiro_mjo import Script, opcode_table
from port_common import ROOT, timestamp, write_json


_module_bytecode={}
_bytecode_compiler=None


def module_bytecode(name):
    """Cache compilation only; every fixture still gets fresh Lua state."""
    global _bytecode_compiler
    path=player_path(name)
    info=path.stat()
    identity=(info.st_mtime_ns,info.st_size,info.st_ino)
    cached=_module_bytecode.get(name)
    if cached and cached[0]==identity:
        return cached[1]
    if _bytecode_compiler is None:
        from lupa.lua51 import LuaRuntime
        compiler=LuaRuntime(encoding=None)
        _bytecode_compiler=compiler.eval(
            b"function(text,name)return string.dump(assert(loadstring(text,'@'..name)))end")
    value=_bytecode_compiler(path.read_bytes(),str(path.relative_to(ROOT)).replace("\\","/").encode("utf-8"))
    _module_bytecode[name]=(identity,value)
    return value


def source_identity():
    paths = sorted({*ROOT.glob("player/**/*.lua"), *ROOT.glob("player/**/*.cpp"),
                    *ROOT.glob("player/**/*.hpp"), *ROOT.glob("player/**/*.inc"),
                    *ROOT.glob("player/**/*.java"),*ROOT.glob("player/android/**/*.xml"),
                    ROOT / "scripts/analysis/audit_player_semantics.py",
                    ROOT / "scripts/analysis/player_paths.py"})
    hashes = {str(p.relative_to(ROOT)).replace("\\", "/"):
              hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("utf-8")).hexdigest()
    return {"sha256": identity, "files": hashes}


def registered_providers(minmax_arguments=None, string_case=None, integer_parse=None,
                         empty_search_absent=None, overrides=None):
    lua = create_lua()
    load = lua.eval("function(text,name)return assert(loadstring(text,'@'..name))()end")

    def module(name):
        return load(module_bytecode(name),str(player_path(name).relative_to(ROOT)).replace("\\","/"))

    names = module("resources/scripts/syscall_names.lua")
    options = lua.table_from({
        "names": names, "width": 1280, "height": 720, "modules": module,
        "source_identity": "registration-audit",
        "minmax_arguments": minmax_arguments,
        "string_case": string_case,
        "integer_parse": integer_parse, "empty_search_absent": empty_search_absent,
    })
    for key in ("config_files", "config_lines", "environment", "resource_names",
                "image_metadata", "movies"):
        options[key] = lua.table()
    options["now"] = lambda: 0
    options["cp932"] = lambda text: text.encode("cp932")

    def forbidden(*args):
        raise RuntimeError("registration audit unexpectedly executed a host operation")

    for key in ("graphics", "audio", "movie", "asset", "media_asset",
                "font_dialog", "screen_mode"):
        options[key] = forbidden
    for key,value in(overrides or{}).items():
        options[key]=lua.table_from(value,recursive=True)if isinstance(value,(dict,list))else value
    system = module("runtime/majiro_syscalls.lua")["new"](options)
    info = lua.eval("""
    function(fn)
      local d=debug.getinfo(fn,'S')
      return d.source,d.linedefined,d.lastlinedefined
    end
    """)
    result = {}
    for key, fn in system["providers"].items():
        source, first, last = info(fn)
        result[key] = {"key": key, "source": source.lstrip("@"),
                       "first_line": first, "last_line": last}
    # Keep the Lua runtime alive for its table references.
    return lua, names, result, system


def call_sites(game, definitions):
    signatures = {}
    errors = []
    parsed = 0
    for arc in game["archives"]:
        path = Path(game["directory"]) / arc["name"]
        archive = Archive(path)
        if archive.index_sha256 != arc["index_sha256"]:
            raise ValueError(f"sample archive index changed: {path}")
        for member in archive.members:
            if member.extension != ".mjo":
                continue
            try:
                script = Script(member.name, member.read(), opcodes=definitions)
                parsed += 1
            except (ValueError, UnicodeError) as error:
                errors.append({"archive": path.name, "script": member.name, "error": str(error)})
                continue
            for ins in script.instructions:
                if ins.name not in ("syscall", "syscallp"):
                    continue
                hash_, argc = ins.operands
                key = f"{hash_:08x}/{argc}/{ins.name}"
                row = signatures.setdefault(key, {
                    "hash": f"{hash_:08x}", "argc": argc, "opcode": ins.name,
                    "count": 0, "examples": [],
                })
                row["count"] += 1
                if len(row["examples"]) < 4:
                    row["examples"].append({
                        "archive": path.name, "script": member.name,
                        "script_sha256": script.sha256, "offset": ins.offset,
                    })
    return {"parsed_members": parsed, "errors": errors,
            "signatures": [signatures[key] for key in sorted(signatures)]}


def numeric_evidence(report, sample, opcode, vm_hash, identity):
    if report.get("vm_sha256") != vm_hash:
        return None
    if report.get("source_changed_during_test"):
        return None
    if report.get("source_identity") and report["source_identity"] != identity:
        return None
    if any(hashlib.sha256((ROOT / "scripts/analysis" / name).read_bytes()).hexdigest() != hash_
           for name, hash_ in report.get("test_sources", {}).items()):
        return None
    row = next((row for row in report.get("samples", [])
                if row["sample"] == sample["sample"]
                and row["exe_sha256"] == sample["exe_sha256"]), None)
    if row and opcode["name"] in row.get("passed_opcodes", []):
        return {"kind": "original_differential", "cases": row["cases"][opcode["name"]],
                "reference_exe_sha256": row["exe_sha256"],
                "report": "reports/numeric_semantics_tests.json",
                "scope": report["scope"], "universal_proof": False}
    return None


def scalar_evidence(report, sample, entry, identity):
    if report.get("source_identity") != identity or report.get("source_changed_during_test"):
        return None
    row = next((row for row in report.get("samples", [])
                if row["sample"] == sample["sample"]
                and row["exe_sha256"] == sample["exe_sha256"]), None)
    if not row:
        return None
    call = next((call for call in row["calls"] if call["hash"] == entry["hash"]), None)
    if not call or call["status"] != "passed":
        return None
    if any(hashlib.sha256((ROOT / "scripts/analysis" / name).read_bytes()).hexdigest() != hash_
           for name, hash_ in report.get("test_sources", {}).items()):
        return None
    return {"kind": "original_differential", "cases": call["cases"],
            "argument_counts": call["argument_counts"],
            "discard_modes": call["discard_modes"], "sample": sample["sample"],
            "reference_exe_sha256": sample["exe_sha256"],
            "scope": report["scope"], "report": "reports/scalar_syscall_tests.json",
            "universal_proof": False}


def audit(scan_calls=True):
    identity = source_identity()
    inventory = json.loads((ROOT / "reports/majiro_samples.json").read_text(encoding="utf-8"))
    sysdispatch = json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf-8"))
    opdispatch = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))
    lua, names, providers, _ = registered_providers()
    definitions = opcode_table()
    by_name = {game.get("id") or Path(game["directory"]).name: game for game in inventory["games"]}
    extra_scan=ROOT/"reports/new_sample_scan.json"
    if extra_scan.exists():
        extra=json.loads(extra_scan.read_text(encoding="utf8"))
        directories={game["directory"]:game for game in extra["games"]}
        for game in extra["games"]:
            by_name[game.get("id") or Path(game["directory"]).name] = game
        for row in extra.get("differences", []):
            by_name[row["sample"]]=directories[row["directory"]]
        for target,key in ((sysdispatch,"syscall_samples"),(opdispatch,"opcode_samples")):
            existing={row["exe_sha256"]for row in target["samples"]}
            target["samples"].extend(row for row in extra[key]if row["exe_sha256"]not in existing)
    opcode_samples = {row["sample"]: row for row in opdispatch["samples"]}
    required = {}
    samples = []
    vm_hash = identity["files"]["player/runtime/majiro_vm.lua"]
    numeric_path = ROOT / "reports/numeric_semantics_tests.json"
    numeric = json.loads(numeric_path.read_text(encoding="utf-8")) if numeric_path.exists() else {}
    storage_path = ROOT / "reports/storage_semantics_tests.json"
    storage = json.loads(storage_path.read_text(encoding="utf-8")) if storage_path.exists() else {}
    load_path = ROOT / "reports/load_semantics_tests.json"
    load = json.loads(load_path.read_text(encoding="utf-8")) if load_path.exists() else {}
    scalar_path = ROOT / "reports/scalar_syscall_tests.json"
    scalar = json.loads(scalar_path.read_text(encoding="utf-8")) if scalar_path.exists() else {}
    extra_reports={}
    for name in ("control_semantics_tests","case_semantics_tests","return_semantics_tests",
                 "string_semantics_tests","reference_storage_tests","array_syscall_tests",
                 "local_initialization_tests","argument_semantics_tests","call_semantics_tests",
                 "event_syscall_tests","configuration_query_tests","ultra_eligibility_tests",
                 "music_request_tests","extsound_semantics_tests","text_semantics_tests",
                 "voice_settings_tests","voice_control_tests","screen_semantics_tests",
                 "audio_remaining_tests","text_remaining_tests","graphics_remaining_tests",
                 "execution_remaining_tests"):
        path=ROOT/"reports"/(name+".json")
        extra_reports[name]=json.loads(path.read_text(encoding="utf-8"))if path.exists()else{}
    call_catalogue = {"schema": 1, "timestamp": timestamp(), "samples": []}
    for dispatch in sysdispatch["samples"]:
        label = dispatch["sample"]
        game = by_name[label]
        calls = call_sites(game, definitions) if scan_calls else None
        if calls:
            calls["sample"] = label
            call_catalogue["samples"].append(calls)
        missing_used, missing_all, present = [], [], 0
        evidence_ops = []
        signatures = defaultdict(list)
        if calls:
            for signature in calls["signatures"]:
                signatures[signature["hash"]].append(signature)
        for entry in dispatch["entries"]:
            hash_ = entry["hash"]
            number = int(hash_, 16)
            name = names[number]
            implementation = providers.get(number) or providers.get(name)
            item = required.setdefault(hash_, {
                "hash": hash_, "name": name or entry.get("name"),
                "implementation": implementation, "samples": [], "used_samples": [],
                "signatures": {}, "semantic_status": "pending_original_validation"
                if implementation else "missing_implementation",
                "behavior_evidence": [],
            })
            item["samples"].append(label)
            evidence = scalar_evidence(scalar, dispatch, entry, identity)
            if not evidence:
                for report_name in ("string_semantics_tests","array_syscall_tests","event_syscall_tests",
                                    "configuration_query_tests","ultra_eligibility_tests",
                                    "music_request_tests","extsound_semantics_tests","text_semantics_tests",
                                    "voice_settings_tests","voice_control_tests","screen_semantics_tests",
                                    "audio_remaining_tests","text_remaining_tests","graphics_remaining_tests",
                                    "execution_remaining_tests"):
                    evidence=scalar_evidence(extra_reports[report_name],dispatch,entry,identity)
                    if evidence:
                        evidence["report"]="reports/"+report_name+".json"
                        break
            if evidence:
                item["behavior_evidence"].append(evidence)
            if hash_ in game["syscalls"]:
                item["used_samples"].append(label)
            if calls and hash_ in signatures:
                item["signatures"][label] = signatures[hash_]
            if implementation:
                present += 1
            else:
                missing_all.append(hash_)
                if hash_ in game["syscalls"]:
                    missing_used.append(hash_)
        recovered = opcode_samples[label]
        for opcode in recovered["active_opcodes"]:
            evidence = numeric_evidence(numeric, recovered, opcode, vm_hash, identity)
            if not evidence:
                evidence = numeric_evidence(storage, recovered, opcode, vm_hash, identity)
                if evidence:
                    evidence["report"] = "reports/storage_semantics_tests.json"
            if not evidence:
                evidence = numeric_evidence(load, recovered, opcode, vm_hash, identity)
                if evidence:
                    evidence["report"] = "reports/load_semantics_tests.json"
            if not evidence:
                for report_name,other in extra_reports.items():
                    evidence=numeric_evidence(other,recovered,opcode,vm_hash,identity)
                    if evidence:
                        evidence["report"]="reports/"+report_name+".json"
                        break
            if not evidence:
                evidence=numeric_evidence(scalar,recovered,opcode,vm_hash,identity)
                if evidence:evidence["report"]="reports/scalar_syscall_tests.json"
            evidence_ops.append({
                "opcode": opcode["opcode"], "name": opcode["name"],
                "semantic_status": "differential_cases_passed" if evidence else
                                   "pending_evidence_mapping_or_validation",
                "evidence": evidence,
            })
        samples.append({
            "sample": label, "exe_sha256": dispatch["exe_sha256"],
            "arc_versions": game["archive_versions"],
            "recovered_syscalls": len(dispatch["entries"]), "registered_syscalls": present,
            "missing_syscalls": missing_all, "missing_script_used_syscalls": missing_used,
            "script_used_syscalls": len(game["syscalls"]),
            "opcodes": evidence_ops,
            "call_signature_count": len(calls["signatures"]) if calls else None,
            "static_parse_errors": calls["errors"] if calls else [],
        })
    changed = identity != source_identity()
    errors = sum(len(row["static_parse_errors"]) for row in samples)
    missing = [row for row in required.values() if not row["implementation"]]
    for row in required.values():
        covered = {evidence["sample"] for evidence in row["behavior_evidence"]}
        if row["implementation"] and covered == set(row["samples"]):
            absent_arities = [];absent_discard_modes=[]
            for sample, signatures in row["signatures"].items():
                evidence = next(e for e in row["behavior_evidence"] if e["sample"] == sample)
                absent_arities += [signature["argc"] for signature in signatures
                                  if signature["argc"] not in evidence["argument_counts"]]
                absent_discard_modes += [signature["opcode"]for signature in signatures
                    if(signature["opcode"]=="syscallp")not in evidence["discard_modes"]]
            row["semantic_status"] = ("partial_original_validation" if absent_arities or absent_discard_modes else
                                      "differential_cases_passed")
    pending_calls = sum(row["semantic_status"] != "differential_cases_passed"
                        for row in required.values())
    pending_ops = sum(op["evidence"] is None for row in samples for op in row["opcodes"])
    text_report=extra_reports["text_semantics_tests"]
    pending_text=text_report.get("pending_features",["text_semantics_report_missing"])
    gate_passed = not (changed or errors or missing or pending_calls or pending_ops or pending_text)
    report = {
        "schema": 1, "timestamp": timestamp(), "source_identity": identity,
        "source_changed_during_audit": changed, "samples": samples,
        "required_syscalls": sorted(required.values(), key=lambda row: row["hash"]),
        "summary": {
            "samples": len(samples), "unique_recovered_syscalls": len(required),
            "registered_recovered_syscalls": len(required) - len(missing),
            "missing_recovered_syscalls": len(missing),
            "missing_script_used_syscalls": sum(bool(row["used_samples"]) for row in missing),
            "registered_provider_keys": len(providers),
            "script_members_scanned": sum(row["parsed_members"] for row in call_catalogue["samples"]),
            "static_parse_errors": errors,
            "syscalls_with_original_cases": sum(bool(row["behavior_evidence"]) for row in required.values()),
            "pending_syscall_semantics": pending_calls,
            "pending_opcode_build_pairs": pending_ops,
            "pending_text_features":pending_text,
        },
        "status": "inconclusive" if changed or errors else "passed" if gate_passed else "gaps_found",
        "semantic_gate": "passed" if gate_passed else "blocked",
        "device_testing_allowed_by_semantic_gate": gate_passed,
        "complete": gate_passed,
        "limits": [
            "All archive script members are scanned, including inactive overlays and all branch bodies.",
            "Registration is implementation evidence only; providers may have unsupported signatures or paths.",
            "Call argument counts are static signatures, not inferred runtime argument types.",
            "Recovered direct EXE entries do not prove absence of dynamically registered interfaces.",
            "Existing nonnumeric tests require explicit evidence mapping before they count toward this gate.",
            "No story branch is skipped or forced and no runtime fallback is installed by this audit.",
        ],
    }
    if scan_calls:
        write_json(ROOT / "reports/player_call_signatures.json", call_catalogue)
    write_json(ROOT / "reports/player_semantics.json", report)
    verdict = "语义验收通过，可进入实机宿主验收。" if gate_passed else "语义验收尚未完成，实机剧情测试已暂停。"
    lines = ["# Majiro 批量语义验证", "", f"更新时间：{report['timestamp']}", "",
             verdict + "注册数量不作为语义通过数量。", "",
             "| 样本 | EXE 调用入口 | 已注册实现 | 脚本使用但未实现 | 操作码已有对照证据 |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for row in samples:
        passed = sum(op["evidence"] is not None for op in row["opcodes"])
        lines.append(f"| {row['sample']} | {row['recovered_syscalls']} | "
                     f"{row['registered_syscalls']} | "
                     f"{len(row['missing_script_used_syscalls'])} | {passed}/163 |")
    lines += ["", "## 缺失实现", "",
              "| 哈希 | 名称 | 用到该接口的样本 |",
              "| --- | --- | --- |"]
    for row in missing:
        lines.append(f"| `{row['hash']}` | {row['name'] or '待恢复名称'} | "
                     f"{', '.join(row['used_samples']) or '样本脚本未使用；原 EXE 可分派'} |")
    lines += ["", "完整调用位置、参数数量和原脚本哈希见 `reports/player_call_signatures.json`。",
              "实现位置与逐构建证据见 `reports/player_semantics.json`。", ""]
    (ROOT / "reports/player_semantics.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    print("semantic gate:", report["semantic_gate"], "; no device tests were executed by this audit")
    return 0 if not changed and not errors else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-call-sites", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    result = audit(not args.skip_call_sites)
    gate = json.loads((ROOT / "reports/player_semantics.json").read_text(encoding="utf-8"))["semantic_gate"]
    raise SystemExit(1 if args.require_complete and gate != "passed" else result)
