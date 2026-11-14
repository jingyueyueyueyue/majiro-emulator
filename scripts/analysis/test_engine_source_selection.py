"""Test real per-EXE host grouping, dispatch ambiguity and runtime availability."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
from types import SimpleNamespace

import pefile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, read_json, sha256, timestamp, write_json
from lua_bridge import create_lua, table_value
from original_calls import CallOracle, OriginalDiagnostic

EVIDENCE = ROOT / "archive/2026-11/20261111_emulator_audit_profiles/20261111_phase3"


def source(root, name):
    candidate = root / name
    return candidate if candidate.exists() else ROOT / name


def java_method(text):
    first = text.index("    public static String[] engineFeatures()")
    start = text.index("{", first)
    depth = 1
    end = start + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[first:end]


def grouped(*groups):
    return [item for group in groups if group
            for item in ("engine-source:begin", *group, "engine-source:end")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", type=Path, default=ROOT,
                        help="Optional staged host/provider tree; report distinguishes it from formal validation.")
    args = parser.parse_args()
    host_root = args.host_root.resolve()
    folder = EVIDENCE / (timestamp().replace(":", "").replace("+", "_") + "_selection")
    folder.mkdir(parents=True, exist_ok=False)
    samples = read_json(ROOT / "reports/opcode_dispatch.json")["samples"]
    by_name = {s["sample"]: s for s in samples}
    entries = {s["sample"]: s["entries"] for s in read_json(ROOT / "reports/syscall_dispatch.json")["samples"]}
    java = Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"
    bridge = ROOT / "player/android/src/org/majiro/player/GameBridge.java"
    feature = ROOT / "player/android/src/org/majiro/player/EngineFeatures.java"
    monitored = [feature, bridge, ROOT / "player/runtime/majiro_engine.lua",
                 *(source(host_root, name) for name in (
                     "player/runtime/player_boot.lua", "player/runtime/majiro_settings.lua",
                     "player/runtime/majiro_text_resources.lua",
                     "scripts/analysis/production_engine_features.py",
                     "scripts/analysis/audit_startup_resources.py"))]
    before = {str(p): sha256(p) for p in monitored}
    fixture = folder / "FeatureSourcesChecks.java"
    fixture.write_text("""package org.majiro.player;
