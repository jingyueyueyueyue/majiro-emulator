"""Bounded offline story/menu regression through original scripts and actual resource decoding."""
from pathlib import Path
import argparse
import json
import sys
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert"),
              str(Path(__file__).resolve().parents[1]/"build")]
from audit_startup_resources import Startup
from deploy_player_android import payload
from lua_bridge import table_value
from port_common import ROOT,archive_dir,read_json,write_json,timestamp,sha256


def run_story(game,files,mode,pages,seconds,session=None):
    session=session or Startup(game,files,mode,2000000,35)
    result=session.run()
    row={"game":game["id"],"mode":mode,"startup_stop":result["stop"],"status":"running",
         "pages":0,"clicks":0,"source_selections":[],"source_scripts":[],
         "source_game_files_modified":False,"player_saves_used":False}
    if result["error"]:
        row.update(status="failed",error=result["error"],problem=result["problem"]);return row
    vm,system=session.vm,session.system
    configured=[]
    original_set=system["providers"]["$console_set"]
    def configure(owner,args,*rest):
        configured.append({"script":owner["frame"](owner)["script"]["name"],
                           "offset":owner["current_offset"],"args":[args[i]for i in range(1,6)]})
        return original_set(owner,args,*rest)
    system["providers"]["$console_set"]=configure
    initial=system["state"]["reading_generation"]or 0
    began=time.monotonic();selected=set();clicked_pages=set();last_click=-100000
    pending_click=None
    release_button=None
    returned_menus=set()
    pointer={"x":511.75,"y":287.625}
    deadline=time.monotonic()+seconds
    try:
        for tick in range(50000):
            if time.monotonic()>=deadline:
                row["status"]="time_budget_reached";break
            state=system["state"];generation=state["reading_generation"]or 0
            event=dict(pointer,buttons=0,held_buttons=0)
            areas=state["ui_areas"]
            frames=[frame["script"]["name"]for frame in vm["frames"].values()]
            menu=next((name for name in frames if name in("loadsave.mjo","config.mjo","cgmode.mjo","music.mjo")),None)
            if release_button:
                release_button=None
            elif menu and row["pages"]==0 and menu not in returned_menus and session.clock-last_click>1000:
                event.update(buttons=2,held_buttons=2);release_button=2
                returned_menus.add(menu);last_click=session.clock;row["clicks"]+=1
            elif pending_click:
                event.update(pending_click,buttons=1,held_buttons=1);pointer=dict(pending_click)
                release_button=1
                pending_click=None;last_click=session.clock;row["clicks"]+=1
            elif state["ui_waiting"]and areas and len(areas)>0 and not state["reading_wait"]:
                # First normal title/start button, then first available story
                # choice, always through the source areaevent callback.
                signature=tuple((a["args"][1],a["args"][2],a["args"][3])for a in areas.values())
                if session.clock-last_click>500:
                    available=[a for a in areas.values()if(signature,a["args"][1])not in selected]
                    if not available:row["status"]="menu_boundary_no_untried_controls";break
                    area=min(available,key=lambda a:(a["args"][3],a["args"][2]))
                    a=area["args"]
                    pending_click={"x":a[2]+a[4]/2+.25,"y":a[3]+a[5]/2+.625}
                    event.update(pending_click)
                    row["source_selections"].append({"script":area["script"],"area_id":a[1]})
                    selected.add((signature,a[1]))
            elif state["reading_wait"]and generation not in clicked_pages and session.clock-last_click>250:
                # Let each whole page and any voice completion settle first.
                if not state["media"]["channels"]["voice"]or session.clock-last_click>2000:
                    event.update(buttons=1,held_buttons=1);release_button=1
                    row["clicks"]+=1;clicked_pages.add(generation);last_click=session.clock
            elif(vm["waiting"]and vm["waiting"]["kind"]=="input"
                 and not state["ui_waiting"]and session.clock-last_click>1000):
                # Some source console paths use input_wait outside PAUSE;
                # these are real click boundaries, not stuck script loops.
                event.update(x=511.75,y=287.625,buttons=1,held_buttons=1)
                release_button=1;row["clicks"]+=1;last_click=session.clock
            session.lua.globals()["PLAYER_TICK"](session.clock,session.lua.table_from(event))
            generation=state["reading_generation"]or 0
            row["pages"]=generation-initial
            if row["pages"]>=pages:row["status"]="passed";break
            if vm["status"]=="finished":row["status"]="source_finished";break
            if vm["modal_dialog"]:row["status"]="source_dialog_boundary";break
            if vm["external_checker"]:row["status"]="external_checker_boundary";break
            next_deadline=vm["next_deadline"](vm)
            session.clock=max(session.clock+50,min(next_deadline or session.clock+50,session.clock+500))
            if vm["steps"]>15000000:row["status"]="instruction_budget_reached";break
        else:row["status"]="tick_budget_reached"
    except Exception as error:
        reason=str(error)
        movie_boundary="movie resource missing"in reason and any(
            event.get("kind")=="fixture_movie_metadata_not_available"for event in session.host_events)
        fixture=movie_boundary or any(term in reason for term in("PC metadata fixture","PC fixture audio",
            "PC startup fixture"))
        row.update(status="fixture_boundary"if fixture else"failed",error=reason,problem=table_value(vm["problem"])if vm["problem"]else None)
    row.update(seconds=round(time.monotonic()-began,3),source_scripts=sorted(vm["scripts"].keys()),
        console_config_calls=configured,
        console_configured=bool(system["state"]["console"]["configuration"]),
        steps=int(vm["steps"]),trace=table_value(vm["trace"]),decoded_images=len(session.decoded_images),
        last_frames=[{"script":f["script"]["name"],"offset":f["script"]["code"][f["index"]][1]
                     if f["script"]["code"][f["index"]]else-1}for f in vm["frames"].values()])
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages",type=int,default=30);parser.add_argument("--seconds",type=int,default=45)
    parser.add_argument("--game",action="append");parser.add_argument("--mode",action="append",choices=("original","online","local"))
    args=parser.parse_args();folder=archive_dir("story_entry_regressions");files,roots=payload(folder)
    samples=read_json(ROOT/"config/player_samples.json")["games"]+read_json(ROOT/"config/player_new_samples.json")["games"]
    by_id={game["id"]:game for game in samples}
    report={"schema":1,"timestamp":timestamp(),"status":"running","games":[],
        "device_accessed":False,"real_api_called":False,"player_saves_used":False,
        "scope":"bounded source-driven first-choice storyline regression; full routes are not covered",
        "source_sha256":{str(p.relative_to(ROOT)):sha256(p)for p in(ROOT/"player/runtime").glob("*.lua")},
        "evidence":str(folder.relative_to(ROOT))}
    for root in roots:
        if args.game and root["id"]not in args.game:continue
        prefix=root["directory"]+"/";source={t[len(prefix):]:p for p,t in files if t.startswith(prefix)}
        for mode in(args.mode or("original","online")):
            row=run_story(by_id[root["id"]],source,mode,args.pages,args.seconds)
            report["games"].append(row)
            write_json(ROOT/"reports/story_entry_regressions.json",report);write_json(folder/"report.json",report)
            print(root["id"],mode,row["status"],"pages",row["pages"],row.get("error","")[:180],flush=True)
    report.update(timestamp=timestamp(),status="diagnostic_complete",
        failures=sum(row["status"]=="failed"for row in report["games"]),
        progressed=sum(row["pages"]>0 for row in report["games"]),
        complete_routes_verified=False)
    write_json(ROOT/"reports/story_entry_regressions.json",report);write_json(folder/"report.json",report)
    return 1 if report["failures"]else 0


if __name__=="__main__":raise SystemExit(main())
