"""Exercise Android's real raw MJO loader, metadata and source configuration."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from build_player_core import msvc_environment
from majiro_arc import Catalogue
from majiro_config import config_tokens
from majiro_mjo import Script, opcode_table
from majiro_patch import Patch
from port_common import ROOT, archive_dir, lua_string, lua_value, read_json, sha256, timestamp, write_json
from prepare_player_patch import build
from proofread_text import Overrides
from lua_bridge import create_lua


def cp932_decoder():
    entries = []
    for first in range(256):
        sequences = [bytes((first, second)) for second in range(256)] if (
            0x81 <= first <= 0x9f or 0xe0 <= first <= 0xfc) else [bytes((first,))]
        for raw in sequences:
            try:
                value = raw.decode("cp932")
            except UnicodeDecodeError:
                continue
            key = '"' + "".join("\\" + str(byte).zfill(3) for byte in raw) + '"'
            entries.append("[" + key + "]=" + lua_string(value))
    return ("local map={" + ",".join(entries) + "}\nreturn function(bytes)\n"
            "local output={};local at=1\nwhile at<=#bytes do local first=bytes:byte(at)\n"
            "local n=((first>=0x81 and first<=0x9f)or(first>=0xe0 and first<=0xfc))and 2 or 1\n"
            "output[#output+1]=assert(map[bytes:sub(at,at+n-1)],'invalid CP932 fixture bytes');at=at+n\n"
            "end;return table.concat(output)end\n")


def main():
    folder = archive_dir("raw_player_patch_tests")
    generated = build()
    metadata = ROOT / generated["metadata"]
    source = ROOT / "player/android/game_host.cpp"
    host = source.read_text(encoding="utf8")
    # Compile the exact production loading functions with only transport mocks.
    spans = [
        ("std::string key(", "std::vector<uint8_t> read_at("),
        ("bool read_patch(", "std::vector<uint8_t> read_member("),
        ("std::vector<uint8_t> read_member(", "void rebuild_members("),
        ("std::string argument(", "double numeric("),
        ("void verify_patch_member(const std::string& name,const std::vector<uint8_t>& raw){",
         "std::vector<uint8_t> parse_hex("),
        ("int resource_text(", "int script_file_read("),
        ("int script(lua_State*", "int graphics("),
    ]
    declarations = (
        "void verify_patch_member(const std::string&,const std::vector<uint8_t>&);\n"
        "std::string decode_source(const std::vector<uint8_t>&,bool);\n"
    )
    selected = declarations + "\n".join(host[host.index(begin):host.index(end, host.index(begin))]
                                         for begin, end in spans)
    assert "player-data/scripts" not in selected and "catalogue.lua" not in selected
    (folder / "raw_loader.inc").write_text(selected, encoding="utf8")
    definitions = opcode_table()
    (folder / "majiro_opcodes.hpp").write_text(
        "#pragma once\n#include <array>\n#include <cstdint>\nnamespace majiro{\n"
        "struct Opcode{uint16_t code;const char* encoding;};\n"
        "inline constexpr std::array<Opcode," + str(len(definitions)) + "> kOpcodes={{\n" +
        ",\n".join("{" + str(code) + "," + lua_string(encoding) + "}" for code, (_, encoding)
                  in sorted(definitions.items())) + "\n}};}\n", encoding="utf8")
    decoder = folder / "cp932_decoder.lua"
    decoder.write_text(cp932_decoder(), encoding="utf8")
    lua_root = ROOT / "references/player/lua/src"
    environment = msvc_environment()
    compiler = Path(environment["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    def run(name, args, timeout=120):
        result = subprocess.run(args, cwd=folder, env=environment, capture_output=True, timeout=timeout)
        (folder / (folder.name[:8] + "_" + name + ".txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(name + " failed; see " + str(folder))
        return result.stdout
    c_sources = sorted(p for p in lua_root.glob("*.c") if p.name not in ("lua.c", "luac.c", "print.c"))
    run("lua_compile", [str(compiler), "/nologo", "/O2", "/MT", "/c", "/D_CRT_SECURE_NO_WARNINGS",
                        "/Fo" + str(folder) + os.sep, *map(str, c_sources)])
    binary = folder / "raw_patch_probe.exe"
    fixture = ROOT / "scripts/analysis/player_raw_patch_probe.cpp"
    run("probe_compile", [str(compiler), "/nologo", "/O2", "/EHsc", "/std:c++20", "/MT", "/utf-8",
                          "/I" + str(folder), "/I" + str(lua_root),
                          "/Fo" + str(folder) + os.sep, "/Fe" + str(binary),
                          str(fixture), str(ROOT / "player/core/majiro_core.cpp"),
                          str(ROOT / "player/core/lua_script.cpp"),
                          *map(str, folder.glob("*.obj")), "bcrypt.lib"])
    raw_root = ROOT / read_json(ROOT / "config/translation_source.json")["extracted_scenario"]
    outputs = folder / "decoded"; outputs.mkdir()
    native = json.loads(run("native", [str(binary), str(metadata), str(decoder),
                                      str(ROOT / "player/runtime/majiro_patch.lua"), str(raw_root), str(outputs)]))
    patch, reviewed = Patch(), Overrides()
    instructions = replacements = preludes = relocations = 0
    records, raw_records = {}, {}
    for member in patch.members:
        if member.extension == ".cfg":
            actual = json.loads((outputs / (member.name + ".json")).read_text(encoding="utf8"))
            assert actual == patch.decode(member.read()), member.name
            assert config_tokens(actual) == config_tokens(patch.decode(member.read())), member.name
            continue
        if member.extension != ".mjo":
            continue
        raw = member.read()
        repairs = patch.repairs(member.name, member.sha256) or []
        stack = patch.preludes(member.name, member.sha256) or []
        script = Script(member.name, raw, definitions, string_decoder=patch.decode,
                        address_repairs=repairs, stack_repairs=stack)
        original = {
            "schema": 1, "name": script.name, "source_sha256": script.sha256,
            "main": script.main, "line_count": script.line_count, "functions": [list(f) for f in script.functions],
            "code_size": script.code_size,
            "code": [[r.offset, r.opcode, *r.operands] for r in script.instructions],
        }
        raw_result = json.loads((outputs / (member.name + ".raw.json")).read_text(encoding="utf8"))
        assert raw_result.pop("patch_source") is True
        assert raw_result == original, member.name + ": native original rows"
        expected = reviewed.apply_script(script, script.runtime_data())
        expected["functions"] = [list(f) for f in expected["functions"]]
        actual = json.loads((outputs / (member.name + ".json")).read_text(encoding="utf8"))
        assert actual.pop("raw_patch_applied") and actual.pop("patch_source")
        assert actual == expected, member.name + ": final runtime rows"
        records[member.name] = expected
        raw_records[member.name] = original
        instructions += len(original["code"])
        preludes += len(stack); relocations += len(repairs)
    reviewed.finish()
    assert native["scripts"] == len(records) == 121
    lua = create_lua()
    data = lua.execute(metadata.read_text(encoding="utf8"))
    decode = lua.execute(decoder.read_text(encoding="utf8"))
    adapter = lua.execute((ROOT / "player/runtime/majiro_patch.lua").read_text(encoding="utf8"))["new"](data, decode)
    codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text(encoding="utf8"))
    checks = []
    def reject(script, label):
        target = lua.table_from(script, recursive=True)
        before = codec["encode"](target["code"])
        ok, _ = lua.eval("function(fn,value)return pcall(fn,value)end")(adapter["apply"], target)
        assert not ok and codec["encode"](target["code"]) == before, label
        checks.append(label)
    chosen = next(name for name in raw_records if any(data["scripts"][name.lower()]["text_replacements"].keys()))
    raw = dict(raw_records[chosen], patch_source=True)
    bad = copy.deepcopy(raw); bad["source_sha256"] = "f" * 64
    reject(bad, "changed raw hash rejected before mutating decoded rows")
    bad = copy.deepcopy(raw); bad["raw_patch_applied"] = True
    reject(bad, "double application rejected")
    entry_offset, entries = next(iter(data["scripts"][chosen.lower()]["text_replacements"].items()))
    bad = copy.deepcopy(raw)
    row = next(row for row in bad["code"] if row[0] == entry_offset)
    row[int(entries[1]["operand"]) + 2] = "changed anchor"
    reject(bad, "changed formal proofreading anchor rejected atomically")
    bad = copy.deepcopy(raw); bad["code"] = [row for row in bad["code"] if row[0] != entry_offset]
    reject(bad, "absent formal proofreading offset rejected atomically")
    stack_name = next(name for name in raw_records if len(data["scripts"][name.lower()]["preludes"]))
    bad = copy.deepcopy(raw_records[stack_name]); bad["patch_source"] = True
    repair = data["scripts"][stack_name.lower()]["preludes"][1]
    row = next(row for row in bad["code"] if row[0] == repair["offset"]); row[1] = 0x191
    reject(bad, "changed stack prelude opcode rejected atomically")
    ordinary = lua.table_from(raw_records["start.mjo"], recursive=True)
    assert adapter["apply"](ordinary)["raw_patch_applied"] is None
    checks.append("original MJO receives no raw translation transformation")
    # Decode every tunnel entry and full original patched configuration bytes.
    for pair, character in patch.tunnel.items():
        assert adapter["decode"](pair) == character
    checks.append("all pinned patch tunnel pairs round-trip")
    ok, _ = lua.eval("function(fn)return pcall(fn,string.char(129))end")(adapter["decode"])
    assert not ok
    checks.append("truncated multibyte patch text fails explicitly")
    # Configuration uses actual resources even if old generated files coexist.
    catalogue = Catalogue(ROOT)
    legacy_reads = []
    def module(path):
        actual = ROOT / ("player/" + path if path.startswith("runtime/") else "player/tables/" + Path(path).name)
        return actual.read_text(encoding="utf8")
    host = lua.table()
    host["asset_text"] = module
    host["game_text"] = lambda path: legacy_reads.append(path) or "error('legacy file used')"
    host["movies"] = lambda: lua.table()
    host["identity"] = lambda: "a" * 64
    host["engine_requirements"] = lambda: lua.table_from({"family":"Majiro","archive_version":3,
        "script_signature":"MajiroObjX1.000","features":["save:105"]},recursive=True)
    members = [items[0] for items in catalogue.by_name.values()
               if items[0].extension in (".cfg", ".env")]
    host["member_names"] = lambda: lua.table_from([member.name for member in members])
    by_name = {member.name: member for member in members}
    host["resource_text"] = lambda name: (
        patch.decode(by_name[name].read()) if by_name[name].identity.startswith("translation:")
        else by_name[name].read().decode("cp932"))
    lua.globals()["HOST"] = host
    configuration = lua.execute(module("runtime/player_configuration.lua"))["make"]()
    assert configuration["width"] == 1280 and configuration["height"] == 720
    assert not legacy_reads
    for member in members:
        if member.extension != ".cfg": continue
        text = host["resource_text"](member.name)
        actual = [configuration["config_files"][member.name][i]
                  for i in range(1, len(configuration["config_files"][member.name]) + 1)]
        assert actual == config_tokens(text), member.name
    checks.append("all raw configuration tokens match independent parser and ignore coexisting generated Lua")
    # Boot parses, creates metadata before configuration, and composes both adapters.
    boot = (ROOT / "player/runtime/player_boot.lua").read_text(encoding="utf8")
    assert boot.index("PLAYER_DECODE_PATCH=raw_patch.decode") < boot.index("player_configuration.lua")
    assert "raw_patch.apply(script)" in boot and "text_patch.apply(script)" in boot
    assert "player-data" not in boot and "player-data" not in host_source_configuration()
    assert 'try { if (env && bridge) debug("ERROR: " + message); }' in host_source()
    lua.eval("function(source)return assert(loadstring(source))~=nil end")(boot)
    checks.append("boot initializes raw decoding before configuration and retains generic text-patch composition and error logging")
    for source_path in ("player/core/lua_script.cpp", "player/android/game_host.cpp"):
        assert "player-data/scripts" not in (ROOT / source_path).read_text(encoding="utf8")
    replacements = sum(len(entries) for _, record in data["scripts"].items()
                       for _, entries in record["text_replacements"].items())
    report = {
        "schema": 1, "timestamp": timestamp(), "status": "passed",
        "scripts": len(records), "raw_instructions": instructions, "formal_text_replacements": replacements,
        "stack_preludes": preludes, "repaired_jump_targets": relocations, "native": native,
        "checks": checks, "all_tunnel_pairs": len(patch.tunnel), "raw_config_files": native["configs"],
        "runtime_legacy_script_reads": native["legacy_reads"],
        "evidence": str(folder.relative_to(ROOT)), "device_accessed": False,
        "full_gameplay_verified": False, "original_save_resume_verified": False,
        "scope": "actual native raw loader functions + C++ parser + Lua adapter differential against Python formal pipeline",
        "deployment": {"metadata_source": "player/patches/ruri/majiro_metadata.lua",
                       "metadata_target": "patches/majiro_metadata.lua",
                       "raw_source_target": "patches/scenario/<original name>",
                       "font_target": "patches/fonts/SourceHanSansSC-Regular.otf",
                       "narcissu_contract_unchanged": True},
        "source_sha256": {name: sha256(ROOT / name) for name in (
            "player/android/game_host.cpp", "player/core/lua_script.cpp", "player/core/lua_script.hpp",
            "player/runtime/player_boot.lua", "player/runtime/player_configuration.lua", "player/runtime/majiro_patch.lua",
            "scripts/build/prepare_player_patch.py", "scripts/analysis/test_raw_player_patch.py",
            "scripts/analysis/player_raw_patch_probe.cpp")},
        "remaining": ["Android device runtime of this new raw path has not been exercised",
                      "Amber Quartz GBK/filename remapping and native save resume need separate verified adapters"],
    }
    write_json(ROOT / "reports/raw_player_patch_tests.json", report)
    write_json(folder / "20261110_raw_player_patch_tests.json", report)
    for path in [binary, *folder.glob("*.obj")]: path.unlink(missing_ok=True)
    print("Raw player patch:", len(records), "native scripts,", replacements, "formal texts,",
          relocations, "jumps,", preludes, "preludes; no compiled legacy script reads")
    return 0


def host_source():
    return (ROOT / "player/android/game_host.cpp").read_text(encoding="utf8")


def host_source_configuration():
    return (ROOT / "player/runtime/player_configuration.lua").read_text(encoding="utf8")


if __name__ == "__main__":
    raise SystemExit(main())
