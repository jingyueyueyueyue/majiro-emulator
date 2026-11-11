"""Bounded natural story reading from title; no pre-unlocked flags or PC rewrites."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time

from model_session import Session
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,write_json,read_json,sha256,archive_dir


def run(pages=100,choices=None,verify_only=False,resume=None,checkpoint=False,stop_at_title=False):
    evidence=archive_dir("story_campaign")
    saves=Path(resume).resolve()if resume else evidence/"saves"
    session=Session(allow_prepare=not verify_only,save_root=saves)
    begun=time.monotonic()
    if session.advance()!="ui":raise RuntimeError("title unavailable")
    if resume:
        assert session.system["state"]["save_catalog"]["slot_09392"],"campaign checkpoint not found: "+str(saves)
        session.system["providers"]["$do_load"](session.vm,session.lua.table_from([9392]))
        status=session.advance()
        assert status=="text",session.summary()
    else:status=session.click(1)
    history=[];selected=[];choices=choices or [];terminal=None
    try:
        for page in range(pages):
            if page%100==0:print("story progress:",page,"pages;",session.vm["steps"],"steps",flush=True)
            if checkpoint and page%100==0 and status=="text":
                session.system["providers"]["$do_save"](session.vm,session.lua.table_from([9392]))
            if status=="text":
                console=session.system["state"]["console"]
                entries=console["page"]
                history.append({"page":page,"text":console["text"],
                    "sources":[{"script":item["script"],"offset":item["offset"]}for item in entries.values()]})
                status=session.page()
            elif status=="ui":
                available=session.system["state"]["ui_areas"]
                if not available:break
                index=choices[len(selected)] if len(selected)<len(choices)else 1
                frames=list(session.vm["frames"].values())
                chapter=any(f["script"]["name"].startswith("sub_title")for f in frames)
                if stop_at_title and frames and frames[0]["script"]["name"]=="start.mjo":
                    terminal="returned_to_title";break
                if chapter and len(selected)>=len(choices):
                    candidates=[(a["args"][1],i)for i,a in available.items()if 0<a["args"][1]<99]
                    if candidates:index=max(candidates)[1]
                if checkpoint:
                    from test_title_render import render_state
                    render_state(session,evidence/(evidence.name[:8]+"_choice_%03d.png"%len(selected)))
                selected.append({"index":index,"chapter_menu":chapter,
                    "frames":[f["script"]["name"]for f in frames],
                    "areas":[list(a["args"].values())[:5]for a in available.values()]})
                status=session.click(index)
            else:break
    except Exception as error:
        status="campaign_error"
        failure=str(error)
    else:failure=None
    if checkpoint and status=="text"and not failure:
        session.system["providers"]["$do_save"](session.vm,session.lua.table_from([9392]))
    report=session.summary()
    if failure or report["problem"]:
        report["threads"]={str(i):{
            "finished":t["finished"],"waiting":__import__("lua_bridge").table_value(t["waiting"]),
            "frames":[{"script":f["script"]["name"],"offset":f["script"]["code"][f["index"]][1],
                       "args":__import__("lua_bridge").table_value(f["args"]),
                       "locals":__import__("lua_bridge").table_value(f["locals"])}
                      for f in t["frames"].values()]}
            for i,t in session.vm["threads"].items()}
    report["console"]={k:v for k,v in report["console"].items()if k!="history"}
    report.update({"schema":1,"navigation_status":status,"pages":history,"selections":selected,
        "elapsed_seconds":round(time.monotonic()-begun,3),"failure":failure,
        "source_identity":read_json(ROOT/"resources/source_manifest.json")["source_identity"],
        "runtime_hashes":{p.name:sha256(p)for p in (ROOT/"runtime").glob("*.lua")},
        "evidence":str(evidence),
        "save_root":str(saves),"resumed":bool(resume),"checkpoint":checkpoint,
        "release_allowed":False,"complete":False,"terminal":terminal,
        "progress":__import__("lua_bridge").table_value(session.system["state"]["progress"])})
    write_json(ROOT/"reports/story_campaign.json",report)
    os.link(ROOT/"reports/story_campaign.json",evidence/(evidence.name[:8]+"_campaign.json"))
    if checkpoint:
        progress_path=ROOT/"reports/story_progress.json"
        progress=read_json(progress_path)if progress_path.exists()else{
            "schema":1,"scripts":[],"read_positions":{},"runs":[]}
        for page in history:
            for source in page["sources"]:
                key=source["script"]+":"+str(source["offset"])
                progress["read_positions"][key]=True
        progress["scripts"]=sorted(set(progress["scripts"])|set(report["loaded_scripts"]))
        progress["runs"].append({"evidence":str(evidence),"pages":len(history),
            "steps":report["steps"],"status":status,"failure":failure,"problem":report["problem"]})
        progress["checkpoint_root"]=str(saves)
        progress["unique_read_positions"]=len(progress["read_positions"])
        progress["all_routes_complete"]=False
        write_json(progress_path,progress)
    print(json.dumps({"status":status,"pages":len(history),"steps":report["steps"],
                      "problem":report["problem"],"failure":failure},ensure_ascii=True))
    if status in("blocked","campaign_error")or failure:
        raise RuntimeError("story campaign failed; see reports/story_campaign.json")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--pages",type=int,default=100)
    parser.add_argument("--choices",default="")
    parser.add_argument("--verify-only",action="store_true")
    parser.add_argument("--resume",type=Path)
    parser.add_argument("--checkpoint",action="store_true")
    parser.add_argument("--stop-at-title",action="store_true")
    args=parser.parse_args()
    run(args.pages,[int(x)for x in args.choices.split(",")if x],args.verify_only,
        args.resume,args.checkpoint,args.stop_at_title)
