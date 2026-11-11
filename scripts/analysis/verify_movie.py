"""Play the shared original WMV in real Artemis and measure its process audio."""
from __future__ import annotations
import time
from pathlib import Path
import subprocess
import sys
from probe_project import isolated_project,clean_project
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,atomic_bytes,write_json,read_json
from majiro_movie import catalogue

PROBE=r'''*top
[debug mode="0" level="3"]
[var name="s.videovol" data="800"]
[var name="t.ruri_movie_file" data="movie/Ruri_OP.wmv"]
[lua]
function ruri_movie_finished(e)
  local f=assert(io.open('movie_finished.txt','wb'))
  f:write(tostring(e:now()));f:close()
end
[/lua]
[call file="system/movie.iet"]
[exit]
'''


def run():
    from pycaw.pycaw import AudioUtilities
    from pycaw.api.endpointvolume import IAudioMeterInformation
    evidence=archive_dir("native_movie")
    project=isolated_project(ROOT/"projects/windows",evidence/"probe")
    atomic_bytes(project/"system.ini",(project/"system.ini").read_text(encoding="utf8").replace(
        "BOOT = system/first.iet","BOOT = movie_probe.iet").encode("utf8"))
    atomic_bytes(project/"movie_probe.iet",PROBE.encode("utf8"))
    metadata=catalogue()["ruri_op.wmv"]
    startup=subprocess.STARTUPINFO()
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    started=time.monotonic();meter=None;samples=[]
    try:
        with(evidence/"20261109_engine.txt").open("wb")as log:
            process=subprocess.Popen([str(project/"Ruri_Artemis.exe")],cwd=project,
                startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
            try:
                while process.poll()is None:
                    elapsed=time.monotonic()-started
                    if elapsed>metadata["duration_ms"]/1000+30:
                        raise RuntimeError("Artemis movie did not return: "+str(evidence))
                    if meter is None:
                        for session in AudioUtilities.GetAllSessions():
                            if session.ProcessId==process.pid:
                                meter=session._ctl.QueryInterface(IAudioMeterInformation)
                    if meter is not None:samples.append(float(meter.GetPeakValue()))
                    time.sleep(.1)
            finally:
                if process.poll()is None:process.kill();process.wait(timeout=5)
        elapsed=time.monotonic()-started
        marker=next((p for p in(project/"movie_finished.txt",project/"savedata-dev/movie_finished.txt")
                     if p.is_file()),None)
        assert marker is not None,"video completion callback missing: "+str(evidence)
        assert elapsed>=metadata["duration_ms"]/1000-5,("video returned too early",elapsed)
        assert max(samples,default=0)>.01,"video soundtrack is silent"
        report={"status":"passed","file":metadata["file"],"elapsed_seconds":elapsed,
                "duration_ms":metadata["duration_ms"],"audio_peak":max(samples),
                "samples":len(samples),"bytes_copied":0,"evidence":str(evidence),
                "build_id":read_json(ROOT/"reports/windows_build.json")["build_id"],
                "scope":"original WMV playback duration, completion callback and per-process audio; video pixels not captured"}
        write_json(ROOT/"reports/native_movie.json",report)
        write_json(evidence/"20261109_result.json",report)
        print(report)
        return report
    finally:clean_project(project)


if __name__=="__main__":
    run()
