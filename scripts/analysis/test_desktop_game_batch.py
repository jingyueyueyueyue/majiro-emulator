"""Batch real-script sessions and native pixel rendering in background processes."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse
import json
import sys
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from port_common import ROOT,archive_dir,read_json,write_json,timestamp,sha256
from lua_bridge import table_value
from desktop_pixel_session import PixelSession
from test_story_entry_regressions import run_story


def files_for(game):
    directory=Path(game["directory"])
    return {p.relative_to(directory).as_posix():p for p in directory.rglob("*")if p.is_file()
            and not any(k.lower()in("savedata","save","saves","save_chs")for k in p.relative_to(directory).parts)}


def tick(session,event=None,delta=50):
    session.clock+=delta
    session.lua.globals()["PLAYER_TICK"](session.clock,session.lua.table_from(event or{"buttons":0,"held_buttons":0}))


def settle(session,minimum=1000,maximum=8000):
    began=session.clock
    for _ in range(maximum//50):
        tick(session)
        state=session.system["state"]
        if session.vm["modal_dialog"]:return "dialog_boundary"
        if session.vm["external_checker"]:return "external_checker_boundary"
        if session.vm["status"]=="finished":return "source_finished"
        if session.clock-began>=minimum and state["reading_wait"]:return "text"
        if session.clock-began>=minimum and state["ui_waiting"]:return "menu"
    return "time_boundary"


def snapshot(session,slot):
    state=session.system["state"]
    flags={name:state["settings"][name]for name in("save","save_menu")}
    # Fixture snapshots use production save/restore and fresh in-memory storage.
    # This only permits taking the test snapshot; no progress is unlocked.
    state["settings"]["save"]=True;state["settings"]["save_menu"]=True
    providers=session.system["providers"]
    providers["$save_point"](session.vm,session.lua.table())
    providers["$do_save"](session.vm,session.lua.table_from([slot]))
    for name,value in flags.items():state["settings"][name]=value


def restore(session,slot):
    if session.vm["modal_dialog"]:
        for _ in range(4):
            tick(session)
            if not session.vm["modal_dialog"]:break
    session.system["providers"]["$do_load"](session.vm,session.lua.table_from([slot]))
    tick(session,delta=16)


def click(session,args):
    point={"x":args[2]+args[4]/2+.25,"y":args[3]+args[5]/2+.625}
    tick(session,point|{"buttons":0,"held_buttons":0},400)
    tick(session,point|{"buttons":1,"held_buttons":1},20)
    tick(session,point|{"buttons":0,"held_buttons":0},20)
    return settle(session)


def available_control(session,args):
    width,height=session.lua.globals()["PLAYER_SIZE"]()
    x,y,w,h=args[1:5]
    return w>0 and h>0 and 0<=x+w/2<width and 0<=y+h/2<height


def worker(game,folder,pages,seconds,modes):
    began=time.monotonic();files=files_for(game);rows=[]
    for mode in modes:
        directory=Path(folder)/game["id"].replace(" ","_")/mode
        row={"game":game["id"],"mode":mode,"status":"running","menu_cases":[]}
        try:
            session=PixelSession(game,files,mode,directory/"menus")
            boot=session.run();row["startup"]=boot
            if boot["error"]:raise RuntimeError(boot["error"])
            settle(session,2500,10000)
            row["title_frame"]=session.capture("title")
            areas=[[area["args"][i]for i in range(1,9)]for area in session.system["state"]["ui_areas"].values()]
            snapshot(session,701)
            for number,args in enumerate(areas[:8]):
                case={"area_id":args[0],"status":"running"}
                if not available_control(session,args):
                    row["menu_cases"].append(case|{"status":"source_inactive","rectangle":args[1:5]})
                    continue
                try:
                    restore(session,701)
                    case["boundary"]=click(session,session.lua.table_from(args))
                    case["frame"]=session.capture("entry_"+str(number))
                    # Send a held-free Enter pulse and a right-click return,
                    # using source callbacks in each entered menu.
                    if case["boundary"]=="menu":
                        tick(session,{"pressed":session.lua.table_from({13:True}),"buttons":0},400)
                        settle(session,250,1500)
                        tick(session,{"buttons":2,"held_buttons":2},400)
                        tick(session,{"buttons":0,"held_buttons":0},30)
                        case["return_boundary"]=settle(session,500,2000)
                    case["status"]="passed"
                except Exception as error:case.update(status="failed",error=str(error))
                row["menu_cases"].append(case)
            row["menu_native_operations"]=dict(session.operation_counts)
            story=PixelSession(game,files,mode,directory/"story")
            row["story"]=run_story(game,files,mode,pages,seconds,session=story)
            settle(story,1000,3000)
            row["story_frame"]=story.capture("story_final")
            row["story_control_cases"]=[]
            if story.system and story.system["state"]["reading_wait"]:
                controls=[[a["args"][i]for i in range(1,9)]
                          for a in story.system["state"]["ui_areas"].values()]
                snapshot(story,702)
                for number,args in enumerate(controls[:12]):
                    case={"area_id":args[0],"status":"running"}
                    if not available_control(story,args):
                        row["story_control_cases"].append(case|{"status":"source_inactive","rectangle":args[1:5]})
                        continue
                    try:
                        restore(story,702)
                        case["boundary"]=click(story,story.lua.table_from(args))
                        state=story.system["state"]
                        case["active_ui_script"]=state["active_ui_script"]
                        case["frame"]=story.capture("story_control_"+str(number))
                        if state["active_ui_script"]=="history.mjo":
                            case["voiced_history_entries"]=sum(bool(e["voice"])for e in state["console"]["history"].values())
                            case["replay_click_regions"]=len(state["ui_areas"])
                            tick(story,{"pressed":story.lua.table_from({13:True}),"buttons":0},500)
                            settle(story,500,1500)
                            for _ in range(2):
                                if not any(f["script"]["name"]=="history.mjo"
                                           for f in story.vm["frames"].values()):break
                                tick(story,{"buttons":2,"held_buttons":2},500)
                                tick(story,{"buttons":0,"held_buttons":0},30)
                                settle(story,500,2000)
                            case["returned_to_story"]=bool(story.system["state"]["reading_wait"])
                        case["status"]="passed"
                    except Exception as error:case.update(status="failed",error=str(error))
                    row["story_control_cases"].append(case)
            row["story_native_operations"]=dict(story.operation_counts)
            all_controls=row["menu_cases"]+row["story_control_cases"]
            row["menu_coverage"]={name:sum(c["status"]==name for c in all_controls)
                                  for name in("passed","failed","source_inactive")}
            row["real_frames"]=session.rendered_frames+story.rendered_frames
            row["status"]="failed"if row["story"]["status"]=="failed"or any(
                c["status"]=="failed"for c in row["menu_cases"]+row["story_control_cases"])else(
                "passed"if row["story"]["status"]=="passed"else"boundary")
        except Exception as error:row.update(status="failed",error=str(error))
        row["seconds"]=round(time.monotonic()-began,2);rows.append(row)
        write_json(directory/"report.json",row)
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game",action="append")
    parser.add_argument("--mode",action="append",choices=("original","online","local"))
    parser.add_argument("--pages",type=int,default=200)
    parser.add_argument("--seconds",type=int,default=120)
    parser.add_argument("--jobs",type=int,default=4)
    args=parser.parse_args();folder=archive_dir("desktop_game_batch")
    games=[g for g in read_json(ROOT/"config/recovered_library.json")["games"]
           if not args.game or g["id"]in args.game]
    before={p.name:sha256(p)for p in(ROOT/"player/runtime").glob("*.lua")}
    report={"status":"running","timestamp":timestamp(),"cases":[],
            "device_accessed":False,"real_native_pixels":True,"windows_game_window_opened":False,
            "real_translation_api_used":False,"player_saves_used":False,"full_routes_verified":False,
            "scope":"public menu inputs, temporary snapshot restore, Enter/right-click transitions and long first-choice story",
            "host_limits":["audio transport and movie playback use metadata fixtures",
                           "desktop glyphs use GDI with the recovered font; Android glyphs are checked separately",
                           "render at every page/menu and periodic animation samples"],
            "evidence":str(folder.relative_to(ROOT))}
    with ProcessPoolExecutor(max_workers=args.jobs)as pool:
        tasks=[pool.submit(worker,g,str(folder),args.pages,args.seconds,args.mode or["original","online"])for g in games]
        for future in as_completed(tasks):
            try:rows=future.result()
            except Exception as error:rows=[{"status":"worker_failed","error":str(error)}]
            report["cases"].extend(rows)
            for row in rows:
                print(json.dumps({k:row.get(k)for k in("game","mode","status","seconds","error")},ensure_ascii=True),flush=True)
            write_json(folder/"report.json",report);write_json(ROOT/"reports/desktop_game_batch.json",report)
    report["status"]="complete"
    report["source_changed_during_run"]=before!={p.name:sha256(p)for p in(ROOT/"player/runtime").glob("*.lua")}
    write_json(folder/"report.json",report);write_json(ROOT/"reports/desktop_game_batch.json",report)
    return int(any(r["status"]in("failed","worker_failed")for r in report["cases"])or report["source_changed_during_run"])


if __name__=="__main__":raise SystemExit(main())
