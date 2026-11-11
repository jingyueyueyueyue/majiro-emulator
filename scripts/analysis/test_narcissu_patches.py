"""Verify five original raw MJO/TXT mappings and execute their shared VM adapters."""
from pathlib import Path
import hashlib
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_arc import Archive
from majiro_mjo import Script
from port_common import ROOT, archive_dir, timestamp, write_json
from lua_bridge import create_lua


def main():
    evidence = archive_dir("narcissu_patch_tests")
    config = json.loads((ROOT / "config/player_samples.json").read_text(encoding="utf8"))
    games = []
    for game in config["games"]:
        if not game["id"].startswith("narci_"):
            continue
        path = ROOT / "localization/narcissu" / game["id"]
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf8"))
        adapter_bytes = (path / "adapter.lua").read_bytes()
        assert hashlib.sha256(adapter_bytes).hexdigest() == manifest["adapter_sha256"]
        for row in manifest["deployment"]:
            assert hashlib.sha256(Path(row["source"]).read_bytes()).hexdigest() == row["sha256"]
        lua = create_lua()
        data = lua.execute(adapter_bytes.decode("utf8"))
        module = lua.execute((ROOT / "player/runtime/majiro_text_patch.lua").read_text(encoding="utf8"))
        adapter = module["new"](data, lambda value: value.encode("cp932"))
        assert adapter["window_title"] == manifest["window_title"]
        for text, translated in data["dialogs"].items():
            assert adapter["dialog"](text + " ") == translated + " "
        assert adapter["graphics"]("text 1 2 3 4 5 6\nold-font\nmessage") == (
            "text 1 2 3 4 5 6\n__majiro_style__1:sans-serif\nmessage")
        assert adapter["graphics"]("glyphs 1 2 3 4 5 6\nold-font\n5 5\nmessage") == (
            "glyphs 1 2 3 4 5 6\n__majiro_style__1:sans-serif\n5 5\nmessage")
        assert adapter["graphics"]("fill 1 0 0 10 10 0") == "fill 1 0 0 10 10 0"
        # Exact translated strings recover the original DLL's CP936 output.
        lua.eval("""function(adapter,data)
          for text,hex in pairs(data.encoded_strings)do
            local raw=hex:gsub('..',function(value)return string.char(tonumber(value,16))end)
            assert(adapter.bytes(text)==raw,'translated DLL byte output differs')
          end
        end""")(adapter,data)
        vm_module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
        opcodes = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf8"))
        codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text(encoding="utf8"))
        inspect = lua.eval(r"""
          function(module,ops,codec,adapter,data,source)
            local before=codec.clone(source)
            local invalid=codec.clone(source);invalid.source_sha256=string.rep('f',64)
            assert(not pcall(adapter.apply,invalid))
            assert(codec.encode(invalid.code)==codec.encode(before.code),'rejected source mutated')
            local script=adapter.apply(source)
            local trace={}
            local vm=module.new{loader=function()return script end,opcodes=ops,
              syscall=function()error('unexpected patch fixture syscall')end,
              text_ops={execute=function(_,kind,value)trace[#trace+1]={kind,value}end}}
            vm:begin(script.name)
            local record=data.scripts[script.name:lower()]
            local count,multiline=0,0
            for _,entry in ipairs(record.entries)do
              vm:frame().index=assert(script.positions[entry.offset])
              if entry.opcode==0x801 then
                vm:step();assert(vm:pop()==entry.value,'translated ldstr differs')
              else
                trace={}
                local steps=0
                repeat
                  vm:step();steps=steps+1;assert(steps<1000,'translated continuation escaped')
                until vm:frame().script.code[vm:frame().index][1]==entry.resume_offset
                local expected={};local at=1
                while true do
                  local ending=entry.value:find('\\n',at,true)
                  expected[#expected+1]={'text',ending and entry.value:sub(at,ending-1)or entry.value:sub(at)}
                  if not ending then break end
                  expected[#expected+1]={'proc'}
                  expected[#expected+1]={'ctrl','n'}
                  at=ending+2;multiline=multiline+1
                end
                assert(codec.encode(trace)==codec.encode(expected),'translated text/control trace differs')
              end
              count=count+1
            end
            return count,multiline
          end
        """)
        checks, multiline = 0, 0
        files = {row["name"].lower(): row for row in manifest["files"]}
        for name, row in files.items():
            if not name.startswith("#mjo/"):
                continue
            raw = Path(row["path"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == row["sha256"]
            script = Script(Path(row["name"]).name, raw)
            source = lua.table_from({
                "name": script.name, "source_sha256": script.sha256, "main": script.main,
                "line_count": script.line_count, "code_size": script.code_size,
                "functions": script.functions,
                "code": [[r.offset, r.opcode, *r.operands] for r in script.instructions],
            }, recursive=True)
            count, wrapped = inspect(vm_module, opcodes, codec, adapter, data, source)
            checks += count
            multiline += wrapped
        assert checks == manifest["replacements"]
        resource_members = sum(len(Archive(Path(row["path"])).members) for row in manifest["files"]
                               if row["name"].endswith(".arc"))
        # Compile the production entry without running a host or a game.
        lua.eval("function(text)return assert(loadstring(text))~=nil end")(
            (ROOT / "player/runtime/player_boot.lua").read_text(encoding="utf8"))
        games.append({"game": game["id"], "scripts": manifest["scripts"],
                      "original_text_units": manifest["text_units"], "executed_replacements": checks,
                      "executed_translated_line_breaks": multiline, "overlay_members": resource_members,
                      "adapter_sha256": manifest["adapter_sha256"], "status": "passed"})
        print(game["id"], checks, "original-source VM replacements passed")
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "games": games,
              "scripts": sum(g["scripts"] for g in games),
              "original_text_units": sum(g["original_text_units"] for g in games),
              "executed_replacements": sum(g["executed_replacements"] for g in games),
              "windows_code_executed": False, "device_accessed": False,
              "full_gameplay_verified": False, "evidence": str(evidence.relative_to(ROOT)),
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                  "player/runtime/majiro_text_patch.lua", "player/runtime/player_boot.lua",
                  "scripts/convert/prepare_narcissu_patches.py", "scripts/analysis/test_narcissu_patches.py")}}
    write_json(ROOT / "reports/narcissu_patch_tests.json", report)
    write_json(evidence / "20261110_narcissu_patch_tests.json", report)


if __name__ == "__main__":
    main()
