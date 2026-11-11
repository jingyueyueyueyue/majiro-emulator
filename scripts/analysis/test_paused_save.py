"""Save and cold-load paused audio through the actual portable slot modules."""
from __future__ import annotations
from player_paths import player_path
import hashlib
import json
from pathlib import Path
import sys

from audit_player_semantics import source_identity
from lua_bridge import create_lua

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    before=source_identity();files={};cases=[]
    def host(request):
        if request.startswith("sha256\n"):
            return"OK "+hashlib.sha256(request[7:].encode("utf-8")).hexdigest()
        operation,_,rest=request.partition(" ");name,_,data=rest.partition("\n")
        if operation=="save_exists":return"OK "+str(int(name in files))
        if operation=="save_read":return"OK data\n"+files[name]if name in files else"OK absent"
        if operation=="save_write":files[name]=data;return"OK"
        if operation=="save_delete":files.pop(name,None);return"OK"
        raise ValueError("unexpected save fixture operation: "+operation)
    def fixture():
        lua=create_lua();clock={"ms":0};events=[]
        def module(name):return lua.execute(player_path(name).read_text(encoding="utf-8"))
        codec=module("runtime/majiro_codec.lua")
        store=module("runtime/majiro_store.lua")["new"](codec,host,"a"*64)
        def audio(action,spec):
            events.append({"action":action,"file":spec["file"],"offset":spec["offset_ms"],
                           "paused":spec["paused_at"]is not None})
            return True
        def graphics(request):
            if request not in ("reset_pages","checkpoint_begin","checkpoint_commit","checkpoint_rollback"):
                raise ValueError("fixture has no graphics to replay: "+request)
            return"OK"
        options=lua.table_from({"names":module("resources/scripts/syscall_names.lua"),
            "config_files":{},"config_lines":{},"environment":{"MUSICVOL":"1000"},
            "resource_names":{"intro.ogg":True,"loop.ogg":True},"width":1280,"height":720,
            "source_identity":"a"*64},recursive=True)
        options["store"]=store;options["modules"]=module;options["now"]=lambda:clock["ms"]
        options["set_clock"]=lambda value:clock.update(ms=value)
        options["audio"]=audio;options["graphics"]=graphics
        options["media_asset"]=lambda name:lua.table_from({"file":name,
            "duration_ms":1000 if name=="intro.ogg"else 250})
        options["cp932"]=lambda text:text.encode("cp932")
        system=module("runtime/majiro_syscalls.lua")["new"](options)
        vm=lua.eval("""
        function(mod,ops,system)
          local script={name='paused-save-fixture.mjo',source_sha256=string.rep('b',64),
            main=0,functions={},code={{0,0x82c,0}}}
          local vm=mod.new{opcodes=ops,loader=function()return script end,
            syscall=system.invoke,resume_action=system.resume_action,string_ops=system.strings}
          vm:begin(script.name);return vm
        end
        """)(module("runtime/majiro_vm.lua"),module("resources/scripts/opcodes.lua"),system)
        def call(name,*args):return system["providers"][name](vm,lua.table_from(args))
        def poll(at):clock["ms"]=at;system["poll"](vm,lua.table())
        return lua,clock,events,system,vm,call,poll
    for slot,elapsed,extended in((1,25,False),(2,1200,True),(3,1500,True)):
        lua,clock,events,system,vm,call,poll=fixture()
        if extended:call("$extmusic","intro","loop")
        else:call("$music","intro",0)
        poll(elapsed);call("$music_pause");poll(elapsed)
        call("$grp_home",slot*7,-slot*3)
        clock["ms"]=5000;call("$save_point");call("$do_save",slot)
        call("$music_stop")
        fresh,clock2,events2,restored,vm2,load,poll2=fixture()
        load("$do_load",slot)
        viewport=restored["state"]["screen"]
        assert viewport["home_x"]==slot*7 and viewport["home_y"]==-slot*3
        restored["input"]["sample"](fresh.table_from({"x":50,"y":60}))
        assert load("$curpos_x")==50-slot*7 and load("$curpos_y")==60+slot*3
        music=restored["state"]["media"]["channels"]["music"]
        assert music and music["paused_at"]==elapsed and music["pause_requested"]
        plays=[event for event in events2 if event["action"]=="play"]
        assert len(plays)==1 and plays[0]["paused"]
        expected_file="loop.ogg"if extended and elapsed>=1000 else"intro.ogg"
        expected_offset=(elapsed-1000)%250 if expected_file=="loop.ogg"else elapsed
        assert plays[0]["file"]==expected_file and plays[0]["offset"]==expected_offset
        assert events2[-1]["action"]=="pause"
        load("$music_resume");poll2(6000)
        assert events2[-1]["action"]=="resume"and music["paused_at"]is None
        assert 6000-music["started"]==elapsed
        cases.append({"slot":slot,"saved_playhead_ms":elapsed,"phase":expected_file,
                      "seek_ms":expected_offset,"paused_after_cold_load":True,
                      "screen_home_after_cold_load":[viewport["home_x"],viewport["home_y"]]})
    report={"schema":1,"timestamp":timestamp(),"status":"passed","cases":cases,
        "source_identity":before,"source_changed_during_test":source_identity()!=before,
        "scope":"portable save codec/store/slot cold restore, paused one-shot and intro/loop audio",
        "limits":["in-memory I/O fixture; original Windows save import and physical seeking remain separate"]}
    if report["source_changed_during_test"]:raise RuntimeError("runtime changed during paused save checks")
    folder=archive_dir("paused_save")
    write_json(folder/(folder.name[:8]+"_paused_save.json"),report)
    write_json(ROOT/"reports/paused_save_tests.json",report)
    print("paused portable saves:",len(cases),"cold-load checks passed")
    return 0


if __name__=="__main__":raise SystemExit(run())
