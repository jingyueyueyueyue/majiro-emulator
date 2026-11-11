"""Reject transactional journals and preserve rollback across combined media failures."""
from pathlib import Path
import re
import hashlib
import sys

ROOT=Path(__file__).resolve().parents[2]
FOLDER=None
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT/"scripts/analysis"),str(ROOT/"scripts/convert")]
import test_emulator_host_restore as base
from test_host_load_atomicity import captured_options
from native_pixels import Pixels
from PIL import Image
from port_common import timestamp,archive_dir,write_json


def journal_case(command):
    lua,module,system,vm,store,transport,script=base.fixture()
    p,state=system["providers"],system["state"]
    codec=module("runtime/majiro_codec.lua")
    options=captured_options(lua,p["$do_extload"])
    native=Pixels()
    trace=[]
    def graphics(request):
        trace.append(request.split("\n")[0])
        return native(request)
    options["graphics"]=graphics
    for request in("create 55 2 2 0 32","fill 55 0 0 2 2 255"):
        assert native(request)=="OK"
    state["pages"][55]=lua.table_from({"width":2,"height":2,"depth":32})
    state["graphics_journal"]=lua.table_from(["create 55 2 2 0 32","fill 55 0 0 2 2 255"])
    p["$save_point"](vm,lua.table());p["$do_extsave"](vm,lua.table())
    valid=transport["document"]
    packet=codec["decode"](valid)
    packet["graphics"]=lua.table_from([
        "create 55 2 2 0 32","fill 55 0 0 2 2 255",command,"fill 55 invalidpixel"])
    transport["document"]=codec["encode"](packet)
    assert native("fill 55 0 0 2 2 99")=="OK"
    path=FOLDER/("20261111_"+command+".png")
    def pixels():
        assert native("png 55 "+str(path))=="OK"
        with Image.open(path)as image:return image.convert("RGBA").tobytes()
    before=pixels()
    ok,error=lua.eval("function(f,v)return pcall(f,v,{})end")(p["$do_extload"],vm)
    after=pixels()
    result={"case":"journal_"+command+"_then_failure","load_accepted":ok,
        "error":str(error),"old_pixels_preserved":before==after,"failure_trace":list(trace)}
    assert not ok
    assert before==after and not trace,result
    transport["document"]=valid
    p["$do_extload"](vm,lua.table())
    result["next_valid_load_succeeded"]=True
    result["retry_trace"]=trace[len(result["failure_trace"]):]
    return result


def paused_case(rollback):
    lua,module,system,vm,store,transport,script=base.fixture()
    p,state=system["providers"],system["state"]
    options=captured_options(lua,p["$do_extload"])
    saved={"id":"music","generation":3,"file":"saved.ogg","started":0,
           "duration_ms":1000,"loop":True,"volume":1000}
    if not rollback:saved.update(paused_at=50,pause_requested=True)
    state["media"]["channels"]["music"]=lua.table_from(saved)
    p["$save_point"](vm,lua.table());p["$do_extsave"](vm,lua.table())
    live={**saved,"file":"live.ogg","generation":19}
    if rollback:live.update(paused_at=75,pause_requested=True)
    else:live.pop("paused_at",None);live.pop("pause_requested",None)
    state["media"]["channels"]["music"]=lua.table_from(live)
    control={"reject_pause":True,"commit_rejected":False,"trace":[]}
    original_graphics=options["graphics"]
    def graphics(request):
        if rollback and request=="checkpoint_commit"and not control["commit_rejected"]:
            control["commit_rejected"]=True;return"ERR injected commit failure"
        return original_graphics(request)
    def audio(action,spec):
        control["trace"].append([action,spec["file"]])
        if action=="pause"and control["reject_pause"]:return False
        return action!="completed"
    options["graphics"],options["audio"]=graphics,audio
    outcome=lua.eval("function(f,v)local ok,why=pcall(f,v,{});return ok,tostring(why)end")(p["$do_extload"],vm)
    ok,error=outcome
    result={"case":"commit_failure_and_rollback_pause_false"if rollback else"saved_pause_false",
            "load_accepted":ok,"error":error,"logical_channel_exists":state["media"]["channels"]["music"]is not None,
            "transaction_active":transport["staged"],"failure_trace":list(control["trace"])}
    assert not transport["staged"]
    assert not ok and "pause" in error,result
    if rollback:assert "rollback failed" in error,result
    assert state["media"]["channels"]["music"]["file"]=="live.ogg",result
    control["reject_pause"]=False
    p["$do_extload"](vm,lua.table())
    result["next_valid_load_succeeded"]=True
    result["retry_trace"]=control["trace"][len(result["failure_trace"]):]
    return result


