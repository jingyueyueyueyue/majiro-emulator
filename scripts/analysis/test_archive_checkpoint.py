"""Resource activation/precedence participates in real Lua load transactions."""
from pathlib import Path
import json
import subprocess
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / part)
                for part in ("analysis", "convert", "build")]
import test_emulator_host_restore as base
from test_host_load_atomicity import captured_options
from test_audio_preparation_cleanup import method
from build_player_core import msvc_environment
from port_common import ROOT, archive_dir, sha256, timestamp, write_json


def case(external, fault):
    lua, module, system, vm, store, transport, script = base.fixture()
    options = captured_options(lua, system["providers"]["$do_load"])
    codec = module("runtime/majiro_codec.lua")
    saved = [{"name": "patch.arc", "active": False}, {"name": "base.arc", "active": True}]
    current = [{"name": "base.arc", "active": True}, {"name": "patch.arc", "active": True}]
    host = {"state": lua.table_from(saved, recursive=True), "fail": False,
            "key": None, "key_fail": False}
    trace = []
    def archives(action, value=None):
        trace.append(action)
        if action == "snapshot":
            return codec["clone"](host["state"])
        if action == "validate":
            assert len(value) == 2
            assert sorted(value[i]["name"] for i in (1, 2)) == ["base.arc", "patch.arc"]
        if action == "restore":
            host["state"] = codec["clone"](value)
            if host["fail"]:
                host["fail"] = False
                raise IOError("injected resource metadata refresh failure after mount")
        return 0
    options["archive_state"] = archives
    def picture_key(value):
        host["key"]=value
        if host["key_fail"]:
            host["key_fail"]=False
            raise IOError("injected picture key refresh failure after effect")
    options["picture_key"]=picture_key
    call = lambda name, *args: system["providers"][name](vm, lua.table_from(args))
    call("$pic_key_set","saved-key")
    call("$save_point")
    call("$do_extsave") if external else call("$do_save", 101)
    host["state"] = lua.table_from(current, recursive=True)
    call("$pic_key_set","live-key")
    host["fail"] = fault == "resources"
    host["key_fail"] = fault == "key"
    transport["fail_graphics"] = fault == "graphics"
    if fault:
        try:
            call("$do_extload") if external else call("$do_load", 101)
        except Exception as error:
            expected = {"resources":"metadata refresh","key":"picture key refresh",
                        "graphics":"graphics staging reset failed"}[fault]
            assert expected in str(error), error
        else:
            raise AssertionError("resource load failure accepted")
        assert codec["encode"](host["state"]) == codec["encode"](lua.table_from(current, recursive=True))
        assert not transport["staged"]
        assert host["key"]=="live-key" and system["state"]["picture_key"]=="live-key"
        transport["fail_graphics"] = False
    call("$do_extload") if external else call("$do_load", 101)
    assert codec["encode"](host["state"]) == codec["encode"](lua.table_from(saved, recursive=True))
    assert "validate" in trace
    assert host["key"]=="saved-key" and system["state"]["picture_key"]=="saved-key"
    return {"external": external, "fault": fault, "status": "passed"}


def native_order(folder):
    production = ROOT / "player/android/game_host.cpp"
    body = method(production.read_text("utf8"), "std::vector<size_t> checked_archive_order(")
    source = r"""
#include <algorithm>
#include <cassert>
#include <cctype>
#include <set>
#include <string>
#include <stdexcept>
#include <vector>
struct Source{std::string name;};
std::vector<Source> sources={{"A.arc"},{"b.arc"},{"c.arc"}};
std::string key(std::string value){for(auto& c:value)c=char(std::tolower((unsigned char)c));return value;}
void need(bool good,const std::string& why){if(!good)throw std::runtime_error(why);}
// METHOD
int main(){
 assert(checked_archive_order({{"B.ARC",false},{"a.arc",true}})==std::vector<size_t>({1,0,2}));
 assert(checked_archive_order({})==std::vector<size_t>({0,1,2}));
 for(const auto& rows:std::vector<std::vector<std::pair<std::string,bool>>>{
     {{"unknown.arc",true}},{{"A.arc",true},{"a.ARC",false}},
     {{"a.arc",true},{"b.arc",true},{"c.arc",true},{"extra.arc",true}}}){
  bool failed=false;try{checked_archive_order(rows);}catch(const std::runtime_error&){failed=true;}
  assert(failed);
 }
}
"""
    path = folder / "archive_order.cpp"
    path.write_text(source.replace("// METHOD", body), encoding="utf8")
    env = msvc_environment()
    compiler = Path(env["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    binary = folder / "archive_order.exe"
    for label, command in (("compile", [str(compiler), "/nologo", "/std:c++20", "/EHsc",
                              str(path), "/Fe:" + str(binary), "/Fo:" + str(folder / "archive_order.obj")]),
                           ("run", [str(binary)])):
        result = subprocess.run(command, cwd=folder, env=env, capture_output=True, timeout=60)
        (folder / (label + ".txt")).write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (result.stdout + result.stderr).decode("utf8", "replace")
    return {"production_sha256": sha256(production), "checks": 5}


def main():
    folder = archive_dir("archive_checkpoint")
    rows = [case(external, fault) for external in (False, True)
            for fault in (None, "resources", "key", "graphics")]
    report = {"status": "passed", "timestamp": timestamp(), "cases": rows,
              "native": native_order(folder), "device_accessed": False,
              "evidence": str(folder.relative_to(ROOT))}
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/archive_checkpoint_tests.json", report)
    print("Archive checkpoint: 8 resource/key load/rollback cases and 5 production order checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
