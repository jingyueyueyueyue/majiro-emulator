"""Interactive native acceptance: select a font, then cancel the second chooser."""
import subprocess
from lua_bridge import create_lua, table_value
from probe_project import isolated_project, clean_project
from port_common import ROOT, archive_dir, atomic_bytes, read_json, write_json

PROBE = r'''*top
[debug mode="0" level="3"]
[lua]
local frames=0
local function stage(text)
  local f=assert(io.open('font_stage.txt','wb'));f:write(text);f:close()
end
function font_start(e)
  stage('started')
  e:setEventHandler{onEnterFrame='font_frame'}
end
function font_frame(e)
  frames=frames+1
  stage('frame '..frames)
  if frames<10 then e:tag{'flip'};return end
  local function native(request)
    e:tag{'callnative',module='native/majiro_pixels.dll',method='RuriPixels',
      param=request,result='t.font'}
    return e:var('t.font')
  end
  assert(native('fontfile font/SourceHanSansSC-Regular.otf')=='OK')
  stage('font registered')
  local providers,state={},{settings={},font_cache={face='Source Han Sans SC'}}
  local settings=assert(loadstring(e:file('runtime/majiro_settings.lua')))()
  settings.install(function(name,fn)providers[name]=fn end,state,{
    font_dialog=function(face)
      stage('opening '..face)
      local reply=native('font_dialog\n'..face)
      assert(reply:sub(1,2)=='OK',reply)
      return reply:match('^OK selected\n(.+)$')or face
    end})
  local first=providers['$font_dialog$']({}, {'Source Han Sans SC',0})
  local changed=state.settings.font_name==first and state.persist_dirty
  state.persist_dirty=false
  local second=providers['$font_dialog$']({}, {first,0})
  local f=assert(io.open('font_result.lua','wb'))
  f:write('return {first='..string.format('%q',first)..',second='..
    string.format('%q',second)..',selected_in_settings='..tostring(changed)..'}')
  f:close();e:tag{'exit'}
end
[/lua]
[calllua function="font_start"]
[stop]
'''


def run():
    evidence = archive_dir("native_font")
    project = isolated_project(ROOT / "projects/windows", evidence / "probe")
    atomic_bytes(project / "system.ini", (project / "system.ini").read_text(encoding="utf8").replace(
        "BOOT = system/first.iet", "BOOT = font_probe.iet").encode("utf8"))
    atomic_bytes(project / "font_probe.iet", PROBE.encode("utf8"))
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    # This acceptance needs a visible interactive modal. SW_HIDE can hide
    # ChooseFont itself when it is the process's first shown window.
    startup.wShowWindow = 1
    try:
        with (evidence / "20261109_engine.txt").open("wb") as log:
            process = subprocess.Popen([str(project / "Ruri_Artemis.exe")], cwd=project,
                                       startupinfo=startup, stdout=log, stderr=subprocess.STDOUT)
            print("font chooser process", process.pid, str(project), flush=True)
            try:
                code = process.wait(timeout=240)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError("interactive font acceptance timed out")
        output = next((p for p in (project / "font_result.lua", project / "savedata-dev/font_result.lua")
                       if p.is_file()), None)
        assert code == 0 and output, str(evidence)
        result = table_value(create_lua().execute(output.read_text(encoding="utf8")))
        assert result["first"] == "Microsoft YaHei", result
        assert result["first"] == result["second"] and result["selected_in_settings"], result
        report = {"status": "passed", "build_id": read_json(ROOT / "reports/windows_build.json")["build_id"],
                  "result": result, "evidence": str(evidence),
                  "scope": "real Artemis/native Windows chooser: select Microsoft YaHei, cancel next chooser"}
        write_json(ROOT / "reports/native_font_tests.json", report)
        write_json(evidence / "20261109_result.json", report)
        print(report)
        return report
    finally:
        clean_project(project)


if __name__ == "__main__":
    run()
