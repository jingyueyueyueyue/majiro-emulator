"""Run normal history/voice flows with real background pixel rendering."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse
import importlib.util
import json
import sys
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from port_common import ROOT,archive_dir,read_json,write_json,timestamp
from audit_startup_resources import Startup
from lua_bridge import table_value
from desktop_pixel_session import PixelSession
from test_story_entry_regressions import run_story
from test_desktop_game_batch import files_for,tick,click,settle
from source_menu_navigation import open_story_history


def legacy_worker(game,folder):
    path=ROOT.parent/"compatibility-restoration/scripts/analysis/test_dialogue_history.py"
    spec=importlib.util.spec_from_file_location("history_flow_fixture",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original=module.HistorySession;instances=[]
    class NativeHistory(original,PixelSession):
        def __init__(self,game,files,mode,patched):
            self.patched=patched;self.voice_plays=0;self.signals=[];self.initial_choice_done=False
            PixelSession.__init__(self,game,files,mode,Path(folder)/game["id"].replace(" ","_")/mode)
            instances.append(self)
        def module(self,path):return Startup.module(self,path)
        def choose(self,identifier):
            if identifier==1 and not self.initial_choice_done:
                self.initial_choice_done=True
                areas=list(self.system["state"]["ui_areas"].values())
                if areas:identifier=min(areas,key=lambda a:(a["args"][3],a["args"][2]))["args"][1]
            return original.choose(self,identifier)
        def control(self):
            result=original.control(self)
            result["name_mode"]=self.vm["globals"][1][3864296330]
            if self.system["state"]["active_ui_script"]=="history.mjo"and not self.snapshots:
                result["render"]=self.capture("history")
            return result
    module.HistorySession=NativeHistory
    directory=Path(game["directory"])
    files={p.relative_to(directory).as_posix():p for p in directory.rglob("*")if p.is_file()}
    rows=[]
    for mode in("original","online"):
        row=module.execute(game,files,mode,True,6)
        stages=row.get("stages",[])
        if row["status"]=="failed"and row.get("stage")in(
            "title_start","normal_story_pages","open_dialogue_history"):
            row["status"]="setup_boundary"
        history=next((s for s in stages if s["stage"]=="open_dialogue_history"),None)
        if row["status"]=="passed"and history and history["voiced_history_entries"]:
            if not history["areas"]:
                row.update(status="failed",error="voiced history has no replay click regions")
        row["real_native_pixels"]=True
        row["native_frames"]=instances[-1].rendered_frames
        rows.append(row)
    return rows


def worker(game,folder,pages=12):
    rows=[]
    class VoicePixels(PixelSession):
        def __init__(self,*args,**kwargs):
            self.voice_plays=0;self.history_layout=[];self.current_redraw=None;self.audio_events=[]
            super().__init__(*args,**kwargs)
        def audio(self,action,spec):
            self.audio_events.append({"action":action,"spec":table_value(spec),"clock":self.clock})
            if action=="play"and(spec["id"]=="voice"or str(spec["id"]).startswith("voice_ch_")):
                self.voice_plays+=1
            return super().audio(action,spec)
        def graphics(self,request):
            if self.current_redraw is not None:
                op=request.split(" ",1)[0]
                if op in("text","glyphs"):
                    head=request.split("\n",1)[0].split()
                    lines=request.split("\n")
                    if op=="glyphs":
                        width=sum(float(step)for step in lines[2].split())
                    else:
                        advance=abs(float(head[5]))or abs(float(head[4]))/2
                        width=sum(advance*(1 if ord(char)<128 else 2)for char in lines[-1])
                    self.current_redraw["draws"].append({
                        "operation":op,"page":int(head[1]),"x":int(head[2]),"y":int(head[3]),
                        "height":int(head[4]),"width":width,"text":lines[-1],
                        "source":self.vm["frame"](self.vm)["script"]["name"],
                        "source_offset":self.vm["current_offset"]})
            return super().graphics(request)
    for mode in("original","online"):
        session=VoicePixels(game,files_for(game),mode,Path(folder)/game["id"].replace(" ","_")/mode)
        row={"game":game["id"],"mode":mode,"status":"running","requested_scene_pages":pages}
        try:
            story=run_story(game,session.files,mode,pages,max(60,pages*.6),session=session)
            if story["status"]!="passed":
                rows.append(row|{"status":"setup_boundary","reason":story.get("error")});continue
            settle(session,1000,3000)
            state=session.system["state"];original_text=state["console"]["text"]
            original_generation=state["reading_generation"]
            providers=session.system["providers"];redraw=providers["$console_redraw"]
            def observe_redraw(vm,args,wants=None):
                if session.current_redraw is not None:
                    session.history_layout.append(session.current_redraw)
                index=len(state["console"]["history"])if args[1]<0 else args[1]
                entry=state["console"]["history"][index]
                session.current_redraw={"has_name":bool(entry and entry["name"]),
                                        "name":entry["name"]if entry else"",
                                        "has_voice":bool(entry and entry["voice"]),"draws":[]}
                return redraw(vm,args,wants)
            providers["$console_redraw"]=observe_redraw
            voice_top=providers["$voicerect_top"]
            def observe_top(vm,args,*rest):
                value=voice_top(vm,args,*rest)
                current=session.current_redraw
                if current is not None:current["voice_top"]=value
                return value
            providers["$voicerect_top"]=observe_top
            row["source_path"]=open_story_history(session)
            for _ in range(100):
                tick(session)
                if state["active_ui_script"]=="history.mjo"and state["ui_waiting"]:break
            else:
                rows.append(row|{"status":"different_source_menu","active_ui_script":state["active_ui_script"]});continue
            row["voiced_history_entries"]=sum(bool(e["voice"])for e in state["console"]["history"].values())
            row["history_control_regions"]=len(state["ui_areas"])
            layout=session.history_layout+([session.current_redraw]if session.current_redraw is not None else[])
            write_json(session.folder/"history_layout.json",layout)
            assert layout and any(item["draws"]for item in layout),"history drawing was not observed"
            named=voiced=0
            for item in layout:
                draws=item["draws"];body=[d for d in draws if d["operation"]=="glyphs"]
                if not body:continue
                names=[d for d in draws if d["operation"]=="text"and d["page"]==body[0]["page"]]
                if item["has_name"]and names:
                    assert draws.index(names[0])<draws.index(body[0]),"history name drawn after dialogue"
                    for name in names:
                        for line in body:
                            separate=(name["x"]+name["width"]<=line["x"]
                                      or line["x"]+line["width"]<=name["x"]
                                      or name["y"]+name["height"]<=line["y"]
                                      or line["y"]+line["height"]<=name["y"])
                            assert separate,"name overlaps history dialogue"
                    named+=1
                if item["has_voice"]and "voice_top"in item:
                    assert item["voice_top"]==body[0]["y"],"voice controls use the pre-name cursor"
                    voiced+=1
            row["layout_checks"]={"named_entries":named,"voice_positions":voiced}
            # Source history numbers its displayed voice records from zero.
            # Scroll/return areas belong to the same input list but are not
            # voice records. Derive the bound from observed voice getters.
            targets=[[a["args"][i]for i in range(1,9)]for a in state["ui_areas"].values()
                     if 0<=a["args"][1]<voiced]
            assert len(targets)==voiced,"displayed voice records lost their source hit regions"
            row["replay_click_regions"]=len(targets)
            write_json(session.folder/"history_layout.json",layout)
            session.current_redraw=None
            row["history_frame"]=session.capture("history")
            before=session.voice_plays
            tick(session,{"pressed":session.lua.table_from({13:True}),"buttons":0},500)
            settle(session,500,4000)
            row["enter_voice_replays"]=session.voice_plays-before
            if row["replay_click_regions"]and row["voiced_history_entries"]:
                assert row["enter_voice_replays"]==1,"Enter must replay the selected single voice once"
                count=0
                for args in targets[:8]:
                    before=session.voice_plays
                    click(session,session.lua.table_from(args))
                    assert session.voice_plays-before==1,"history voice hitbox must play its single voice once"
                    count+=1
                row["pointer_voice_replays"]=count
            for _ in range(2):
                if not any(f["script"]["name"]=="history.mjo"for f in session.vm["frames"].values()):break
                tick(session,{"buttons":2,"held_buttons":2},500)
                tick(session,{"buttons":0,"held_buttons":0},30)
                settle(session,500,2000)
            row["returned_to_story"]=bool(state["reading_wait"])
            row["same_story_text"]=state["console"]["text"]==original_text
            row["same_reading_generation"]=state["reading_generation"]==original_generation
            assert row["returned_to_story"]and row["same_story_text"]and row["same_reading_generation"],"history changed suspended story"
            row["status"]="passed";row["native_frames"]=session.rendered_frames
        except Exception as error:
            row.update(status="failed",error=str(error))
            if session.system:
                state=session.system["state"]
                row["failure_frame"]=session.capture("failure")
                write_json(session.folder/"failure_state.json",{
                    "input":table_value(state["input"]),
                    "areas":table_value(state["ui_areas"]),
                    "voice_rects":table_value(state["console"]["voice_rects"]),
                    "history":table_value(state["console"]["history"]),
                    "settings":{key:state["settings"][key]for key in(
                        "mouse","keyboard_disabled","voice_enabled","voice_volume","auto_mode","fast_mode")},
                    "threads":{str(i):{"focus":t["area_focus"],"waiting":table_value(t["waiting"])}
                               for i,t in session.vm["threads"].items()},
                    "audio":session.audio_events})
        rows.append(row)
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game",action="append")
    parser.add_argument("--pages",type=int,default=12)
    parser.add_argument("--jobs",type=int,default=4)
    args=parser.parse_args()
    folder=archive_dir("history_voice_batch")
    games=[g for g in read_json(ROOT/"config/recovered_library.json")["games"]
           if not args.game or g["id"]in args.game]
    report={"status":"running","timestamp":timestamp(),"cases":[],
            "device_accessed":False,"real_native_pixels":True,
            "requested_scene_pages":args.pages,
            "scope":"source story traversal, real history toolbar, name/body geometry, voice positions, Enter and pointer replay, public return",
            "evidence":str(folder.relative_to(ROOT))}
    with ProcessPoolExecutor(max_workers=args.jobs)as pool:
        for future in as_completed([pool.submit(worker,g,str(folder),args.pages)for g in games]):
            try:rows=future.result()
            except Exception as error:rows=[{"status":"worker_failed","error":str(error)}]
            report["cases"].extend(rows)
            for row in rows:print(json.dumps({k:row.get(k)for k in("game","mode","status","stage","error")},ensure_ascii=True),flush=True)
            write_json(folder/"report.json",report);write_json(ROOT/"reports/history_voice_batch.json",report)
    report.update(status="complete",timestamp=timestamp())
    write_json(folder/"report.json",report);write_json(ROOT/"reports/history_voice_batch.json",report)
    return int(any(r["status"]in("failed","worker_failed")for r in report["cases"]))


if __name__=="__main__":raise SystemExit(main())
