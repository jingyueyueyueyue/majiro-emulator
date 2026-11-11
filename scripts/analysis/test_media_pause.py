"""Pending music controls, real completion waits and paused replay boundaries."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers,source_identity
from lua_bridge import table_value

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    before=source_identity();lua_ref={};clock={"ms":0};events=[];tracks={}
    def audio(action,spec):
        name=spec["id"];track=tracks.get(name)
        if action=="play":
            tracks[name]={"started":clock["ms"]-(spec["offset_ms"]or 0),
                "duration":spec["duration_ms"],"loop":bool(spec["loop"]),"paused":None}
        elif action=="stop":tracks.pop(name,None)
        elif action=="playing":
            return bool(track and(track["paused"]is not None or track["loop"]
                or clock["ms"]-track["started"]<track["duration"]))
        elif action=="pause":
            if not track:return False
            track["paused"]=clock["ms"]
        elif action=="resume":
            if not track:return False
            if track["paused"]is not None:
                track["started"]+=clock["ms"]-track["paused"];track["paused"]=None
        elif action not in("volume","pan","fadeout"):raise ValueError("unexpected audio action")
        events.append({"action":action,"id":name,"file":spec["file"],"offset_ms":spec["offset_ms"]})
        return True
    def asset(name):
        return lua_ref["lua"].table_from({"file":name,"duration_ms":100})
    lua,_,_,system=registered_providers(overrides={
        "now":lambda:clock["ms"],"audio":audio,"media_asset":asset,
        "resource_names":{"fixture.ogg":True},"environment":{"MUSICVOL":"1000"}})
    lua_ref["lua"]=lua
    mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
    vm=lua.eval("""
    function(mod,ops)
      local script={name='audio-wait',main=0,functions={},code={{0,0x82c,0}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=function()error('unexpected wait fixture syscall')end}
      vm:begin(script.name);return vm
    end
    """)(mod,ops)
    def call(name,*args):return system["providers"][name](vm,lua.table_from(args))
    def poll(at):
        clock["ms"]=at;system["poll"](vm,lua.table())
    checks=[]
    call("$music","fixture",0)
    call("$music_pause");call("$music_resume");call("$music_pause")
    assert[len(events)==1, bool(system["state"]["media"]["channels"]["music"]["pause_requested"])]==[True,True]
    poll(0)
    assert len(events)==1 and not system["state"]["media"]["channels"]["music"]["pause_requested"]
    checks.append("pause/resume/pause coalesces pending requests without replacing the stream")
    poll(20);call("$music_pause")
    assert len(events)==1
    poll(20)
    assert events[-1]["action"]=="pause"and call("$music_stat")==1
    call("$music_wait")
    poll(500)
    assert vm["waiting"]and vm["next_deadline"](vm)is None and call("$music_get_fn$")=="fixture"
    checks.append("paused music remains active and an audio wait cannot expire by wall-clock alone")
    call("$music_resume");poll(500)
    assert events[-1]["action"]=="resume"and vm["waiting"]
    assert system["state"]["media"]["channels"]["music"]["started"]==480
    poll(579);assert vm["waiting"]
    poll(580);assert vm["waiting"]is None and call("$music_stat")==0
    checks.append("resume preserves the playhead and waits for physical completion")
    call("$music","fixture",1);call("$music_wait")
    poll(10000);assert vm["waiting"]
    call("$music_stop");poll(10000);assert vm["waiting"]is None
    checks.append("loop waits remain cancellable instead of reporting unsupported semantics")
    call("$music","fixture",0);call("$music_wait")
    call("$music","fixture",1);poll(10000);assert vm["waiting"]is None
    checks.append("replacing a stream releases waits owned by the previous generation")
    codec=lua.execute((ROOT/"player/runtime/majiro_codec.lua").read_text(encoding="utf-8"))
    replay=system["state"]["media"]["replay"]
    for elapsed,expected_file,expected_offset in((50,"intro.ogg",50),(125,"loop.ogg",25),
                                                 (375,"loop.ogg",25)):
        spec=lua.table_from({"id":"music","file":"intro.ogg","loop_file":"loop.ogg",
            "duration_ms":100,"loop_duration_ms":250,"loop":True,"started":10000-elapsed,
            "paused_at":10000,"pause_requested":True})
        saved=codec["decode"](codec["encode"](spec))
        replay(vm,saved)
        assert events[-2]["action"]=="play"and events[-2]["file"]==expected_file
        assert events[-2]["offset_ms"]==expected_offset and events[-1]["action"]=="pause"
        assert saved["file"]=="intro.ogg"and saved["loop_file"]=="loop.ogg"
    checks.append("paused save replay selects the intro/loop phase and restores its offset")
    report={"schema":1,"timestamp":timestamp(),"status":"passed","checks":checks,
        "source_identity":before,"source_changed_during_test":source_identity()!=before,
        "scope":"shared runtime with deterministic transport; actual device decoding and seeking remain separate"}
    if report["source_changed_during_test"]:raise RuntimeError("runtime changed during media regression")
    folder=archive_dir("media_pause")
    write_json(folder/(folder.name[:8]+"_media_pause.json"),report)
    write_json(ROOT/"reports/media_pause_tests.json",report)
    print("media pause:",len(checks),"request/wait/replay checks passed")
    return 0


if __name__=="__main__":raise SystemExit(run())
