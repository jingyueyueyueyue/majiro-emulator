"""Save through real UI, exit Artemis, relaunch, and load in an isolated project."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
from lua_bridge import create_lua,table_value
from probe_project import isolated_project,clean_project
from test_saves import assert_frame
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,write_json


def run_engine(project,evidence,scenario):
    startup=subprocess.STARTUPINFO()
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow=0
    with(evidence/("20261109_"+scenario+".txt")).open("wb")as log:
        process=subprocess.Popen([str(project/"Ruri_Artemis.exe"),"/probe","1",
            "/scenario",scenario],cwd=project,startupinfo=startup,
            stdout=log,stderr=subprocess.STDOUT)
        try:code=process.wait(timeout=23)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=5)
            raise RuntimeError("native save navigation timed out: "+str(evidence))
    found={}
    for base in (project,project/"savedata-dev"):
        for name in ("native_result.lua","native_error.txt","native_screen.png"):
            source=base/name
            if source.is_file():
                target=evidence/("20261109_"+scenario+"_"+name)
                source.replace(target);found[name]=target
    if "native_error.txt"in found:
        raise RuntimeError(found["native_error.txt"].read_text(encoding="utf8",errors="replace"))
    assert code==0 and "native_result.lua"in found,("native save failed",code,str(evidence))
    result=table_value(create_lua().execute(found["native_result.lua"].read_text(encoding="utf8")))
    assert not result.get("problem"),result
    assert result["reading_wait"]and "native_screen.png"in found,result
    return result,found["native_screen.png"]


def run():
    evidence=archive_dir("native_save_restart")
    project=isolated_project(ROOT/"projects/windows",evidence/"probe")
    try:
        saved,before=run_engine(project,evidence,"save_flow")
        assert saved["last_saved_slot"]=="slot_00101",saved
        save_root=project/"savedata-dev/port-data"
        assert(save_root/"index.mjs").is_file(),"native save files missing"
        initial_bytes=sum(p.stat().st_size for p in save_root.iterdir())
        loaded,after=run_engine(project,evidence,"load_flow")
        assert loaded["load_generation"]==1,loaded
        expected=read_json(ROOT/"reports/save_tests.json")["first"]
        assert saved["console"]["text"]==loaded["console"]["text"]==expected["text"]
        assert(saved["console"]["x"],saved["console"]["y"])==(loaded["console"]["x"],loaded["console"]["y"])
        assert_frame(before,after,expected)
        report={"status":"passed","build_id":loaded["build_id"],"save_bytes":initial_bytes,
                "saved":saved,"loaded":loaded,"screenshots":[str(before),str(after)],
                "evidence":str(evidence),"scope":"two real Artemis processes; original menus; native storage; scene and text restore"}
        write_json(ROOT/"reports/native_save_tests.json",report)
        write_json(evidence/"20261109_result.json",report)
        print(json.dumps({k:report[k]for k in("status","build_id","save_bytes","evidence")},ensure_ascii=True))
        return report
    finally:
        clean_project(project)


if __name__=="__main__":
    run()
