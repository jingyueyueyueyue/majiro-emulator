"""Isolated Java/native/Lua integration checks; no APK, devices or game writes."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import struct
import time

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parents[1] if ROOT.parent.name == "text-mode-restoration" else ROOT.parent
sys.path[:0] = [str(ROOT / "scripts/convert"), str(ROOT / "scripts/build")]
from port_common import lua_value
from majiro_mjo import opcode_table
from build_player_core import msvc_environment


def source_hashes():
    paths=list((ROOT/"player").rglob("*"))
    paths += [ROOT/"scripts/analysis"/name for name in
              ("run_text_resource_candidate.py","text_resource_probe.cpp","TextResourceChecks.java",
               "ArchiveLayerChecks.java","TextModeChecks.java","PatchManifestChecks.java","LibrarySelectionChecks.java")]
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def checked_main():
    """Legacy audit names share this suite; each invocation rechecks game bytes."""
    return main([])


def run(label, args, folder, *, env=None, expected=0, timeout=180):
    began=time.perf_counter()
    result = subprocess.run(list(map(str, args)), cwd=folder, env=env, capture_output=True, timeout=timeout)
    (folder / ("20261111_" + label + ".log")).write_bytes(result.stdout + result.stderr)
    (folder / ("20261111_" + label + ".timing.json")).write_text(
        json.dumps({"elapsed_seconds": round(time.perf_counter()-began, 4)}), encoding="ascii")
    if result.returncode != expected:
        raise RuntimeError(label + " failed; see " + str(folder / ("20261111_" + label + ".log")))
    return result.stdout.decode("utf8")


def native_build(formal, folder):
    host = (ROOT / "player/android/game_host.cpp").read_text(encoding="utf8")
    spans = [
        ("std::string key(", "std::vector<uint8_t> read_at("),
        ("bool read_patch(", "void rebuild_members("),
        ("void text_field(", "double numeric("),
        ("void verify_patch_member(const std::string& name,const std::vector<uint8_t>& raw){",
         "std::vector<uint8_t> parse_hex("),
        ("int resource_text(", "int script_file_read("),
        ("int script(lua_State*", "int graphics("),
    ]
    declarations = (
        "void verify_text_resource(const std::string&,const std::vector<uint8_t>&,const majiro::TextSource&);\n"
        "std::string decode_source(const std::vector<uint8_t>&,const majiro::TextSource&);\n"
    )
    selected = declarations + "\n".join(host[host.index(a):host.index(b, host.index(a))] for a, b in spans)
    (folder / "text_loader.inc").write_text(selected, encoding="utf8")
    definitions = opcode_table(formal / "references/majiro-py/opcodes.py")
    (folder / "majiro_opcodes.hpp").write_text(
        "#pragma once\n#include <array>\n#include <cstdint>\nnamespace majiro{\n"
        "struct Opcode{uint16_t code;const char* encoding;};\n"
        "inline constexpr std::array<Opcode," + str(len(definitions)) + "> kOpcodes={{\n" +
        ",\n".join("{" + str(code) + "," + json.dumps(encoding) + "}" for code, (_, encoding)
                  in sorted(definitions.items())) + "\n}};}\n", encoding="utf8")
    lua_root = formal / "references/player/lua/src"
    environment = msvc_environment()
    compiler = Path(environment["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    c_sources = sorted(p for p in lua_root.glob("*.c") if p.name not in ("lua.c", "luac.c", "print.c"))
    objects = [folder / (p.stem + ".obj") for p in c_sources]
    if not all(p.exists() for p in objects):
        run("lua_compile", [compiler, "/nologo", "/O2", "/MT", "/c", "/D_CRT_SECURE_NO_WARNINGS",
                            "/Fo" + str(folder) + os.sep, *c_sources], folder, env=environment)
    binary = folder / "text_resource_probe.exe"
    run("native_compile", [compiler, "/nologo", "/O2", "/EHsc", "/std:c++20", "/MT", "/utf-8",
                          "/I" + str(folder), "/I" + str(lua_root), "/Fo" + str(folder) + os.sep,
                          "/Fe" + str(binary), ROOT / "scripts/analysis/text_resource_probe.cpp",
                          ROOT / "player/core/majiro_core.cpp", ROOT / "player/core/lua_script.cpp",
                          *objects, "bcrypt.lib"], folder, env=environment)
    return binary


def java_build(formal, folder, toolchain, games):
    java = Path(toolchain["java"]) / "bin"
    jar = formal / "references/player/testing-json/json-20240303.jar"
    classes = folder / "java"
    classes.mkdir(exist_ok=True)
    production = ROOT / "player/android/src/org/majiro/player"
    run("java_compile", [java / "javac.exe", "-encoding", "UTF-8", "-source", "8", "-target", "8",
                        "-cp", jar, "-d", classes,
                        *[production / name for name in ("PatchManifest.java", "TextMode.java",
                           "ArchiveLayerPolicy.java", "TranslationModePolicy.java")],
                        ROOT / "scripts/analysis/TextResourceChecks.java",
                        ROOT / "scripts/analysis/TextModeChecks.java",
                        ROOT / "scripts/analysis/PatchManifestChecks.java",
                        ROOT / "scripts/analysis/ArchiveLayerChecks.java"], folder)
    classpath = os.pathsep.join(map(str, (classes, jar)))
    for fixture in ("TextModeChecks","PatchManifestChecks"):
        run(fixture,[java/"java.exe","-Dfile.encoding=UTF-8","-cp",classpath,"org.majiro.player."+fixture],folder)
    plan = json.loads(run("java_resources", [java / "java.exe", "-Dfile.encoding=UTF-8", "-cp", classpath,
                                           "org.majiro.player.TextResourceChecks",
                                           *[g["directory"] for g in games]], folder))
    duplicates = json.loads(run("java_archive_layers", [java / "java.exe", "-Dfile.encoding=UTF-8", "-cp", classpath,
                                                       "org.majiro.player.ArchiveLayerChecks",
                                                       *[g["directory"] for g in games]], folder))
    return plan, duplicates


def android_compile(formal, folder, toolchain):
    sdk = Path(toolchain["sdk"])
    java = Path(toolchain["java"]) / "bin"
    classes = folder / "android-java"
    classes.mkdir(exist_ok=True)
    jars = [sdk / "platforms/android-36/android.jar", formal / "references/player/SDL3/classes.jar"]
    sources = list((ROOT / "player/android/src").rglob("*.java"))
    ijk = formal / "references/player/ijk"
    sources.extend(ijk.rglob("*.java"))
    jars.extend(ijk.rglob("*.jar"))
    run("android_java", [java / "javac.exe", "-encoding", "UTF-8", "-source", "8", "-target", "8",
                        "-cp", os.pathsep.join(map(str, jars)), "-d", classes, *sources,
                        ROOT / "scripts/analysis/LibrarySelectionChecks.java"], folder)
    run("library_selection", [java / "java.exe", "-cp", os.pathsep.join(map(str, (classes, *jars))),
                              "org.majiro.player.LibrarySelectionChecks"], folder)
    compiler = sdk / "ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"
    run("android_native_syntax", [compiler, "--target=aarch64-linux-android26", "-std=c++20", "-fsyntax-only",
                                 "-I" + str(formal / "references/player/lua/src"),
                                 "-I" + str(formal / "references/player/SDL3/include"),
                                 ROOT / "player/android/game_host.cpp"], folder)


def synthetic_checks(binary, folder, real_plans):
    """Small disposable resources live only in the archive, never in games/."""
    fixture = folder / "20261111_synthetic"
    fixture.mkdir(exist_ok=True)
    (fixture/"start.mjo").unlink(missing_ok=True)
    def digest(raw):
        return hashlib.sha256(raw).hexdigest()
    def mjo(text, codec):
        raw = text.encode(codec) + b"\0"
        code = struct.pack("<HH", 0x840, len(raw)) + raw + struct.pack("<H", 0x82b)
        return b"MajiroObjV1.000\0" + struct.pack("<IIII", 0, 0, 0, len(code)) + code
    def arc(path, entries):
        names = b"".join(name.encode("cp932") + b"\0" for name in entries)
        names_at = 28 + 12 * len(entries)
        data_at = names_at + len(names)
        at, index, body = data_at, [], []
        for name, raw in entries.items():
            index.append(struct.pack("<III", 0, at, len(raw)))
            body.append(raw);at += len(raw)
        path.write_bytes(b"MajiroArcV2.000\0" + struct.pack("<III", len(entries), names_at, data_at)
                         + b"".join(index) + names + b"".join(body))
    def plan(name, spec, expected=0, test=None):
        spec = dict(spec)
        if test:
            spec["test"] = test
        path = folder / ("20261111_negative_" + name + ".lua")
        path.write_text("return " + lua_value(spec), encoding="utf8")
        output = run("negative_" + name, [binary, ROOT, path], folder, expected=expected)
        return json.loads(output) if expected == 0 else {"status": "rejected"}
    original = mjo("\u65e5\u672c\u8a9e", "cp932")
    chinese = mjo("\u4e2d\u6587", "gbk")
    arc(fixture / "base.arc", {"start.mjo": original, "game.env": b"CLIENT_WIDTH 800\nCLIENT_HEIGHT 600"})
    (fixture / "patches").mkdir(exist_ok=True)
    arc(fixture / "patches/local.arc", {"start.mjo": chinese})
    base = {"root": str(fixture), "mode": "original", "layers": [],
            "archives": [{"path": "base.arc", "scope": "original"}], "configuration": {}}
    checks = {}
    online_test = r"""
      local T=module('runtime/majiro_translation.lua')
      local json=module('runtime/majiro_json.lua');local codec=module('runtime/majiro_codec.lua')
      local source=HOST.script('start.mjo');local before=codec.encode(source)
      local submitted={}
      local api=T.new{mode='online',game=RES.identity('source'),json=json,
        hash=function(value)return HOST.storage('sha256\n'..value):sub(4)end,
        opcodes=module('tables/opcodes.lua'),names={},host=function(action,payload)
          if action=='config'then return json.encode{mode='online',ready=true,context=2,generation='fixture'}end
          if action=='submit'then submitted=json.decode(payload).lines;return 'queued'end
          if action=='lookup'then return 'display translated'end
          return ''
        end}
      local frame={script=source,index=2}
      local vm={context_generation=0,current_offset=0,functions={},frame=function()return frame end}
      api.instruction(vm,'text',source.code[1][3])
      assert(#submitted==1 and submitted[1].text==source.code[1][3])
      assert(submitted[1].id:match('^dialogue%-v3%-original:'))
      assert(api.display(vm,source.code[1][3])=='display translated')
      assert(codec.encode(source)==before)
      source.text_origin='cp936'
      assert(not pcall(api.scan,source,1,2,vm))
      local data=module('runtime/majiro_text_resources.lua')
      for _,bad in ipairs{
        'return {x=1+1}', 'return (function()return {}end)()','while true do end',
        'return {x=1,x=2}', 'return {} print(1)', string.char(27)..'Lua',
        'return {x=string.rep("x",999999999)}'
      }do assert(not pcall(data.data,bad,'negative'))end
      assert(not pcall(data.utf8,string.char(0xc0,0x80)))
      assert(not pcall(data.utf8,string.char(0xed,0xa0,0x80)))
      assert(data.data('return {a=false,[2]="\\230\\151\\165"}','valid').a==false)
      HOST.movies=function()return{}end
      HOST.identity=function()return string.rep('a',64)end
      HOST.member_names=HOST.text_resource_names
      HOST.engine_requirements=function()
        return{family='Majiro',archive_version=2,script_signature='MajiroObjV1.000',features={}}
      end
      local configuration=module('runtime/player_configuration.lua').make()
      assert(configuration.width==800 and configuration.height==600)
      local files={}
      local storage=function(request)
        if request:sub(1,7)=='sha256\n' then return HOST.storage(request)end
        local action,name,body=request:match('^(%S+) ([^\n]+)\n?(.*)$')
        if action=='save_exists'then return files[name]and'OK 1'or'OK 0'end
        if action=='save_read'then return files[name]and'OK data\n'..files[name]or'OK absent'end
        if action=='save_write'then files[name]=body;return'OK'end
        error('unexpected save mutation')
      end
      local store=module('runtime/majiro_store.lua')
      local old=store.new(codec,storage,string.rep('a',64),{},{partition_foreign=true})
      old:commit{schema=1,identity=old.identity,slots={},profile={marker='old'}}
      local old_index=files['index.mjs']
      local current=store.new(codec,storage,string.rep('b',64),{},{partition_foreign=true})
      assert(current:load_index().profile==nil and current.foreign_identity==old.identity)
      current:commit{schema=1,identity=current.identity,slots={},profile={marker='new'}}
      assert(files['index.mjs']==old_index)
      assert(old:load_index().profile.marker=='old')
    """
    checks["no_exe_online_display_only"] = plan("no_exe_online_display_only", dict(base, mode="online"), test=online_test)
    encoded = dict(base, mode="local",
                   archives=[{"path": "patches/local.arc", "scope": "cp936"}, *base["archives"]],
                   configuration={"schema": 2, "vm_encoding": "cp936", "resource_hashes": {"start.mjo": digest(chinese)}})
    checks["declared_cp936"] = plan("declared_cp936", encoded)
    checks["same_size_hash_mismatch"] = plan("same_size_hash_mismatch", dict(encoded,
        configuration={"schema": 2, "resource_hashes": {"start.mjo": "0" * 64}}), 1)
    checks["untyped_arc"] = plan("untyped_arc", dict(encoded,
        archives=[{"path": "patches/local.arc", "scope": "untyped_local"}, *base["archives"]]), 1)
    checks["online_rejects_local_scope"] = plan("online_rejects_local_scope", dict(encoded, mode="online", configuration={}), 1)
    checks["images_do_not_expose_mjo"] = plan("images_do_not_expose_mjo", dict(base,
        archives=[{"path": "patches/local.arc", "scope": "images"}, *base["archives"]]))
    assert checks["images_do_not_expose_mjo"]["identity"] == checks["no_exe_online_display_only"]["identity"]
    utf = mjo("\u65e5\u672c\u8a9e", "utf8")
    arc(fixture / "patches/utf.arc", {"start.mjo": utf})
    checks["declared_utf8"] = plan("declared_utf8", dict(encoded,
        archives=[{"path": "patches/utf.arc", "scope": "utf-8"}, *base["archives"]],
        configuration={"schema": 2, "vm_encoding": "utf-8", "resource_hashes": {"start.mjo": digest(utf)}}))
    arc(fixture / "patches/local.arc", {"start.mjo": chinese, "unlisted.cfg": b"unsafe"})
    checks["mixed_unlisted_cfg"] = plan("mixed_unlisted_cfg", encoded, 1)
    # Real adapters: relocate only their declared path in a plan, no game writes.
    actual = next(g for g in real_plans if Path(g["root"]).name == "narci_himeko")
    local = next(p for p in actual["modes"] if p["mode"] == "local")
    # Missing declaration is caught before enumeration in actual Java. Here the
    # Lua preflight receives the existing directory with one required hash removed.
    partial = dict(local, root=actual["root"], configuration=dict(local["configuration"],
        text_adapter={"kind": "sequential_text_v1", "path": "patches/not-installed.lua"}))
    checks["missing_real_adapter"] = plan("missing_real_adapter", partial, 1)
    stale = dict(local, root=actual["root"], configuration=dict(local["configuration"],
        schema=2, metadata_hashes={local["configuration"]["text_adapter"]["path"]: "f" * 64}))
    checks["real_metadata_hash_mismatch"] = plan("real_metadata_hash_mismatch", stale, 1)
    # A tiny sequential layer validates all inventory entries before loading VM.
    script_dir = fixture / "patches/source"
    script_dir.mkdir(exist_ok=True)
    (script_dir / "start.mjo").write_bytes(original)
    adapter = {"schema": 1, "identity": "a" * 64, "scripts": {
        "start.mjo": {"sha256": digest(original), "code_size": len(original) - 32, "entries": []},
        "missing.mjo": {"sha256": digest(original), "code_size": len(original) - 32, "entries": []}},
        "encoded_strings": {}, "encoded_characters": {}, "dialogs": {}}
    adapter_path = fixture / "patches/adapter.lua"
    adapter_path.write_text("return " + lua_value(adapter), encoding="utf8")
    sequential = dict(base, mode="local", layers=[{"directory": "patches/source", "kind": "source_mjo", "encoding": "cp932"}],
        configuration={"schema": 1, "script_layers": [{"directory": "patches/source", "kind": "source_mjo"}],
                       "text_adapter": {"kind": "sequential_text_v1", "path": "patches/adapter.lua"}})
    checks["incomplete_inventory"] = plan("incomplete_inventory", sequential, 1)
    del adapter["scripts"]["missing.mjo"]
    adapter["scripts"]["start.mjo"]["entries"] = [
        {"offset": 0, "opcode": 0x840, "expected": "wrong", "value": "fixture", "resume_offset": len(original) - 34}]
    adapter_path.write_text("return " + lua_value(adapter), encoding="utf8")
    checks["wrong_anchor_preflight"] = plan("wrong_anchor_preflight", sequential, 1)
    # Frozen bytes are independent of later filesystem mutation; uncached reload
    # must reject a changed source instead of mixing it into the current launch.
    original_file = fixture / "start.mjo"
    original_file.write_bytes(original)
    changed = mjo("\u65b0\u7248\u6587", "cp932")
    assert len(changed)==len(original)
    frozen_test = """
      local old=HOST.script('start.mjo').source_sha256
      local p=PLAN.root..'/start.mjo'
      local f=assert(io.open(p,'rb'));local raw=f:read('*a');f:close()
      f=assert(io.open(p,'wb'));f:write(%s);f:close()
      assert(HOST.script('start.mjo').source_sha256==old)
      HOST.clear_snapshot_cache_for_test()
      assert(not pcall(HOST.script,'start.mjo'))
      f=assert(io.open(p,'wb'));f:write(raw);f:close()
    """ % ('"' + "".join("\\%03d" % byte for byte in changed) + '"')
    checks["frozen_snapshot_and_changed_reload"] = plan("frozen_snapshot_and_changed_reload", base, test=frozen_test)
    old_identity=checks["frozen_snapshot_and_changed_reload"]["identity"]
    original_file.write_bytes(changed)
    checks["same_size_next_launch_new_identity"] = plan("same_size_next_launch_new_identity", base)
    assert checks["same_size_next_launch_new_identity"]["identity"]!=old_identity
    original_file.unlink()
    arc(fixture / "no-scripts.arc", {"game.env": b"CLIENT_WIDTH 800\nCLIENT_HEIGHT 600"})
    checks["no_original_script_rejected"] = plan("no_original_script_rejected", dict(base,
        archives=[{"path":"no-scripts.arc","scope":"original"}]),1)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal", type=Path, default=WORKSPACE / "recovered")
    parser.add_argument("--evidence", type=Path,
                        default=WORKSPACE / "archive/2026-11" /
                        ("20261111_text_mode_worker" if ROOT.parent.name=="text-mode-restoration"
                         else "20261111_text_mode_integration") /
                        ("." if ROOT.parent.name=="text-mode-restoration" else
                         time.strftime("%Y%m%d_%H%M%S_")+str(os.getpid())))
    parser.add_argument("--skip-android", action="store_true")
    args = parser.parse_args(argv)
    args.formal = args.formal.resolve()
    args.evidence = args.evidence.resolve()
    assert args.evidence.is_relative_to(WORKSPACE / "archive"), "evidence must remain archived"
    args.evidence.mkdir(parents=True, exist_ok=True)
    tools = json.loads((args.formal / "reports/android_toolchain.json").read_text(encoding="utf8"))
    games = json.loads((args.formal / "config/player_samples.json").read_text(encoding="utf8"))["games"]
    plan, duplicate_report = java_build(args.formal, args.evidence, tools, games)
    print("Java production policy checks passed", flush=True)
    binary = native_build(args.formal, args.evidence)
    print("Native production loader fixture compiled", flush=True)
    results = []
    for game in plan["games"]:
        identities = {}
        for spec in game["modes"]:
            name = Path(game["root"]).name
            label = "native_" + name.replace(" ", "_") + "_" + spec["mode"]
            fixture = args.evidence / ("20261111_" + label + ".lua")
            fixture.write_text("return " + lua_value(dict(spec, root=game["root"])), encoding="utf8")
            result = json.loads(run(label, [binary, ROOT, fixture], args.evidence))
            result.update(game=name, mode=spec["mode"])
            identities[spec["mode"]] = result["identity"]
            results.append(result)
            print(name, spec["mode"], result["resources"], result["executed_replacements"], flush=True)
        assert identities["original"] == identities["online"]
        if "local" in identities:
            assert identities["local"] != identities["original"]
    if not args.skip_android:
        android_compile(args.formal, args.evidence, tools)
        print("Android Java compile and native syntax check passed", flush=True)
    negative = synthetic_checks(binary, args.evidence, plan["games"])
    print("Synthetic/partial/online checks passed", len(negative), flush=True)
    measurements=[]
    for game_name in ("narci_onetwo","narci_sumire"):
        label="native_"+game_name+"_local"
        fixture=args.evidence/("20261111_"+label+".lua")
        uncached=json.loads(run(label+"_uncached",[binary,ROOT,fixture,"uncached"],args.evidence))
        cached=next(row for row in results if row["game"]==game_name and row["mode"]=="local")
        assert cached["identity"]==uncached["identity"] and cached["executed_replacements"]==uncached["executed_replacements"]
        before=uncached["io"]["loose_bytes"]+uncached["io"]["archive_bytes"]
        after=cached["io"]["loose_bytes"]+cached["io"]["archive_bytes"]
        assert after<before
        measurements.append({"game":game_name,"cached_io":cached["io"],"uncached_io":uncached["io"],
                             "read_bytes_saved":before-after,"read_reduction_percent":round(100*(before-after)/before,2),
                             "cached_seconds":json.loads((args.evidence/("20261111_"+label+".timing.json")).read_text())["elapsed_seconds"],
                             "uncached_seconds":json.loads((args.evidence/("20261111_"+label+"_uncached.timing.json")).read_text())["elapsed_seconds"]})
    report = {"status": "passed", "java_checks": plan["checks"],
              "archive_checks": duplicate_report["checks"], "games": results,
              "plans": plan["games"], "apk_built": False, "device_accessed": False,
              "game_files_modified": False, "android_compile": not args.skip_android,
              "synthetic": negative,"performance":measurements,"source_sha256":source_hashes()}
    (args.evidence / "20261111_candidate_tests.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    if ROOT.parent.name!="text-mode-restoration":
        report["evidence"]=str(args.evidence)
        (ROOT/"reports/text_resource_tests.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
    print(json.dumps({"status": "passed", "runs": len(results),
                      "executed_replacements": sum(row["executed_replacements"] for row in results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
