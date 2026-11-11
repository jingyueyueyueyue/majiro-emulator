"""Check chapter right-click policy and sustained title pixels in real Artemis."""
import argparse
import subprocess

from lua_bridge import create_lua, table_value
from probe_project import isolated_project, clean_project
from test_title_render import assert_title
from port_common import ROOT, archive_dir, read_json, write_json


def run(chapter="sub_title_yura.mjo",via="button"):
    evidence=archive_dir("chapter_return_native")
    stamp=evidence.name[:8]
    project=isolated_project(ROOT/"projects/windows",evidence/"probe")
    startup=subprocess.STARTUPINFO()
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow=0
    try:
        with(evidence/(stamp+"_engine.txt")).open("wb")as log:
            process=subprocess.Popen([str(project/"Ruri_Artemis.exe"),"/probe","1",
                "/scenario","chapter_return","/chapter",chapter,"/via",via,
                "/profile","3000","/profile_scene","chapter_return"],cwd=project,
                startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
            try:
                code=process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
                raise RuntimeError("chapter return native probe timed out: "+str(evidence))
        outputs={}
        for folder in(project,project/"savedata-dev"):
            for name in("native_profile.lua","native_error.txt","native_result.lua"):
                path=folder/name
                if path.is_file():outputs[name]=path
        assert code==0 and "native_error.txt"not in outputs and "native_profile.lua"in outputs,(
            "chapter return failed",str(evidence),list(outputs))
        raw=table_value(create_lua().execute(outputs["native_profile.lua"].read_text(encoding="utf8")))
        assert not raw.get("problem"),raw.get("problem")
        assert raw["build_id"]==read_json(ROOT/"reports/windows_build.json")["build_id"]
        assert raw["ui_waiting"]and raw["active_ui_script"]=="buttonmenu.mjo"
        main=raw["frames"][0]if isinstance(raw["frames"],list)else raw["frames"]["1"]
        assert main["frames"][0]["script"]=="start.mjo"
        screenshots=[*project.glob("profile_*.png"),*(project/"savedata-dev").glob("profile_*.png")]
        assert len(screenshots)>=3
        pixels=[{"screenshot":str(picture),"comparison":assert_title(picture)}for picture in screenshots]
        report={"status":"passed","chapter":chapter,"via":via,"build_id":raw["build_id"],
            "evidence":str(evidence),"screenshots":pixels,"real_idle_ms":raw["elapsed_ms"],
            "scope":"source chapter, right menu open/return, explicit title return, idle title pixels"}
        write_json(evidence/(stamp+"_result.json"),report)
        write_json(ROOT/"reports/chapter_return_native.json",report)
        print({k:report[k]for k in("status","chapter","via","real_idle_ms","evidence")},flush=True)
        return report
    finally:
        clean_project(project)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--chapter",default="sub_title_yura.mjo")
    parser.add_argument("--via",choices=("button","menu"),default="button")
    parser.add_argument("--all",action="store_true")
    args=parser.parse_args()
    if args.all:
        cases=[run(chapter,via)for chapter in(
            "sub_title_yura.mjo","sub_title_iichiko.mjo",
            "sub_title_ruri_akarui.mjo","sub_title_ruri_sibou.mjo")
            for via in("button","menu")]
        write_json(ROOT/"reports/chapter_return_native.json",{
            "status":"passed","cases":cases,"build_id":cases[0]["build_id"]})
    else:
        run(args.chapter,args.via)
