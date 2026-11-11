"""Run the original intro and loop files fully through the real Artemis decoder."""
import subprocess
from lua_bridge import create_lua, table_value
from probe_project import isolated_project, clean_project
from port_common import ROOT, archive_dir, atomic_bytes, read_json, write_json, lua_value

PROBE = r'''*top
[debug mode="0" level="3"]
[lua]
local host,began,observed,first_end
local SPEC=SPEC_VALUE
function loop_start(e)
  host=assert(loadstring(e:file('runtime/artemis_audio.lua')))().install(e)
  host.command('play',SPEC);began=e:now()
  e:setEventHandler{onEnterFrame='loop_frame'}
end
function loop_frame(e)
  local track=host.state.tracks.ruri_audio_music
  local elapsed=e:now()-began
  if track.phase=='loop'and not first_end then
    first_end=elapsed
  end
  if first_end and elapsed-first_end>=2000 then
    e:tag{'var',name='t.loop_query',system='get_sound_info'}
    local playing=tonumber(e:var('t.loop_query.playing'))or 0
    local f=assert(io.open('loop_result.lua','wb'))
    f:write('return {phase="'..track.phase..'",playing='..playing..
      ',handoff_ms='..first_end..',observed_ms='..elapsed..'}')
    f:close();host.command('stop',SPEC);e:tag{'exit'}
  end
  e:tag{'flip'}
end
[/lua]
[calllua function="loop_start"]
[stop]
'''


def run():
    evidence = archive_dir("audio_loop_native")
    spec = read_json(ROOT / "reports/music_menu_tests.json")["extended_music"]
    record = read_json(ROOT / "reports/windows_build.json")
    project = isolated_project(ROOT / "projects/windows", evidence / "probe")
    ini = (project / "system.ini").read_text(encoding="utf-8").replace(
        "BOOT = system/first.iet", "BOOT = loop_probe.iet")
    atomic_bytes(project / "system.ini", ini.encode("utf-8"))
    atomic_bytes(project / "loop_probe.iet", PROBE.replace("SPEC_VALUE", lua_value(spec)).encode("utf-8"))
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    try:
        with (evidence / "20261109_engine.txt").open("wb") as log:
            process = subprocess.Popen([str(project / "Ruri_Artemis.exe")], cwd=project,
                startupinfo=startup, stdout=log, stderr=subprocess.STDOUT)
            try:
                process.wait(timeout=spec["duration_ms"] / 1000 + 25)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
                raise RuntimeError("original intro did not hand over to loop: " + str(evidence))
        result = next((p for p in (project / "loop_result.lua",
            project / "savedata-dev/loop_result.lua") if p.is_file()), None)
        assert process.returncode == 0 and result, str(evidence)
        native = table_value(create_lua().execute(result.read_text(encoding="utf-8")))
        assert native["phase"] == "loop" and native["playing"] in (1, 2), native
        delay = native["handoff_ms"] - spec["duration_ms"]
        assert -100 <= delay < 750, (delay, native)
        report = {"status": "passed", "build_id": record["build_id"], "native": native,
                  "original_intro_ms": spec["duration_ms"], "completion_observation_delay_ms": delay,
                  "evidence": str(evidence), "audio_file_copies": 0,
                  "scope": "full original WAV, actual decoder completion and OGG loop playback; acoustic gap not measured"}
        write_json(ROOT / "reports/native_audio_loop.json", report)
        print("native original intro/loop passed", native)
        return report
    finally:
        clean_project(project)


if __name__ == "__main__":
    run()