import java.io.*;import java.nio.file.*;import java.nio.charset.StandardCharsets;import java.util.*;
public final class FeatureSourcesChecks {
  private static final String rootId="ROOT";
  private static final HashMap<String,String> root=new HashMap<String,String>(),binFiles=new HashMap<String,String>();
  private static final HashMap<String,Path> paths=new HashMap<String,Path>();
  private static HashMap<String,String> children(String id){return id.equals(rootId)?root:binFiles;}
  private static Path resolve(String path){return paths.get(path);}
  private static final Context context=new Context();
  private static final class Context {
    Context getContentResolver(){return this;}
    InputStream openInputStream(Path path)throws Exception{return Files.newInputStream(path);}
  }
  private static final class DiagnosticLog {static void write(String a,String b){}}
""" + java_method(bridge.read_text("utf8")) + """
  public static void main(String[]args)throws Exception {
    for(String row:Files.readAllLines(Paths.get(args[0]),StandardCharsets.UTF_8)){
      root.clear();binFiles.clear();paths.clear();int i=0;
      if(!row.isEmpty())for(String part:row.split("\\t")){
        String name="source_"+(i++)+".exe";String kind=part.substring(0,1);
        Path path=Paths.get(part.substring(2));
        if(kind.equals("R")){root.put(name,name);paths.put(name,path);}
        else {root.put("bin","BIN");
          if(kind.equals("B")){binFiles.put(name,name);paths.put("bin/"+name,path);}
          else binFiles.put("nested","NESTED");
        }
      }
      System.out.println(String.join(",",engineFeatures()));
    }
  }
}
""", encoding="utf8")

    def command(name, arguments):
        result = subprocess.run(arguments, capture_output=True, timeout=120)
        (folder / ("20261111_" + name + ".log")).write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (name, result.stderr.decode("utf8", "replace"))
        return result.stdout.decode("utf8").splitlines()

    command("compile", [str(java / "javac.exe"), "-encoding", "UTF-8", "-d", str(folder),
                         str(feature), str(fixture), str(ROOT / "scripts/analysis/EngineFeaturesChecks.java")])
    listing = folder / "20261111_engines.txt"
    listing.write_text("\n".join(s["path"] for s in samples) + "\n", encoding="utf8")
    lines = command("single_features", [str(java / "java.exe"), "-cp", str(folder),
                    "org.majiro.player.EngineFeaturesChecks", str(listing)])
    found = {s["sample"]: line.split(",") if line else [] for s, line in zip(samples, lines)}
    assert len(found) == 17 and all(not any(f.startswith("engine:ambiguous-dispatch") for f in x)
                                  for x in found.values())
    lua = create_lua()
    engine = lua.execute((ROOT / "player/runtime/majiro_engine.lua").read_text("utf8"))

    def profile(features, archive=3):
        return table_value(engine["detect"](lua.table_from({
            "family": "Majiro", "archive_version": archive, "script_signature": "MajiroObjX1.000",
            "features": features}, recursive=True)))

    def behavior(value):
        return {k: v for k, v in value.items() if k not in ("generation", "recognition")}

    checks = []
    def check(name, value):
        assert value, name
        checks.append({"name": name, "status": "passed"})

    for name, features in found.items():
        check("grouped_single_" + name, profile(grouped(features)) == profile(features))
        check("grouped_duplicate_" + name, profile(grouped(features, features)) == profile(features))
    pair_count = 0
    rejected = 0
    for i, left in enumerate(samples):
        for right in samples[i + 1:]:
            a, b = found[left["sample"]], found[right["sample"]]
            same = behavior(profile(a)) == behavior(profile(b))
            for ordered in ((a, b), (b, a)):
                try:
                    got = profile(grouped(*ordered))
                except Exception as error:
                    assert not same and "conflicting Majiro engine" in str(error), error
                    rejected += 1
                else:
                    assert same and behavior(got) == behavior(profile(a))
                pair_count += 1
    check("all_source_pairs_agree_or_reject_in_both_orders", pair_count == 272)
    check("partial_helper_does_not_override_interpreter",
          profile(grouped(["save:105"], found["kizoku"])) == profile(found["kizoku"]))
    check("empty_executable_sources_fall_back", profile(grouped([])) == profile([]))
    wrapped = read_json(ROOT / "reports/happy_wrapper_recovery.json")
    host_inputs = [
        "R=" + by_name["kizoku"]["path"] + "\tB=" + by_name["RURINOKASANE"]["path"],
        "R=" + wrapped["original_container"] + "\tB=" + wrapped["raw_embedded_reference"] +
            "\tR=" + wrapped["initialized_reference"],
        "R=" + by_name["kizoku"]["path"] + "\tD=" + by_name["RURINOKASANE"]["path"],
        "",
    ]
    host_list = folder / "20261111_host_inputs.txt"
    host_list.write_text("\n".join(host_inputs) + "\n", encoding="utf8")
    host_lines = command("real_host_method", [str(java / "java.exe"), "-cp", str(folder),
                         "org.majiro.player.FeatureSourcesChecks", str(host_list)])
    host_features = [line.split(",") if line else [] for line in host_lines]
    check("real_host_keeps_two_sources", host_features[0].count("engine-source:begin") == 2)
    try:
        profile(host_features[0])
    except Exception as error:
        check("real_host_conflict_rejected", "conflicting Majiro engine" in str(error))
    else:
        raise AssertionError("real host built a mixed profile")
    check("wrapper_raw_initialized_behavior_agree",
          behavior(profile(host_features[1])) == behavior(profile(found["happy_princess"])))
    check("real_host_does_not_recurse_below_bin", profile(host_features[2]) == profile(found["kizoku"]))
    check("real_host_no_exe_fallback", profile(host_features[3]) == profile([]))

    sample = by_name["idol_revolution"]
    original = Path(sample["path"]).read_bytes()
    pe = pefile.PE(data=original)
    text = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
    assert text.SizeOfRawData - text.Misc_VirtualSize >= 11
    at = text.PointerToRawData + text.Misc_VirtualSize
    va = pe.OPTIONAL_HEADER.ImageBase + text.VirtualAddress + text.Misc_VirtualSize
    target = next(e["selected_va"] for e in entries[sample["sample"]] if e["hash"] == "efd49bc9")
    own_target = next(e["selected_va"] for e in entries[sample["sample"]] if e["hash"] == "f63ed9ae")
    mutants = []
    for name, public, destination, expand in (
        ("padding", 0xf63ed9ae, target, False),
        ("ambiguous_api", 0xf63ed9ae, target, True),
        ("ambiguous_engine", 0x45815f6d, target, True),
        ("duplicate_same_handler", 0xf63ed9ae, own_target, True)):
        data = bytearray(original)
        data[at:at + 11] = b"\x3d" + struct.pack("<I", public) + b"\x0f\x84" + struct.pack("<i", destination - va - 11)
        if expand:
            struct.pack_into("<I", data, text.get_file_offset() + 8, text.Misc_VirtualSize + 16)
        path = folder / ("20261111_" + name + ".exe")
        path.write_bytes(data)
        mutants.append(path)
    mutation_list = folder / "20261111_mutants.txt"
    mutation_list.write_text("\n".join(str(p) for p in mutants) + "\n", encoding="utf8")
    mutated = command("ambiguity", [str(java / "java.exe"), "-cp", str(folder),
                       "org.majiro.player.EngineFeaturesChecks", str(mutation_list)])
    changed = [line.split(",") for line in mutated]
    check("file_alignment_padding_is_not_dispatch", set(changed[0]) == set(found["idol_revolution"]))
    check("duplicate_same_handler_is_not_ambiguous", set(changed[3]) == set(found["idol_revolution"]))
    for index in (1, 2):
        expected_hash = "f63ed9ae" if index == 1 else "45815f6d"
        check("explicit_ambiguity_marker_" + str(index),
              "engine:ambiguous-dispatch:" + expected_hash in changed[index])
        try:
            profile(changed[index], 2)
        except Exception as error:
            check("ambiguity_does_not_become_absence_" + str(index),
                  "ambiguous Majiro syscall dispatch " + expected_hash in str(error))
        else:
            raise AssertionError("ambiguous dispatch became a generic profile")

    # Execute the actual PC helper, sharing the just-built Java fixture.
    pc_path = source(host_root, "scripts/analysis/production_engine_features.py")
    spec = importlib.util.spec_from_file_location("phase3_pc_features", pc_path)
    pc = importlib.util.module_from_spec(spec);spec.loader.exec_module(pc)
    pc._compiler = lambda identity: (folder, java / "java.exe")
    pc_grouped = pc.features([Path(by_name["kizoku"]["path"]), Path(by_name["RURINOKASANE"]["path"])])
    check("pc_host_keeps_two_sources", pc_grouped.count("engine-source:begin") == 2)
    try:
        profile(pc_grouped)
    except Exception as error:
        check("pc_host_rejects_same_conflict", "conflicting Majiro engine" in str(error))
    else:
        raise AssertionError("PC fixture hid host conflict")

    sys.modules["production_engine_features"] = pc
    startup_path = source(host_root, "scripts/analysis/audit_startup_resources.py")
    spec = importlib.util.spec_from_file_location("phase3_startup", startup_path)
    startup = importlib.util.module_from_spec(spec);spec.loader.exec_module(startup)
    obj = SimpleNamespace(files={"engine.exe": Path(by_name["kizoku"]["path"]),
                                "BIN/nested/helper.exe": Path(by_name["RURINOKASANE"]["path"])},
                          lua=lua, archive_rows=[("data.arc", SimpleNamespace(version=1))],
                          image_only_archives=set(),
                          resource=lambda name: SimpleNamespace(read=lambda: b"MajiroObjX1.000\0"))
    requirements = table_value(startup.Startup.engine_requirements(obj))
    check("pc_enumeration_matches_root_bin_only",
          requirements["features"].count("engine-source:begin") == 1)

    # Actual boot/configuration, with only expensive provider constructors captured.
    boot_lua = create_lua()
    stubs = {
        # Provider capture isolates configuration routing; upgrade recovery has
        # its own production VM/storage integration suite.
        "runtime/majiro_save_compatibility.lua": "return {new=function()return {recover=function()return {recovered=0,rejected=0,conflicts=0}end,accepts=function()return false end}end}",
        "runtime/majiro_store.lua": "return {new=function()return {load_index=function()return {}end}end}",
        "runtime/majiro_syscalls.lua": "return {new=function(o)BOOT_OPTIONS=o;return {state={}}end}",
        "runtime/majiro_vm.lua": "return {new=function(o)VM_OPTIONS=o;return {begin=function()end}end}",
        "scripts/opcodes.lua": "return {}", "scripts/syscall_names.lua": "return {}",
    }
    host = boot_lua.table()
    host["asset_text"] = lambda name: stubs.get(name) or source(host_root, "player/" + name).read_text("utf8")
    host["movies"] = lambda: boot_lua.table()
    host["identity"] = lambda: "phase3"
    host["member_names"] = lambda: boot_lua.table_from(["start.mjo", "fixture.env"])
    text_reads = set()
    text_state = {"sealed": False, "identity": None, "encoding": None}
    environment = "CLIENT_WIDTH 800\nCLIENT_HEIGHT 600\n"
    def fixture_script(name):
        assert name == "start.mjo"
        text_reads.add(name)
        boot_lua.globals()["PLAYER_VERIFY_TEXT_RESOURCE"](
            name, hashlib.sha256(b"source-selection-start").hexdigest(), "original", name)
        return boot_lua.table_from({"name": name, "code": [], "functions": [], "main": 0}, recursive=True)
    def fixture_resource_text(name):
        assert name == "fixture.env"
        text_reads.add(name)
        boot_lua.globals()["PLAYER_VERIFY_TEXT_RESOURCE"](
            name, hashlib.sha256(environment.encode()).hexdigest(), "original", name)
        return environment
    def seal_text_resources():
        assert text_reads == {"start.mjo", "fixture.env"}
        text_state["sealed"] = True
    def set_text_identity(identity, encoding):
        assert text_state["sealed"] and encoding == "cp932"
        assert identity == hashlib.sha256(b"verified-text-v2\nphase3\nvm:cp932").hexdigest()
        text_state.update(identity=identity, encoding=encoding)
    def text_storage(request):
        assert request.startswith("sha256\n"), "unexpected storage operation in boot capture fixture"
        return "OK " + hashlib.sha256(request[7:].encode("utf8")).hexdigest()
    host["text_resource_names"] = lambda: boot_lua.table_from(["start.mjo", "fixture.env"])
    host["script"] = fixture_script
    host["resource_text"] = fixture_resource_text
    host["seal_text_resources"] = seal_text_resources
    host["set_text_identity"] = set_text_identity
    host["storage"] = text_storage
    host["engine_requirements"] = lambda: boot_lua.table_from({
        "family": "Majiro", "archive_version": 1, "script_signature": "MajiroObjX1.000",
        "features": grouped(found["kizoku"])}, recursive=True)
    boot_lua.globals()["HOST"] = host
    boot_lua.execute(source(host_root, "player/runtime/player_boot.lua").read_text("utf8"))
    check("boot_preflights_and_seals_original_text_resources",
          text_state["sealed"] and text_state["encoding"] == "cp932" and
          boot_lua.globals()["BOOT_OPTIONS"]["source_identity"] == text_state["identity"])
    check("boot_forwards_false_availability", boot_lua.globals()["BOOT_OPTIONS"]["ultra_fast_mode_available"] is False)
    check("boot_forwards_legacy_array", boot_lua.globals()["VM_OPTIONS"]["array_3d_legacy_stride"] is True)
    # A restored VM keeps the same indexing semantics, and rejects a packet
    # made under another array/numeric profile before changing its live state.
    snapshot_lua = create_lua()
    snapshot_lua.globals()["VM"] = snapshot_lua.execute(
        (ROOT / "player/runtime/majiro_vm.lua").read_text("utf8"))
    snapshot_lua.globals()["OPCODES"] = snapshot_lua.execute(
        (ROOT / "player/tables/opcodes.lua").read_text("utf8"))
    restored, array_rejected, numeric_rejected = snapshot_lua.execute("""
      local function make(legacy,numeric)
        return VM.new{opcodes=OPCODES,syscall=function()end,
          loader=function()return {name='fixture.mjo',source_sha256='phase3',
            code={{0,0x191}},functions={},main=0}end,
          array_3d_legacy_stride=legacy,numeric_profile=numeric,case_string_relations=false}
      end
      local original=make(true,'x87');original:begin('fixture.mjo')
      local packet=original:export_state()
      local changed=make(false,'x87')
      local array_ok=pcall(changed.import_state,changed,packet)
      changed=make(true,'sse32')
      local numeric_ok=pcall(changed.import_state,changed,packet)
      local restored=make(true,'x87');restored:import_state(packet)
      return restored.array_3d_legacy_stride and not restored.case_string_relations,
        not array_ok,not numeric_ok
    """)
    check("snapshot_retains_vm_semantics", restored)
    check("snapshot_rejects_array_profile_change", array_rejected)
    check("snapshot_rejects_numeric_profile_change", numeric_rejected)

    def system_for(name):
        runtime = create_lua()
        def module(path):
            name = "player/" + path if path.startswith("runtime/") else "player/tables/" + Path(path).name
            return runtime.execute(source(host_root, name).read_text("utf8"))
        options = runtime.table_from(profile(found[name]), recursive=True)
        options["names"] = runtime.execute((ROOT / "player/tables/syscall_names.lua").read_text("utf8"))
        for key in ("config_files", "config_lines", "environment", "resource_names", "image_metadata", "movies"):
            options[key] = runtime.table()
        options["width"], options["height"], options["modules"], options["now"] = 800, 600, module, lambda: 0
        options["cp932"] = lambda text: text.encode("cp932")
        system = module("runtime/majiro_syscalls.lua")["new"](options)
        return runtime, system

    unavailable_hashes = (0xc102bcc3, 0x8fe5d6e7, 0x45acd4a7, 0x38721a26, 0xf3d276f1)
    absent_cases = []
    for name in ("kizoku", "reconquista"):
        original_vm = CallOracle(by_name[name])
        runtime, system = system_for(name)
        for public in unavailable_hashes:
            try:
                original_vm.execute("syscall", [], struct.pack("<IH", public, 0), budget=20000)
            except OriginalDiagnostic:
                pass
            else:
                raise AssertionError("original absent API did not diagnose")
            vm = runtime.eval("{status='running',fail=function(self,message)self.status='blocked';error(message,0)end}")
            try:
                system["invoke"](vm, public, runtime.table(), True)
            except Exception as error:
                assert vm["status"] == "blocked" and "unimplemented syscall" in str(error), error
            else:
                raise AssertionError("unsupported ultra provider was enabled")
            absent_cases.append({"sample": name, "hash": f"{public:08x}", "original": "undefined_builtin",
                                 "production": "blocked_missing_provider", "status": "passed"})
    runtime, system = system_for("BOKUTSUBA")
    result = system["providers"]["$set_ultra_fast_mode"](runtime.table(), runtime.table())
    check("available_ultra_still_changes_mode", result == 0 and system["state"]["settings"]["ultra_fast_mode"] is True)
    after = {str(p): sha256(p) for p in monitored}
    check("sources_stable", before == after)
    report = {"timestamp": timestamp(), "status": "passed", "checks": checks,
              "check_count": len(checks), "pair_order_cases": pair_count, "rejected_conflicting_pairs": rejected,
              "original_absent_api_cases": absent_cases, "single_engines": len(found),
              "host_scope": "formal" if host_root == ROOT else "staged_shared_host_files",
              "host_root": str(host_root), "source_sha256": after, "sources_stable": before == after,
              "gamebridge_method_sha256": __import__("hashlib").sha256(java_method(bridge.read_text("utf8")).encode()).hexdigest(),
              "evidence": str(folder.relative_to(ROOT)), "device_accessed": False,
              "original_game_modified": False, "full_apk_or_native_build": False}
    write_json(folder / "20261111_report.json", report)
    write_json(ROOT / "reports/engine_source_selection_tests.json", report)
    for path in mutants:
        path.unlink()
    print(f"Engine source selection: {len(checks)} checks, {pair_count} ordered source pairs, "
          f"{len(absent_cases)} original absent-API cases; scope={report['host_scope']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
