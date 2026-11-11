"""Profile real Artemis frame callbacks in isolated projects using wall time."""
from __future__ import annotations
import argparse
from pathlib import Path
import statistics
import shutil
import subprocess
import sys
from lua_bridge import create_lua,table_value
from probe_project import isolated_project,clean_project
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,write_json,sha256


def percentile(values,p):
    return sorted(values)[min(len(values)-1,int(len(values)*p))]if values else 0


def run(scene="title",duration=8000,save_source=None,slot=None,label=None):
    evidence=archive_dir("frame_profile")
    project=isolated_project(ROOT/"projects/windows",evidence/"probe")
    originals={}
    if save_source:
        source=Path(save_source).resolve(strict=True)
        target=project/"savedata-dev/port-data"
        target.mkdir()
        for path in source.iterdir():
            if path.is_file()and path.suffix in(".mjs",".mjb",".png"):
                originals[path]=sha256(path)
                shutil.copy2(path,target/path.name)
    startup=subprocess.STARTUPINFO()
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    try:
        with(evidence/(evidence.name[:8]+"_engine.txt")).open("wb")as log:
            command=[str(project/"Ruri_Artemis.exe"),"/profile",str(duration),"/profile_scene",scene]
            if slot is not None:command.extend(("/profile_slot",str(slot)))
            process=subprocess.Popen(command,cwd=project,startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
            try:code=process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
                raise RuntimeError("real-time frame profile timed out")
        result=next((p for p in(project/"native_profile.lua",project/"savedata-dev/native_profile.lua")
                     if p.is_file()),None)
        if code!=0 or result is None:
            raise RuntimeError("real-time profile did not finish: "+str(evidence))
        raw=table_value(create_lua().execute(result.read_text(encoding="utf-8")))
        if raw.get("problem"):raise AssertionError(raw["problem"])
        samples=raw["samples"]
        active=[s for s in samples if s["t"]>1000]
        intervals=[b["t"]-a["t"]for a,b in zip(active,active[1:])]
        draw=[s["draw_ms"]for s in active if s["changed"]]
        native=[s["native_ms"]for s in active if s["native_ms"]>0]
        bind=[s["bind_ms"]for s in active if s["changed"]]
        total=[s["vm_ms"]+s["draw_ms"]for s in active]
        drawn=[s["t"]for s in active if s["changed"]]
        animation=[b-a for a,b in zip(drawn,drawn[1:])if 0<b-a<=100]
        if not intervals:raise AssertionError("not enough actual native frame samples")
        report={"status":"passed","scene":scene,"build_id":raw["build_id"],"samples":len(samples),
            "frame_interval_median_ms":statistics.median(intervals),
            "frame_interval_p95_ms":percentile(intervals,.95),
            "effective_callback_fps":1000/statistics.mean(intervals),
            "draw_median_ms":statistics.median(draw)if draw else 0,"draw_p95_ms":percentile(draw,.95),
            "native_median_ms":statistics.median(native)if native else 0,
            "bind_median_ms":statistics.median(bind)if bind else 0,
            "motion_frame_median_ms":statistics.median(animation)if animation else 0,
            "motion_frame_p95_ms":percentile(animation,.95),
            "callback_cpu_p95_ms":percentile(total,.95),"presented_frames":raw["presenter"]["frames"],
            "unchanged_frames_skipped":raw["presenter"]["unchanged"],
            "partial_frames_suppressed":raw["presenter"]["suppressed"],
            "reading_wait":raw.get("reading_wait"),"evidence":str(evidence),
            "scope":"real Artemis callbacks and rendering; virtual setup excluded from measurements",
            "transport":raw["presenter"].get("transport","png")}
        if scene=="menu_pause":
            from PIL import Image,ImageStat
            captures=[*project.glob("profile_*.png"),*(project/"savedata-dev").glob("profile_*.png")]
            assert len(captures)>=4,"menu screenshots missing"
            for picture in captures:
                with Image.open(picture)as image:
                    assert max(ImageStat.Stat(image.convert("RGB")).stddev)>10,(
                        "blank menu presentation",picture.name)
            assert bool(raw.get("ui_waiting"))==bool(raw.get("initial_menu")),(
                "right-click did not complete both transitions",raw.get("initial_menu"),raw.get("ui_waiting"))
            windows=[]
            for begin,end in((450,1150),(1450,2150)):
                frames=[s["t"]for s in samples if begin<=s["t"]<=end and s["changed"]]
                gaps=[b-a for a,b in zip(frames,frames[1:])]
                costs=[s["vm_ms"]for s in samples if begin<=s["t"]<=end]
                windows.append({"begin_ms":begin,"frames":len(frames),
                    "gap_median_ms":statistics.median(gaps)if gaps else 0,
                    "gap_p95_ms":percentile(gaps,.95),"gap_max_ms":max(gaps,default=0),
                    "vm_p95_ms":percentile(costs,.95),"vm_max_ms":max(costs,default=0)})
            assert all(w["frames"]>=3 for w in windows),windows
            report.update(animation_windows=windows,slot=slot,label=label,
                scope="real right-click menu opening and closing; isolated save copy")
        if scene=="title"and(report["callback_cpu_p95_ms"]>40 or report["motion_frame_p95_ms"]>75):
            report["status"]="failed"
        if scene=="intro":
            from PIL import Image,ImageStat
            first=next((p for p in(project/"profile_1.png",project/"savedata-dev/profile_1.png")if p.exists()),None)
            if not first:raise AssertionError("initial real-time screenshot missing")
            with Image.open(first)as image:
                report["initial_frame_mean"]=ImageStat.Stat(image.convert("RGB")).mean
            if max(report["initial_frame_mean"])>2:report["status"]="failed"
        if scene=="start"and duration>=23000 and not raw.get("reading_wait"):
            report["status"]="failed"
        if scene=="coast":
            if not raw.get("reading_wait")or raw.get("console",{}).get("text","").startswith("\u4e00\u76f4"):
                report["status"]="failed"
            from PIL import Image,ImageDraw
            screenshots=sorted([*project.glob("profile_*.png"),*(project/"savedata-dev").glob("profile_*.png")],
                               key=lambda p:int(p.stem.rsplit("_",1)[-1]))
            contact=Image.new("RGB",(1280,210*((len(screenshots)+3)//4)),(24,24,24))
            draw=ImageDraw.Draw(contact)
            for i,path in enumerate(screenshots):
                with Image.open(path)as image:
                    thumb=image.convert("RGB");thumb.thumbnail((320,180))
                x=i%4*320;y=i//4*210
                contact.paste(thumb,(x,y+24));draw.text((x+8,y+5),path.name,fill="white")
            image_path=evidence/(evidence.name[:8]+"_coast_contact.png");contact.save(image_path)
            report["contact_sheet"]=str(image_path)
            report["final_console"]=raw["console"]["text"]
            report["scope"]="actual real-time Artemis coast-line sequence through two public dialogue clicks"
        if scene.startswith("menu_")and scene!="menu_pause":
            from PIL import Image,ImageDraw,ImageStat
            expected="config.mjo"if scene=="menu_config"else"loadsave.mjo"
            assert raw.get("active_ui_script")==expected,(scene,raw.get("active_ui_script"))
            screenshots=sorted([*project.glob("profile_*.png"),*(project/"savedata-dev").glob("profile_*.png")],
                               key=lambda p:int(p.stem.rsplit("_",1)[-1]))
            contact=Image.new("RGB",(1280,205*((len(screenshots)+3)//4)),"#242424")
            draw=ImageDraw.Draw(contact);pixels=set()
            for i,path in enumerate(screenshots):
                with Image.open(path)as picture:
                    image=picture.convert("RGB")
                    assert image.getpixel((20,300))!=(0,0,0),(scene,"partial black frame",path.name)
                    pixels.add(image.tobytes())
                    image.thumbnail((320,180))
                x,y=i%4*320,i//4*205
                contact.paste(image,(x,y+25));draw.text((x+5,y+5),path.name,fill="white")
            assert len(pixels)>=4,(scene,"no visible transition",len(pixels))
            contact_path=evidence/("20261109_"+scene+"_contact.png");contact.save(contact_path)
            report.update(contact_sheet=str(contact_path),distinct_captures=len(pixels),
                          active_ui_script=raw["active_ui_script"],
                          scope="real-time pause-menu transition into "+expected)
        write_json(ROOT/"reports"/("frame_profile_"+scene+("_"+label if label else"")+".json"),report)
        print(report)
        if report["status"]!="passed":raise AssertionError("real-time presentation regression: "+scene)
        return report
    finally:
        clean_project(project)
        assert all(sha256(path)==digest for path,digest in originals.items()),"player save changed"


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--scene",choices=("title","intro","start","coast",
        "menu_config","menu_save","menu_load","menu_pause"),default="title")
    parser.add_argument("--duration",type=int,default=8000)
    parser.add_argument("--save-source",type=Path)
    parser.add_argument("--slot",type=int)
    parser.add_argument("--label")
    args=parser.parse_args();run(args.scene,args.duration,args.save_source,args.slot,args.label)
