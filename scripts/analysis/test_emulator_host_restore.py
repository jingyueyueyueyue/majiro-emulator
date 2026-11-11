"""Run independent player save transactions, including suspended audio owners."""
from pathlib import Path
import json
import itertools
import sys

from lua_bridge import create_lua
from player_paths import player_path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, sha256, timestamp, write_json

EVIDENCE = ROOT.parent / "archive/2026-11/20261111_emulator_audit_host"


def fixture(legacy_stride=False):
    lua = create_lua()
    transport = {"document": "", "staged": False, "fail_graphics": False,
                 "loads": 0, "commit_hook": None, "audio": []}

    def module(name):
        return lua.execute(player_path(name).read_text(encoding="utf8"))

    def graphics(request):
        if request == "checkpoint_begin":
            assert not transport["staged"]
            transport["staged"] = True
        elif request in ("checkpoint_commit", "checkpoint_rollback"):
            transport["staged"] = False
            if request == "checkpoint_commit" and transport["commit_hook"]:
                transport["commit_hook"]()
        elif transport["fail_graphics"]:
            return "ERR injected graphics failure"
        return "OK"

    def external(action, value):
        if action == "save":
            transport["document"] = value
            return "OK"
        return "OK\n" + transport["document"]

    def audio(action, spec):
        transport["audio"].append((action, spec["id"], spec["generation"]))
        return action != "completed"

    store = lua.execute("""
      local index={schema=1,identity=string.rep('a',64),slots={}}
      local objects={}
      return {load_index=function()return index end,
        put=function(self,data)local id='object_'..(#objects+1);objects[#objects+1]=data;
          objects[id]=data;return id end,
        get=function(self,id)return assert(objects[id])end,
        object_key=function(self,id)return id end,
        accepts_identity=function(self,id)return id==index.identity end,
        exists=function()return false end,path=function(name)return name end,
        commit=function()end,collect=function()end,objects=objects,index=index}
    """)
    options = lua.table_from({
        "names": module("resources/scripts/syscall_names.lua"), "source_identity": "a"*64,
        "config_files": {}, "config_lines": {}, "environment": {}, "resource_names": {},
        "width": 800, "height": 600,
    }, recursive=True)
    clock = {"now": 100}
    options["modules"] = module
    options["now"] = lambda: clock["now"]
    options["set_clock"] = lambda value: clock.update(now=value)
    options["graphics"] = graphics
    options["external_save"] = external
    options["audio"] = audio
    options["store"] = store
    options["cp932"] = lambda text: text.encode("cp932")
    system = module("runtime/majiro_syscalls.lua")["new"](options)
    script = lua.table_from({
        "name": "host-save.mjo", "source_sha256": "b"*64, "main": 0,
        "functions": [[101, 0]], "code": [[0, 0x82c, 0], [6, 0x82b]],
    }, recursive=True)

    def loader(name):
        transport["loads"] += 1
        return script

    vm = module("runtime/majiro_vm.lua")["new"](lua.table_from({
        "array_3d_legacy_stride":legacy_stride,
        "opcodes": module("resources/scripts/opcodes.lua"), "loader": loader,
        "syscall": system["invoke"], "resume_action": system["resume_action"],
        "string_ops": system["strings"],
    }))
    vm["begin"](vm, script["name"])
    return lua, module, system, vm, store, transport, script


