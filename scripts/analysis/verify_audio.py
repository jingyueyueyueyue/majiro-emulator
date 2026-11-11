"""Measure this engine process's real Windows audio session; never alter mixer settings."""
from __future__ import annotations
from pathlib import Path
import subprocess
import sys
import time
from lua_bridge import create_lua,table_value
from probe_project import isolated_project,clean_project
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,atomic_bytes,read_json,write_json,lua_value

PROBE=r'''*top
[debug mode="0" level="3"]
[lua]
local host,began
function audio_start(e)
  host=assert(loadstring(e:file('runtime/artemis_audio.lua')))().install(e)
  host.command('play',AUDIO_SPEC)
  began=e:now()
  e:setEventHandler{onEnterFrame='audio_frame'}
end
function audio_frame(e)
  if e:now()-began>=3500 then
    e:tag{'var',name='t.check',system='get_sound_info'}
    local playing=tonumber(e:var('t.check.playing'))
    local gain=tonumber(e:var('t.check.gain'))
    local f=assert(io.open('audio_result.lua','wb'))
    f:write('return {playing='..tostring(playing)..',gain='..tostring(gain)..'}')
    f:close();host.command('stop',AUDIO_SPEC);e:tag{'exit'}
  end
  e:tag{'flip'}
end
[/lua]
[calllua function="audio_start"]
[stop]
'''


def run():
    from pycaw.pycaw import AudioUtilities
    from pycaw.api.endpointvolume import IAudioMeterInformation
    catalogue=read_json(ROOT/"resources/audio_catalogue.json")
    asset=catalogue["pl_op_inst.ogg"]
    from lua_bridge import create_lua
    environment=create_lua().execute((ROOT/"resources/scripts/environment.lua").read_text(encoding="utf-8"))
    gain=int(environment["MUSICVOL"])
    spec={"id":"music","file":asset["file"],"name":"pl_op_inst.ogg","volume":gain,
          "loop":True,"duration_ms":asset["duration_ms"],"generation":1}
    evidence=archive_dir("audio_native")
    project=isolated_project(ROOT/"projects/windows",evidence/"probe")
    config=(project/"system.ini").read_text(encoding="utf-8").replace("BOOT = system/first.iet",
                                                                    "BOOT = audio_probe.iet")
    atomic_bytes(project/"system.ini",config.encode("utf-8"))
    code=PROBE.replace("local host,began","local host,began\nlocal AUDIO_SPEC="+lua_value(spec))
    atomic_bytes(project/"audio_probe.iet",code.encode("utf-8"))
    startup=subprocess.STARTUPINFO()
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    meter=None;samples=[];mixer=None
    try:
        with (evidence/"20261109_engine_output.txt").open("wb")as log:
            process=subprocess.Popen([str(project/"Ruri_Artemis.exe")],cwd=project,
                startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+15
            try:
                while process.poll()is None:
                    if time.monotonic()>deadline:
                        raise RuntimeError("native audio check timed out")
                    if meter is None:
                        for session in AudioUtilities.GetAllSessions():
                            if session.ProcessId==process.pid:
                                meter=session._ctl.QueryInterface(IAudioMeterInformation)
                                volume=session.SimpleAudioVolume
                                mixer={"mute":bool(volume.GetMute()),"volume":volume.GetMasterVolume()}
                    if meter is not None:
                        samples.append(float(meter.GetPeakValue()))
                    time.sleep(0.04)
            finally:
                if process.poll()is None:
                    process.kill();process.wait(timeout=5)
        result_path=next((p for p in (project/"audio_result.lua",project/"savedata-dev/audio_result.lua")
                          if p.is_file()),None)
        if result_path is None:
            raise RuntimeError("engine did not finish the audio check: "+str(evidence))
        native=table_value(create_lua().execute(result_path.read_text(encoding="utf-8")))
        peak=max(samples,default=0)
        report={"status":"passed"if peak>0.01 else"failed","peak":peak,"sample_count":len(samples),
            "nonzero_samples":sum(x>0.001 for x in samples),"process_id":process.pid,
            "file":asset["file"],"gain_requested":gain,"native_query":native,"mixer":mixer,
            "build_id":read_json(ROOT/"reports/windows_build.json")["build_id"],
            "scope":"actual isolated Artemis BGM output measured per process, not a mocked callback",
            "evidence":str(evidence),"bytes_recorded":0}
        write_json(ROOT/"reports/native_audio.json",report)
        if report["status"]!="passed":
            raise AssertionError("Artemis BGM output is silent: "+str(report))
        print("native BGM output: passed, peak",round(peak,4),"samples",len(samples))
        return report
    finally:
        clean_project(project)


if __name__=="__main__":
    run()
