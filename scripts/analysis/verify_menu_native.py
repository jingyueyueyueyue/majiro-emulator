"""Actual Artemis checks for save-page digits and settings/history black bands."""
from __future__ import annotations
import subprocess
import numpy as np
from PIL import Image
from lua_bridge import create_lua,table_value
from probe_project import isolated_project,clean_project
from port_common import ROOT,archive_dir,read_json,write_json


def run():
    evidence=archive_dir("native_menu_composition")
    assets=read_json(ROOT/"resources/image_catalogue.json")
    build=read_json(ROOT/"reports/windows_build.json")["build_id"]
    cases=[]
    for scene in("load_header","story_config","story_history"):
        project=isolated_project(ROOT/"projects/windows",evidence/scene)
        startup=subprocess.STARTUPINFO()
        startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        try:
            with(evidence/("20261109_"+scene+"_engine.txt")).open("wb")as log:
                process=subprocess.Popen([str(project/"Ruri_Artemis.exe"),"/probe","1",
                    "/scenario",scene],cwd=project,startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
                try:code=process.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
                    raise RuntimeError("native menu probe timed out: "+scene)
            outputs={}
            for folder in(project,project/"savedata-dev"):
                for name in("native_result.lua","native_screen.png","native_error.txt"):
                    source=folder/name
                    if source.is_file():
                        dest=evidence/("20261109_"+scene+"_"+name)
                        source.replace(dest);outputs[name]=dest
            if code or"native_result.lua"not in outputs or"native_error.txt"in outputs:
                raise AssertionError((scene,outputs,code))
            result=table_value(create_lua().execute(outputs["native_result.lua"].read_text(encoding="utf-8")))
            assert not result.get("problem"),result.get("problem")
            assert result["build_id"]==build
            with Image.open(outputs["native_screen.png"])as image:actual=np.asarray(image.convert("RGB"))
            samples=0
            if scene=="load_header":
                variants={k:np.asarray(Image.open(ROOT/"resources"/assets["sl_load_"+k+".rct"]["file"]).convert("RGBA"))
                          for k in("f","p")}
                for digit in range(10):
                    x=632+52*digit
                    expected=variants["p"if digit==1 else"f"][60:93,x:x+39]
                    opaque=expected[:,:,3]==255
                    difference=np.abs(actual[60:93,x:x+39].astype(int)-expected[:,:,:3].astype(int))
                    # The final native texture pass rounds anti-aliased
                    # edges; the CPU test above remains exact within 2.
                    assert difference[opaque].max()<=6 and difference[opaque].mean()<1,(
                        scene,digit,int(difference[opaque].max()),float(difference[opaque].mean()))
                    samples+=int(opaque.sum())
            else:
                for y in range(70,600):
                    assert actual[y,20:110].max()>20,(scene,"black scanline",y)
                if scene=="story_config":
                    expected=np.asarray(Image.open(ROOT/"resources"/assets["config_off.rct"]["file"]).convert("RGBA"))[40:78,760:1110]
                    opaque=(expected[:,:,3]==255)&(expected[:,:,:3].max(axis=2)>100)
                    difference=np.abs(actual[40:78,760:1110].astype(int)-expected[:,:,:3].astype(int))
                    assert difference[opaque].max()<=6 and difference[opaque].mean()<2,(scene,"header clipped")
                    samples=int(opaque.sum())
            cases.append({"scene":scene,"status":"passed","source_glyph_pixels":samples,
                          "screenshot":str(outputs["native_screen.png"])})
            print("native menu:",scene,"passed",flush=True)
        finally:clean_project(project)
    report={"status":"passed","build_id":build,"cases":cases,"evidence":str(evidence),
            "scope":"real engine save-page 2, settings through pause menu and history; header pixels and black-band checks"}
    write_json(ROOT/"reports/native_menu_composition_tests.json",report)
    return report


if __name__=="__main__":run()
