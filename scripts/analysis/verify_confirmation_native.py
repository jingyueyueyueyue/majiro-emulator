"""Render the real title confirmation after normal input; compare and retain visible evidence."""
import subprocess
from PIL import Image,ImageChops,ImageStat
from model_session import Session
from lua_bridge import create_lua,table_value
from probe_project import isolated_project,clean_project
from test_title_render import render_state
from port_common import ROOT,archive_dir,read_json,write_json


def run():
    evidence=archive_dir("confirmation_regression")
    session=Session(False,save_root=evidence/"model_saves")
    assert session.advance()=="ui"
    assert session.click(4)=="ui",session.summary()
    for _ in range(40):
        session.clock["ms"]+=20;session.event()
        assert session.vm["run"](session.vm,10000)!="blocked"
    expected=evidence/"20261109_model.png"
    render_state(session,expected)
    project=isolated_project(ROOT/"projects/windows",evidence/"probe")
    startup=subprocess.STARTUPINFO()
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    try:
        with(evidence/"20261109_engine.txt").open("wb")as log:
            process=subprocess.Popen([str(project/"Ruri_Artemis.exe"),"/probe","1",
                "/scenario","title_confirmation"],cwd=project,startupinfo=startup,
                stdout=log,stderr=subprocess.STDOUT)
            try:code=process.wait(timeout=40)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
                raise RuntimeError("native confirmation probe timed out")
        outputs={}
        for folder in(project,project/"savedata-dev"):
            for name in("native_result.lua","native_screen.png","native_error.txt"):
                path=folder/name
                if path.exists():
                    target=evidence/("20261109_"+name)
                    path.replace(target);outputs[name]=target
        assert code==0 and "native_error.txt"not in outputs and "native_result.lua"in outputs,outputs
        result=table_value(create_lua().execute(outputs["native_result.lua"].read_text(encoding="utf-8")))
        assert not result.get("problem"),result.get("problem")
        build=read_json(ROOT/"reports/windows_build.json")["build_id"]
        assert result["build_id"]==build
        with Image.open(expected)as model,Image.open(outputs["native_screen.png"])as real:
            difference=ImageChops.difference(model.convert("RGB"),real.convert("RGB"))
            error=max(ImageStat.Stat(difference.crop((320,230,960,470))).mean)
            assert error<2,("native confirmation differs",error)
        write_json(ROOT/"reports/native_confirmation_tests.json",{
            "status":"passed","build_id":build,"mean_difference":error,
            "screenshot":str(outputs["native_screen.png"]),"evidence":str(evidence),
            "scope":"real Artemis confirmation reached through original title exit button"})
        print("native confirmation: passed",evidence)
    finally:clean_project(project)


if __name__=="__main__":
    run()
