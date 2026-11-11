"""Fault-injected production loads with real independent pixel rollback and retry."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

import test_emulator_host_restore as base
from native_pixels import Pixels
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def captured_options(lua,provider):
    return lua.eval("""function(provider)
      local seen={}
      local function find(fn)
        if type(fn)~='function'or seen[fn]then return end;seen[fn]=true
        for i=1,100 do
          local key,value=debug.getupvalue(fn,i);if not key then break end
          if key=='options'then return value end
          local found=find(value);if found then return found end
        end
      end
      local result=assert(find(provider));return result
    end""")(provider)


def run(candidate=None):
    folder=archive_dir("host_load_atomicity")
    source=(candidate/"player/runtime/majiro_save.lua")if candidate else ROOT/"player/runtime/majiro_save.lua"
    original_path=base.player_path
    cases=[]
    faults=("missing_media","missing_progress","invalid_progress_lines",
            "audio_stop_throw","audio_stop_false","movie_detach_throw",
            "graphics_commit_error","audio_replay_throw","clock_throw")
    for fault in faults:
        if candidate:
            base.player_path=lambda name:source if name=="runtime/majiro_save.lua"else original_path(name)
        try:lua,module,system,vm,store,transport,script=base.fixture()
        finally:base.player_path=original_path
        p,state=system["providers"],system["state"]
        opts=captured_options(lua,p["$do_extload"])
        codec=module("runtime/majiro_codec.lua")
        native=Pixels()
        trace=[];control={"fault":None,"tripped":False,"active":False,
                         "audio":"saved","movie":False,"clock":100}
        def trip(name):
            if control["fault"]==name and not control["tripped"]:
                control["tripped"]=True;return True
            return False
        def graphics(request):
            trace.append(request.split("\n")[0])
            if request=="checkpoint_commit"and trip("graphics_commit_error"):
                return"ERR injected commit rejection"
            reply=native(request)
            if reply=="OK":
                if request=="checkpoint_begin":control["active"]=True
                if request in("checkpoint_commit","checkpoint_rollback"):control["active"]=False
            return reply
        def audio(action,spec):
            trace.append("audio:"+action+":"+spec["file"])
            if action=="stop":
                control["audio"]=None
                if trip("audio_stop_throw"):raise RuntimeError("injected stop failure after effect")
                if trip("audio_stop_false"):return False
            elif action=="play":
                control["audio"]=spec["file"]
                if trip("audio_replay_throw"):raise RuntimeError("injected playback failure after effect")
            return action!="completed"
        def movie(action,spec):
            trace.append("movie:"+action)
            if action=="checkpoint_detach":
                control["movie"]=False
                if trip("movie_detach_throw"):raise RuntimeError("injected movie stop failure after effect")
                return 0
            if action in("stop","texture_stop"):
                control["movie"]=False
                return native("release "+str(spec["page"]))
            if action=="texture_play":
                control["movie"]=True
                # The restarted transport can publish a frame before rollback.
                assert native("create 55 2 2 0 32")=="OK"
                assert native("fill 55 0 0 2 2 77")=="OK"
                return 1
            raise AssertionError(action)
        original_clock=opts["set_clock"]
        def set_clock(value):
            control["clock"]=value;original_clock(value)
            if trip("clock_throw"):raise RuntimeError("injected clock failure after effect")
        opts["graphics"],opts["audio"],opts["movie"],opts["set_clock"]=graphics,audio,movie,set_clock
        for command in("create 55 2 2 0 32","fill 55 0 0 2 2 255"):
            assert native(command)=="OK"
        state["pages"][55]=lua.table_from({"width":2,"height":2,"depth":32})
        state["graphics_journal"]=lua.table_from(["create 55 2 2 0 32","fill 55 0 0 2 2 255"])
        state["media"]["channels"]["music"]=lua.table_from({"id":"music","generation":2,
            "file":"saved.ogg","duration_ms":1000,"started":0,"loop":True,"volume":1000})
        p["$save_point"](vm,lua.table());p["$do_extsave"](vm,lua.table())
        document=transport["document"]
        packet=codec["decode"](document)
        if fault=="missing_media":packet["packet"]["state"]["media"]=None
        if fault=="missing_progress":packet["packet"]["state"]["progress"]=None
        if fault=="invalid_progress_lines":packet["packet"]["state"]["progress"]["lines"]="bad"
        transport["document"]=codec["encode"](packet)
        state["console"]["text"]="LIVE"
        vm["globals"][1][123]=777
        state["media"]["channels"]["music"]["file"]="live.ogg"
        state["media"]["channels"]["music"]["generation"]=17
        state["media"]["movie_texture"]=lua.table_from({"page":55,"file":"live.avi","texture":True,
            "started":0,"duration_ms":1000,"repeat_play":True,"range_from":0,"range_to":-1,"volume":1000})
        control.update(audio="live.ogg",movie=True)
        assert native("fill 55 0 0 2 2 99")=="OK"
        identity=lua.eval("""function(s,v)
          local refs={s.console,s.settings,s.media,s.input,s.pages,v.threads,v.frames,v.globals,
            v:frame(),s.media.channels.music,v.context_generation}
          return function()
            return refs[1]==s.console and refs[2]==s.settings and refs[3]==s.media
              and refs[4]==s.input and refs[5]==s.pages and refs[6]==v.threads
              and refs[7]==v.frames and refs[8]==v.globals and refs[9]==v:frame()
              and refs[10]==s.media.channels.music and refs[11]==v.context_generation
          end
        end""")(state,vm)
        encode=lua.eval("function(c,v)return c.encode(c.clone(v))end")
        before_state=encode(codec,state)
        before_vm=encode(codec,vm["export_state"](vm))
        output=folder/(fault+".png")
        def pixels():
            assert native("png 55 "+str(output))=="OK"
            with Image.open(output)as image:return image.convert("RGBA").tobytes()
        before_pixels=pixels()
        control.update(fault=fault,tripped=False)
        ok,error=lua.eval("function(p,v)return pcall(p,v,{})end")(p["$do_extload"],vm)
        assert not ok,fault
        assert not control["active"],(fault,"transaction leaked",error)
        assert encode(codec,state)==before_state,(fault,"state changed after rollback")
        assert encode(codec,vm["export_state"](vm))==before_vm,(fault,"VM changed after rollback")
        assert identity(),(fault,"live aliases changed")
        assert pixels()==before_pixels,(fault,"pixels changed after rollback")
        assert control["audio"]=="live.ogg"and control["movie"],(fault,"media not restored",control)
        assert control["clock"]==100,(fault,"clock not restored")
        p["$do_extsave"](vm,lua.table())
        assert transport["document"]==document,(fault,"prior checkpoint lost")
        control["fault"]=None
        p["$do_extload"](vm,lua.table())
        assert not control["active"]and state["pages"][55]is not None
        assert pixels()!=before_pixels and control["audio"]=="saved.ogg"
        assert not control["movie"],"movie transport still live after successful load"
        cases.append({"fault":fault,"failure_reported":str(error),
            "exact_state_vm_pixels_restored":True,"aliases_retained":True,"media_recovered":True,
            "transaction_closed":True,"next_valid_load_passed":True,
            "texture_page_alias_preserved":True,"trace":trace})
    report={"status":"passed","timestamp":timestamp(),"cases":cases,
        "source":str(source),"sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
        "candidate":bool(candidate),"native_pixels":"independent Windows provider, real page transaction and PNG bytes",
        "media_boundary":"deterministic failure after side effect; real native release vs checkpoint_detach convention",
        "device_accessed":False,"user_saves_accessed":False}
    write_json(folder/"20261111_atomicity.json",report)
    if not candidate:write_json(ROOT/"reports/host_load_atomicity_tests.json",report)
    print("Load atomicity:",len(cases),"fault/alias/retry cases passed;",folder)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate",type=Path)
    return run(parser.parse_args().candidate)


if __name__=="__main__":
    main()