def double_compensation_case():
    lua,module,system,vm,store,transport,script=base.fixture()
    p,state=system["providers"],system["state"]
    options=captured_options(lua,p["$do_extload"])
    p["$save_point"](vm,lua.table());p["$do_extsave"](vm,lua.table())
    lua.eval("""function(s,v)
      local shared={value='live'};shared.self=shared
      s.phase4_shared=shared;v.globals[1][123]=shared
      s.media.channels.music={id='music',file='live.ogg',generation=9,started=0,duration_ms=1000,loop=true}
      s.media.movie_texture={page=55,texture=true,file='live.avi'}
    end""")(state,vm)
    before=lua.eval("function(s,v,c)return c.encode(c.clone{s=s,v=v:export_state()})end")(state,vm,module("runtime/majiro_codec.lua"))
    original_graphics=options["graphics"]
    control={"fault":True};trace=[]
    def graphics(request):
        trace.append(request)
        if request=="checkpoint_commit"and control["fault"]:return"ERR primary commit failure"
        return original_graphics(request)
    def audio(action,spec):
        if action=="play"and control["fault"]:raise RuntimeError("second failure: audio reopen")
        return True
    def movie(action,spec):
        if action=="texture_play"and control["fault"]:raise RuntimeError("third failure: movie reopen")
        return 1
    options["graphics"],options["audio"],options["movie"]=graphics,audio,movie
    ok,error=lua.eval("function(f,v)local ok,why=pcall(f,v,{});return ok,tostring(why)end")(p["$do_extload"],vm)
    assert not ok and "audio rollback" in error and "movie rollback" in error,error
    assert not transport["staged"]
    after=lua.eval("function(s,v,c)return c.encode(c.clone{s=s,v=v:export_state()})end")(state,vm,module("runtime/majiro_codec.lua"))
    assert before==after
    assert lua.eval("function(s,v)return s.phase4_shared==v.globals[1][123]and s.phase4_shared.self==s.phase4_shared end")(state,vm)
    control["fault"]=False
    p["$do_extload"](vm,lua.table())
    return{"case":"commit_error_plus_two_compensation_errors_and_shared_cycle",
        "status":"passed","all_errors_reported":error,"state_vm_graph_equal":True,
        "shared_cycle_preserved":True,"transaction_closed":True,"next_valid_load_succeeded":True,
        "note":"Unavailable media recovery is explicitly reported; physical playback recovery is not claimed."}


def main():
    global FOLDER
    FOLDER=archive_dir("host_restore_combinations")
    source_names=("player/runtime/majiro_save.lua","player/runtime/majiro_media.lua")
    before={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()for n in source_names}
    def operations(path,name):
        text=(ROOT/path).read_text(encoding="utf8")
        block=text.split("local "+name+"={",1)[1].split("}",1)[0]
        return set(re.findall(r"(\w+)=true",block))
    allowed=operations(source_names[0],"replay_operations")
    assert allowed==operations("player/runtime/majiro_syscalls.lua","pixel_mutations")
    rows=[journal_case(command)for command in("checkpoint_commit","checkpoint_rollback")]
    rows += [paused_case(rollback)for rollback in(False,True)]
    rows.append(double_compensation_case())
    assert before=={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()for n in source_names}
    report={"status":"passed","timestamp":timestamp(),"cases":rows,
            "source_sha256":before,"sources_stable":True,"journal_operation_contract":len(allowed),
            "compiled":False,"device_accessed":False,
            "scope":"formal Lua and real independent pixels; combined commit/pause/reopen faults with retry"}
    write_json(FOLDER/"20261111_restore_combinations.json",report)
    write_json(ROOT/"reports/host_restore_combinations_tests.json",report)
    print("Restore combinations: 5 cases and 30-operation journal contract passed")
    return report


if __name__=="__main__":
    main()
