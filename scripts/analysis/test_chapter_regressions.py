"""Batch isolated narrative script entries, preserving source PCs and reporting prerequisite boundaries."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import argparse
import json
import sys
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert"),
              str(Path(__file__).resolve().parents[1]/"build")]
from audit_startup_resources import Startup
from deploy_player_android import payload
from lua_bridge import table_value
from majiro_mjo import Script
from port_common import ROOT,archive_dir,read_json,write_json,timestamp,sha256
from test_story_entry_regressions import run_story

UI_MODULES={"start","console","yazlib","pic","transit","transit_top","menu","buttonmenu",
    "loadsave","load_sysdata","config","pausemenu","history","help","cgmode","music",
    "boss","usertrans","message_box","staffroom"}


def game_chapters(game,files,mode,pages,seconds,publish):
    session=Startup(game,files,mode,2000000,40)
    # Title entry is too early for engines which register console/story
    # libraries only after START. Reach an actual first dialogue through the
    # original menu before taking the reusable isolated snapshot.
    boot=run_story(game,files,mode,1,60,session=session)
    if boot["status"]!="passed":
        return{"game":game["id"],"mode":mode,"status":"story_setup_boundary",
               "error":boot.get("error"),"setup":boot,"chapters":[]}
    candidates=[]
    for key,item in session.resources.items():
        if item.extension!=".mjo"or Path(item.name).stem.lower()in UI_MODULES:continue
        try:
            _,raw=session.raw(item.name)
            script=Script(item.name,raw)
        except Exception:continue
        if script.has_main and sum(row.name=="text"for row in script.instructions)>=3:
            candidates.append((item.name,script.sha256))
    candidates.sort()
    state=session.system["state"];providers=session.system["providers"];vm=session.vm
    # The snapshot lives exclusively in this in-memory fixture. Save restore
    # exercises the same source/hash/frame checks used by real cold loading.
    state["settings"]["save"]=True;state["settings"]["save_menu"]=True
    providers["$save_point"](vm,session.lua.table());providers["$do_save"](vm,session.lua.table_from([777]))
    rows=[]
    for number,(name,digest)in enumerate(candidates):
        began=time.monotonic();row={"chapter":name,"source_sha256":digest,"status":"running","pages":0}
        try:
            providers["$do_load"](vm,session.lua.table_from([777]))
            state=session.system["state"]
            script=vm["load"](vm,name)
            vm["exec_at"](vm,script,script["main"],session.lua.table(),True)
            state["ui_waiting"]=False;state["ui_areas"]=session.lua.table()
            state["reading_wait"]=False;state["reading_context"]=None
            initial=state["reading_generation"]or 0
            last_generation=initial;last_click=-100000;release=False
            for tick in range(2000):
                if time.monotonic()-began>=seconds:row["status"]="time_budget";break
                event={"x":511.75,"y":287.625,"buttons":0,"held_buttons":0}
                generation=state["reading_generation"]or 0
                if release:release=False
                elif state["reading_wait"]and generation>last_generation and session.clock-last_click>=500:
                    event.update(buttons=1,held_buttons=1);release=True
                    last_generation=generation;last_click=session.clock
                session.lua.globals()["PLAYER_TICK"](session.clock,session.lua.table_from(event))
                row["pages"]=(state["reading_generation"]or 0)-initial
                if row["pages"]>=pages:row["status"]="passed";break
                if vm["modal_dialog"]:row["status"]="dialog_boundary";break
                if vm["external_checker"]:row["status"]="external_checker_boundary";break
                if state["ui_waiting"]and not state["reading_wait"]:
                    row["status"]="choice_boundary";break
                if vm["status"]=="finished":row["status"]="source_finished";break
                deadline=vm["next_deadline"](vm)
                session.clock=max(session.clock+50,min(deadline or session.clock+50,session.clock+500))
            else:row["status"]="tick_budget"
        except Exception as error:
            reason=str(error)
            prerequisite=any(term in reason for term in(
                "console destination missing","sprite not found","sprite source page missing",
                "picture dimensions missing","event function missing","unresolved function"))
            movie_boundary="movie resource missing"in reason and any(
                event.get("kind")=="fixture_movie_metadata_not_available"for event in session.host_events)
            fixture=movie_boundary or any(term in reason for term in("PC metadata fixture","PC fixture audio",
                "PC startup fixture","PC fixture does not"))
            row.update(status="fixture_boundary"if fixture else"needs_prerequisite_state"if prerequisite else"failed",
                error=reason[:2500],problem=table_value(vm["problem"])if vm["problem"]else None,
                trace=table_value(vm["trace"])[-12:])
        row["seconds"]=round(time.monotonic()-began,3);rows.append(row)
        if number%10==0 or row["status"]in("failed","needs_prerequisite_state")or number+1==len(candidates):
            publish({"game":game["id"],"mode":mode,"status":"running",
                     "total_chapters":len(candidates),"completed":len(rows),"chapters":rows})
    result={"game":game["id"],"mode":mode,"status":"completed",
        "total_chapters":len(candidates),"completed":len(rows),"chapters":rows}
    publish(result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game",action="append");parser.add_argument("--mode",default="original",choices=("original","online","local"))
    parser.add_argument("--pages",type=int,default=3);parser.add_argument("--seconds",type=float,default=2.5)
    parser.add_argument("--jobs",type=int,default=3);args=parser.parse_args()
    folder=archive_dir("chapter_regressions");files,roots=payload(folder)
    samples=read_json(ROOT/"config/player_samples.json")["games"]+read_json(ROOT/"config/player_new_samples.json")["games"]
    by_id={game["id"]:game for game in samples};tasks=[]
    for root in roots:
        if args.game and root["id"]not in args.game:continue
        prefix=root["directory"]+"/";source={target[len(prefix):]:path for path,target in files if target.startswith(prefix)}
        tasks.append((by_id[root["id"]],source))
    report={"schema":1,"timestamp":timestamp(),"status":"running","games":[],
        "mode":args.mode,"pages_per_chapter":args.pages,"seconds_per_chapter":args.seconds,
        "device_accessed":False,"player_saves_used":False,"real_api_called":False,
        "scope":"every narrative MJO main entry from isolated title snapshot; prerequisite flags are not invented",
        "evidence":str(folder.relative_to(ROOT)),
        "source_sha256":{str(p.relative_to(ROOT)):sha256(p)for p in(ROOT/"player/runtime").glob("*.lua")}}
    def publish(row):
        write_json(folder/(row["game"].replace(" ","_")+".json"),row)
    with ThreadPoolExecutor(max_workers=args.jobs)as pool:
        futures={pool.submit(game_chapters,game,source,args.mode,args.pages,args.seconds,publish):game for game,source in tasks}
        for future in as_completed(futures):
            game=futures[future]
            try:row=future.result()
            except Exception as error:row={"game":game["id"],"status":"fixture_failure","error":str(error),"chapters":[]}
            report["games"].append(row)
            print(row["game"],"chapters",len(row["chapters"]),
                "failed",sum(c["status"]=="failed"for c in row["chapters"]),
                "prerequisites",sum(c["status"]=="needs_prerequisite_state"for c in row["chapters"]),flush=True)
            write_json(ROOT/"reports/chapter_regressions.json",report);write_json(folder/"report.json",report)
    counts={}
    for game in report["games"]:
        for chapter in game["chapters"]:counts[chapter["status"]]=counts.get(chapter["status"],0)+1
    report.update(status="diagnostic_complete",timestamp=timestamp(),summary=counts,
        game_failures=[row["game"]for row in report["games"]if row["status"]!="completed"],
        full_routes_verified=False,source_changed_during_run=any(sha256(ROOT/name)!=digest for name,digest in report["source_sha256"].items()))
    write_json(ROOT/"reports/chapter_regressions.json",report);write_json(folder/"report.json",report)
    print("Chapter results:",json.dumps(counts),flush=True)
    return 1 if counts.get("failed",0)or report["game_failures"]or report["source_changed_during_run"]else 0


if __name__=="__main__":raise SystemExit(main())
