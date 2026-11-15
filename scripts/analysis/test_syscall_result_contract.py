"""Execute real syscall opcodes so helper diagnostics cannot masquerade as deferral."""
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "convert")]
from audit_player_semantics import registered_providers
from native_pixels import Pixels
from port_common import ROOT, archive_dir, sha256, timestamp, write_json


def main():
    folder = archive_dir("syscall_result_contract")
    native = Pixels()
    assert native("create 1 2 2 0") == "OK"
    assert native("fill 1 0 0 2 2 773834") == "OK"
    lua, names, _, system = registered_providers(overrides={
        "graphics": native, "service": lambda action, value: "1041"})
    module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text("utf8"))
    ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text("utf8"))
    hashes = {name: hash_ for hash_, name in names.items()}
    execute = lua.eval("""function(mod,ops,system,hash,args)
      local code={};local at=0
      for i=#args,1,-1 do code[#code+1]={at,0x800,args[i]};at=at+6 end
      code[#code+1]={at,0x834,hash,#args};at=at+8
      code[#code+1]={at,0x80f,123,0,1};at=at+12
      code[#code+1]={at,0x1b0,32,999,-1};at=at+10
      code[#code+1]={at,0x800,0};code[#code+1]={at+6,0x82b}
      code[#code+1]={1000,0x802,96,1,-2};code[#code+1]={1010,0x82b}
      local script={name='syscall-result.mjo',main=0,functions={{123,1000}},code=code}
      local vm=mod.new{loader=function()return script end,opcodes=ops,syscall=system.invoke}
      vm:begin(script.name);assert(vm:run(100)=='finished')
      return vm.globals[1][999]
    end""")
    for name, hash_, args, expected in (("$grp_point_get", hashes["$grp_point_get"], [1, 0, 0], 773834),
                                       ("system_lcid", 0xf66cd6ea, [], 1041)):
        actual = execute(module, ops, system, hash_, lua.table_from(args))
        assert actual == expected, (name, actual, expected)
    report = {"status": "passed", "timestamp": timestamp(), "checks": 2,
              "scope": "production providers, native pixels, syscall return followed by CALL argument use",
              "source_sha256": sha256(ROOT / "player/runtime/majiro_syscalls.lua"),
              "device_accessed": False, "evidence": str(folder.relative_to(ROOT))}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/syscall_result_contract_tests.json", report)
    print("Syscall result contract: point query and locale pass values to a real following CALL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
