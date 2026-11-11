"""Preserve native floating results through the production VM/save codec."""
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "convert")]
from port_common import ROOT, archive_dir, sha256, timestamp, write_json
from lua_bridge import create_lua


def main():
    lua = create_lua()
    codec_path = ROOT / "player/runtime/majiro_codec.lua"
    vm_path = ROOT / "player/runtime/majiro_vm.lua"
    codec = lua.execute(codec_path.read_text("utf8"))
    vm = lua.execute(vm_path.read_text("utf8"))
    ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text("utf8"))
    count = lua.eval(r"""function(codec,mod,ops)
      local script={name='numbers.mjo',source_sha256='fixture',main=0,functions={},
        code={{0,0x803,3.4028234663852886e38},{6,0x803,2},{12,0x101},
          {14,0x1b1,288,42,-1},{24,0x803,0/0},{30,0x1b1,288,43,-1},
          {40,0x803,-1/math.huge},{46,0x1b1,288,44,-1},{56,0x191},
          {58,0x82c,56}}}
      local function make()
        return mod.new{opcodes=ops,loader=function()return script end,
          syscall=function()error('unexpected syscall')end}
      end
      local before=make();before:begin(script.name)
      for i=1,8 do before:step()end
      assert(before.globals[1][42]==math.huge)
      assert(before.globals[1][43]~=before.globals[1][43])
      assert(1/before.globals[1][44]==-math.huge)
      local data=codec.encode(before:export_state())
      local after=make();after:import_state(codec.decode(data))
      assert(after.globals[1][42]==math.huge)
      assert(after.globals[1][43]~=after.globals[1][43])
      assert(1/after.globals[1][44]==-math.huge)
      assert(data==codec.encode(after:export_state()))
      local shared={value=-math.huge};local root={shared,shared,nan=0/0};root.self=root
      local restored=codec.decode(codec.encode(root))
      assert(restored[1]==restored[2] and restored.self==restored)
      assert(restored[1].value==-math.huge and restored.nan~=restored.nan)
      assert(codec.decode('MJS1\nN1.25\n')==1.25)
      local bad={'FNaN','F1','Finf','Nnan','Ninf','F+inf\nZ',
        'T1,1\nFnan\nN1','T1,1\nR2\nN1'}
      for _,value in ipairs(bad)do assert(not pcall(codec.decode,'MJS1\n'..value..'\n'))end
      return 11+#bad
    end""")(codec, vm, ops)
    folder = archive_dir("player_snapshot_numbers")
    report = {"timestamp": timestamp(), "status": "passed", "checks": count,
              "production_files": {str(p.relative_to(ROOT)): sha256(p) for p in (codec_path, vm_path)},
              "scope": "VM-generated infinity/NaN/signed zero, aliases, cycles and strict data-only decoding",
              "device_accessed": False}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/player_snapshot_numbers.json", report)
    print("Player snapshot numeric checks:", count)


if __name__ == "__main__":
    main()
