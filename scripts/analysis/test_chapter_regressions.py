"""Batch isolated narrative script entries, preserving source PCs and reporting prerequisite boundaries."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor,as_completed
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


def game_chapters(game,files,mode,pages,seconds,publish,pixel_folder=None,wanted=None,predecessors=None):
    if pixel_folder is not None:
        from desktop_pixel_session import PixelSession
        session=PixelSession(game,files,mode,pixel_folder,maximum=2000000,seconds=60)
    else:session=Startup(game,files,mode,2000000,40)
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
        if wanted is not None and item.name not in wanted:continue
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
    polled_keys=set()
    read_key=providers["$kb_read"]
    def observe_key(owner,args,*rest):
        if args[1]in(13,27,32):polled_keys.add(args[1])
        return read_key(owner,args,*rest)
    providers["$kb_read"]=observe_key
    for number,(name,digest)in enumerate(candidates):
        began=time.monotonic();row={"chapter":name,"source_sha256":digest,"status":"running","pages":0}
        try:
            providers["$do_load"](vm,session.lua.table_from([777]))
            state=session.system["state"]
            predecessor=(predecessors or{}).get(name)
            script=vm["load"](vm,predecessor or name)
            vm["exec_at"](vm,script,script["main"],session.lua.table(),True)
            if predecessor:
                row["predecessor"]=predecessor
                target=vm["load"](vm,name)
                vm["frame"](vm)["on_return"]=session.lua.table_from({
                    "kind":"enter","script":target["name"],"offset":target["main"]})
            state["ui_waiting"]=False;state["ui_areas"]=session.lua.table()
            state["reading_wait"]=False;state["reading_context"]=None
            initial=state["reading_generation"]or 0
            target_reached=not predecessor
            polled_keys.clear()
            last_generation=initial;last_click=-100000;release=False
            pending_choice=None
            selected_areas=set()
            row["source_selections"]=[]
            for tick in range(20000):
                if time.monotonic()-began>=seconds:row["status"]="time_budget";break
                event={"x":511.75,"y":287.625,"buttons":0,"held_buttons":0}
                if not target_reached and any(f["script"]["name"]==name for f in vm["frames"].values()):
                    target_reached=True;initial=state["reading_generation"]or 0
                generation=state["reading_generation"]or 0
                if release:release=False
                elif pending_choice:
                    event.update(pending_choice,buttons=1,held_buttons=1);release=True
                    pending_choice=None;last_click=session.clock
                elif state["ui_waiting"]and not state["reading_wait"]and state["ui_areas"]and len(state["ui_areas"]):
                    available=[area for area in state["ui_areas"].values()if area["args"][4]>0 and area["args"][5]>0]
                    if available and session.clock-last_click>=500:
                        signature=tuple(sorted((area["script"],*[area["args"][i]for i in range(1,6)])
                                               for area in available))
                        untried=[area for area in available if(signature,area["args"][1])not in selected_areas]
                        if not untried:
                            selected_areas={key for key in selected_areas if key[0]!=signature}
                            untried=available
                        area=min(untried,key=lambda area:(area["args"][3],area["args"][2]))
                        a=area["args"]
                        selected_areas.add((signature,a[1]))
                        row["source_selections"].append({"script":area["script"],"area_id":a[1]})
                        pending_choice={"x":a[2]+a[4]/2+.25,"y":a[3]+a[5]/2+.625}
                        event.update(pending_choice)
                elif(vm.waiting and vm.waiting["kind"]=="input")or(
                    state["reading_wait"]and generation>last_generation and session.clock-last_click>=500):
                    event.update(buttons=1,held_buttons=1);release=True
                    last_generation=generation;last_click=session.clock
                elif polled_keys and not state["reading_wait"]and not state["ui_waiting"]and session.clock-last_click>=1000:
                    event["pressed"]=session.lua.table_from({key:True for key in polled_keys})
                    polled_keys.clear();last_click=session.clock
                session.lua.globals()["PLAYER_TICK"](session.clock,session.lua.table_from(event))
                row["pages"]=(state["reading_generation"]or 0)-initial if target_reached else 0
                if row["pages"]>=pages:row["status"]="passed";break
                if vm["external_checker"]:row["status"]="external_checker_boundary";break
                if vm["status"]=="finished":row["status"]="source_finished";break
                deadline=vm["next_deadline"](vm)
                session.clock=max(session.clock+50,min(deadline or session.clock+50,session.clock+500))
            else:row["status"]="tick_budget"
            if row["status"]in("tick_budget","time_budget"):
                row["waiting"]=table_value(vm["waiting"])
                row["frames"]=[{"script":frame["script"]["name"],
                    "offset":frame["script"]["code"][frame["index"]][1]}for frame in vm["frames"].values()]
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


def pixel_worker(game,files,mode,pages,seconds,folder,wanted,predecessors):
    folder=Path(folder)
    def publish(row):write_json(folder/"chapters.json",row)
    return game_chapters(game,files,mode,pages,seconds,publish,folder,wanted,predecessors)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game",action="append");parser.add_argument("--mode",default="original",choices=("original","online","local"))
    parser.add_argument("--pages",type=int,default=3);parser.add_argument("--seconds",type=float,default=2.5)
    parser.add_argument("--jobs",type=int,default=3)
    parser.add_argument("--pixels",action="store_true",help="use actual native pages in isolated worker processes")
    parser.add_argument("--resume-report",type=Path,help="recheck unresolved entries from a prior chapter report")
    parser.add_argument("--predecessor",action="append",default=[],
                        help="game:target.mjo:previous.mjo; execute source predecessor to establish required state")
    args=parser.parse_args()
    # This continuation waits for the nonlooping track set by its preceding
    # source chapter. Run that source; do not invent its globals/audio state.
    predecessors={"happy_princess":{"hime09-02.mjo":"hime09-01.mjo"}}
    for spec in args.predecessor:
        game,target,source=spec.split(":",2)
        predecessors.setdefault(game,{})[target]=source
    previous=read_json(args.resume_report)if args.resume_report else None
    unresolved={"failed","fixture_boundary","needs_prerequisite_state","time_budget","tick_budget",
                "choice_boundary","dialog_boundary"}
    wanted={row["game"]:{chapter["chapter"]for chapter in row.get("chapters",[])
                       if chapter.get("status")in unresolved}
            for row in previous.get("games",[])}if previous else None
    folder=archive_dir("chapter_regressions");files,roots=payload(folder)
    samples=read_json(ROOT/"config/player_samples.json")["games"]+read_json(ROOT/"config/player_new_samples.json")["games"]
    by_id={game["id"]:game for game in samples};tasks=[]
    for root in roots:
        if args.game and root["id"]not in args.game:continue
        if wanted is not None and not wanted.get(root["id"]):continue
        prefix=root["directory"]+"/";source={target[len(prefix):]:path for path,target in files if target.startswith(prefix)}
        tasks.append((by_id[root["id"]],source))
    report={"schema":1,"timestamp":timestamp(),"status":"running","games":[],
        "mode":args.mode,"pages_per_chapter":args.pages,"seconds_per_chapter":args.seconds,
        "device_accessed":False,"player_saves_used":False,"real_api_called":False,
        "scope":"every narrative MJO main entry from isolated title snapshot; prerequisite flags are not invented",
        "evidence":str(folder.relative_to(ROOT)),
        "source_sha256":{str(p.relative_to(ROOT)):sha256(p)for p in(ROOT/"player/runtime").glob("*.lua")}}
    report["real_pixels"]=args.pixels
    if previous:
        report["prior_report"]={"file":str(args.resume_report),"sha256":sha256(args.resume_report),
                                "summary":previous.get("summary",{})}
    destination=ROOT/("reports/chapter_followup_regressions.json"if previous else"reports/chapter_regressions.json")
    def publish(row):
        write_json(folder/(row["game"].replace(" ","_")+".json"),row)
    executor=ProcessPoolExecutor if args.pixels else ThreadPoolExecutor
    with executor(max_workers=args.jobs)as pool:
        if args.pixels:
            futures={pool.submit(pixel_worker,game,source,args.mode,args.pages,args.seconds,
                                 str(folder/game["id"]),wanted.get(game["id"])if wanted is not None else None,
                                 predecessors.get(game["id"])):game
                     for game,source in tasks}
        else:
            futures={pool.submit(game_chapters,game,source,args.mode,args.pages,args.seconds,publish,
                                 None,wanted.get(game["id"])if wanted is not None else None,
                                 predecessors.get(game["id"])):game for game,source in tasks}
        for future in as_completed(futures):
            game=futures[future]
            try:row=future.result()
            except Exception as error:row={"game":game["id"],"status":"fixture_failure","error":str(error),"chapters":[]}
            report["games"].append(row)
            print(row["game"],"chapters",len(row["chapters"]),
                "failed",sum(c["status"]=="failed"for c in row["chapters"]),
                "prerequisites",sum(c["status"]=="needs_prerequisite_state"for c in row["chapters"]),flush=True)
            write_json(destination,report);write_json(folder/"report.json",report)
    counts={}
    for game in report["games"]:
        for chapter in game["chapters"]:counts[chapter["status"]]=counts.get(chapter["status"],0)+1
    report.update(status="diagnostic_complete",timestamp=timestamp(),summary=counts,
        game_failures=[row["game"]for row in report["games"]if row["status"]!="completed"],
        full_routes_verified=False,source_changed_during_run=any(sha256(ROOT/name)!=digest for name,digest in report["source_sha256"].items()))
    report["status"]=("passed"if not report["game_failures"]and not report["source_changed_during_run"]
        and all(key in("passed","source_finished")for key in counts)else"failed")
    write_json(destination,report);write_json(folder/"report.json",report)
    print("Chapter results:",json.dumps(counts),flush=True)
    return int(report["status"]!="passed")


if __name__=="__main__":raise SystemExit(main())