def run():
    checks = []
    for external,legacy_stride in itertools.product((False,True),repeat=2):
        lua, module, system, vm, store, transport, script = fixture(legacy_stride)
        call = lambda name, *args: system["providers"][name](vm, lua.table_from(args))
        lua.eval("""function(vm,state)
          state.media.generation=7
          state.media.channels.music={id='music',file='music.ogg',name='music',
            generation=7,duration_ms=10000,started=0,loop=true,volume=700,
            frequency=22050,channel_volume=500}
          vm.waiting={kind='audio',channel='music',generation=7}
          vm:frame().on_return={kind='event_return',depth=1,
            waiting={kind='audio',channel='music',generation=7}}
          local child=vm:spawn(101,{},false)
          vm.threads[child].waiting={kind='audio',channel='music',generation=6}
          vm.globals[1][123]=456
          local values={};for i=0,23 do values[i]=i end
          vm.globals[1][234]={kind='array',element_type=0,dims={2,3,4},values=values}
        end""")(vm, system["state"])
        call("$save_point")
        call("$do_extsave") if external else call("$do_save", 101)
        system["state"]["media"]["generation"] = 90
        system["state"]["settings"]["musicvolume"] = 600
        system["state"]["settings"]["voice_setup_levels"][1] = 123
        system["state"]["settings"]["voice_musicdown_vol"] = 234
        vm["globals"][1][123] = 999
        before = transport["loads"]
        # Mutate the source exactly after graphics commit: validated private
        # script copies must still permit restore without invoking the loader.
        transport["commit_hook"] = lambda: script.__setitem__("source_sha256", "c"*64)
        call("$do_extload") if external else call("$do_load", 101)
        generation = system["state"]["media"]["channels"]["music"]["generation"]
        assert generation == 91
        assert vm["waiting"]["generation"] == generation
        assert vm["frame"](vm)["on_return"]["waiting"]["generation"] == generation
        assert vm["threads"][2]["waiting"]["generation"] == 6
        assert vm["globals"][1][123] == 456 and not transport["staged"]
        assert transport["loads"] == before + 1
        assert vm["scripts"]["host-save.mjo"]["source_sha256"] == "b"*64
        assert system["state"]["media"]["channels"]["music"]["volume"] == 300
        assert system["state"]["settings"]["voice_setup_levels"][1] == 123
        assert system["state"]["settings"]["voice_musicdown_vol"] == 234
        assert ("frequency", "music", generation) in transport["audio"]
        value=lua.eval("""function(vm)
          vm:push(1);vm:push(2);vm:push(3)
          local bank,key=vm:slot(32+3*2048,234,0,true);return bank[key]
        end""")(vm)
        assert value==(14 if legacy_stride else 23),"load changed 3D indexing profile"
        system["poll"](vm, lua.table())
        assert vm["waiting"] is not None, "restored music wait released while stream is active"
        assert vm["threads"][2]["waiting"] is None, "stale wait acquired restored stream"
        checks.append(("external" if external else "local") +
                      f": legacy_stride={legacy_stride}; audio waits rebound; validated scripts and array addressing retained")

    for external in (False, True):
        for failure in ("script", "graphics"):
            lua, module, system, vm, store, transport, script = fixture()
            call = lambda name, *args: system["providers"][name](vm, lua.table_from(args))
            call("$save_point")
            call("$do_extsave") if external else call("$do_save", 101)
            vm["globals"][1][123] = 777
            if failure == "script":
                script["source_sha256"] = "c"*64
            else:
                transport["fail_graphics"] = True
            try:
                call("$do_extload") if external else call("$do_load", 101)
                raise AssertionError("invalid checkpoint accepted")
            except Exception as error:
                assert "checkpoint accepted" not in str(error)
            assert vm["globals"][1][123] == 777 and not transport["staged"]
            assert not transport["audio"], "failed validation stopped live audio"
            checks.append(("external" if external else "local") +
                          ": " + failure + " rejection preserves VM/audio and rolls back graphics")
    report = {"status": "passed", "timestamp": timestamp(), "runtime_root": "player/runtime",
              "checks": checks, "device_accessed": False,
              "sources": {name: sha256(ROOT/name) for name in (
                  "player/runtime/majiro_save.lua", "player/runtime/majiro_media.lua",
                  "scripts/analysis/test_emulator_host_restore.py")}}
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    write_json(EVIDENCE/"20261111_restore_checks.json", report)
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    run()
