"""Verify the engine command used by source CONFIG really changes its native state."""
from __future__ import annotations
from pathlib import Path
import subprocess
import sys
from lua_bridge import create_lua,table_value
from probe_project import isolated_project,clean_project
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,atomic_bytes,write_json,read_json

PROBE=r'''*top
[debug mode="0" level="3"]
[lua]
local phase,frames,values=0,0,{}
function fullscreen_start(e)
  e:setEventHandler{onEnterFrame='fullscreen_frame'}
end
function fullscreen_frame(e)
  frames=frames+1
  if frames%12==0 then
    e:tag{'var',name='t.actual',system='fullscreen'}
    values[#values+1]=tonumber(e:var('t.actual'))
    if phase<2 then
      e:tag{'exec',command='fullscreen'};phase=phase+1
    else
      local f=assert(io.open('fullscreen_result.lua','wb'))
      f:write('return {'..table.concat(values,',')..'}');f:close()
      e:tag{'exit'};return
    end
  end
  e:tag{'flip'}
end
[/lua]
[calllua function="fullscreen_start"]
[stop]
'''


def run():
    evidence=archive_dir("native_fullscreen")
    project=isolated_project(ROOT/"projects/windows",evidence/"probe")
    atomic_bytes(project/"system.ini",(project/"system.ini").read_text(encoding="utf8").replace(
        "BOOT = system/first.iet","BOOT = fullscreen_probe.iet").encode("utf8"))
    atomic_bytes(project/"fullscreen_probe.iet",PROBE.encode("utf8"))
    startup=subprocess.STARTUPINFO()
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    try:
        with(evidence/"20261109_engine.txt").open("wb")as log:
            process=subprocess.Popen([str(project/"Ruri_Artemis.exe")],cwd=project,
                startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
            try:code=process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
                raise RuntimeError("native fullscreen check timed out: "+str(evidence))
        result=next((p for p in(project/"fullscreen_result.lua",project/"savedata-dev/fullscreen_result.lua")
                     if p.is_file()),None)
        assert code==0 and result is not None,("fullscreen result missing",str(evidence))
        values=table_value(create_lua().execute(result.read_text(encoding="utf8")))
        assert values==[0,1,0],values
        report={"status":"passed","states":values,"evidence":str(evidence),
                "build_id":read_json(ROOT/"reports/windows_build.json")["build_id"],
                "scope":"real Artemis fullscreen query before, after and after toggling back; source mapping model checked separately"}
        write_json(ROOT/"reports/native_fullscreen.json",report)
        print(report)
        return report
    finally:clean_project(project)


if __name__=="__main__":
    run()
