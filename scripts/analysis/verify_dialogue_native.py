"""Render the reported shrine and portrait pages through the real Artemis host."""
from __future__ import annotations
import subprocess
from pathlib import Path
from PIL import Image,ImageChops,ImageStat
from lua_bridge import create_lua,table_value
from probe_project import isolated_project,clean_project
from port_common import ROOT,archive_dir,read_json,write_json


def run():
    evidence=archive_dir("native_dialogue")
    references=read_json(ROOT/"reports/dialogue_layout_tests.json")["pages"]
    results=[]
    for item in references:
        page=item["page"]
        project=isolated_project(ROOT/"projects/windows",evidence/("probe_%d"%page))
        startup=subprocess.STARTUPINFO()
        startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        try:
            with(evidence/("20261109_page_%d_engine.txt"%page)).open("wb")as log:
                process=subprocess.Popen([str(project/"Ruri_Artemis.exe"),"/probe","1",
                    "/scenario","story_page","/story_page",str(page)],cwd=project,
                    startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
                try:code=process.wait(timeout=55)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
                    raise RuntimeError("native dialogue probe timed out")
            outputs={}
            for folder in(project,project/"savedata-dev"):
                for name in("native_result.lua","native_screen.png","native_error.txt"):
                    source=folder/name
                    if source.is_file():
                        destination=evidence/("20261109_page_%d_"%page+name)
                        source.replace(destination);outputs[name]=destination
            if code!=0 or "native_error.txt"in outputs or "native_result.lua"not in outputs:
                raise AssertionError("native dialogue failed: "+str(outputs))
            native=table_value(create_lua().execute(outputs["native_result.lua"].read_text(encoding="utf-8")))
            assert not native.get("problem"),native.get("problem")
            assert native["reading_wait"]and native["console"]["raster_only"]
            assert native["console"]["text"]==item["console"]["text"]
            assert native["console"]["name"]==item["console"]["name"]
            assert native["build_id"]==read_json(ROOT/"reports/windows_build.json")["build_id"]
            with Image.open(outputs["native_screen.png"])as actual,Image.open(item["screenshot"])as expected:
                delta=ImageChops.difference(actual.convert("RGB"),expected.convert("RGB"))
                # Animated click cursor is outside these source UI regions.
                regions={"body":(310,585,1120,690),"portrait_and_name":(96,530,490,700)}
                errors={name:max(ImageStat.Stat(delta.crop(rectangle)).mean)
                        for name,rectangle in regions.items()}
                assert max(errors.values())<2,errors
            results.append({"page":page,"status":"passed","mean_channel_difference":errors,
                            "screenshot":str(outputs["native_screen.png"])})
            print("native dialogue page:",page,"passed",flush=True)
        finally:clean_project(project)
    report={"status":"passed","build_id":native["build_id"],"pages":results,"evidence":str(evidence),
            "scope":"actual Artemis output; source-controlled coordinates, name and portrait; model comparison"}
    write_json(ROOT/"reports/native_dialogue_tests.json",report)
    return report


if __name__=="__main__":run()
