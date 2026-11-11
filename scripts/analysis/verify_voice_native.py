"""Real raw-voice audition plays once and finishes, including the force flag."""
import subprocess
from lua_bridge import create_lua, table_value
from probe_project import isolated_project, clean_project
from port_common import ROOT, archive_dir, atomic_bytes, read_json, write_json

PROBE=r'''*top
[debug mode="0" level="3"]
[lua]
local host,providers,state,began,duration
local timeline,last_sample={},-1000
function voice_start(e)
  host=assert(loadstring(e:file('runtime/artemis_audio.lua')))().install(e)
  local media=assert(loadstring(e:file('runtime/majiro_media.lua')))()
  providers={};state={settings={voicevolume=1000,musicvolume=1000,soundvolume=1000},
    media_providers=providers,pending_voice_skip=true}
  local assets=assert(loadstring(e:file('scripts/audio_catalogue.lua')))()
  local name='01ruri_0334'
  local asset=assert(assets[name..'.ogg'])
  duration=asset.duration_ms
  media.install(function(n,f)providers[n]=f end,state,{
    now=function()return e:now()end,fast_mode=function()return true end,
    resource_names={[name..'.ogg']=true},
    media_asset=function()return asset end,
    audio=function(action,spec)return host.command(action,spec)end})
  providers['$voice']({},{name})
  assert(host.state.tracks.ruri_audio_voice==nil,'tag played early')
  providers['$voice_raw']({},{name,1})
  assert(not state.media.channels.voice.loop,'force flag became looping')
  began=e:now()
  e:setEventHandler{onEnterFrame='voice_frame'}
end
function voice_frame(e)
  local elapsed=e:now()-began
  if elapsed-last_sample>=500 then
    last_sample=elapsed
    e:tag{'var',name='t.voice',system='get_sound_info',id='ruri_audio_voice'}
    local playing=tonumber(e:var('t.voice.playing'))or 0
    local track=host.state.tracks.ruri_audio_voice
    timeline[#timeline+1]='{ms='..elapsed..',playing='..playing..',finished='..tostring(track.finished==true)..'}'
    if elapsed>=duration+300 and
       ((playing==0 and track.finished)or elapsed>=duration+5000)then
    local f=assert(io.open('voice_result.lua','wb'))
    f:write('return {playing='..playing..',finished='..tostring(track.finished==true)..
      ',elapsed='..(e:now()-began)..',expected='..duration..',loop='..
      tostring(state.media.channels.voice.loop)..',timeline={'..table.concat(timeline,',')..'}}')
    f:close();host.command('stop',state.media.channels.voice);e:tag{'exit'}
    end
  end
  e:tag{'flip'}
end
[/lua]
[calllua function="voice_start"]
[stop]
'''


def run():
    evidence=archive_dir("voice_native")
    project=isolated_project(ROOT/"projects/windows",evidence/"probe")
    settings=(project/"system.ini").read_text(encoding="utf-8").replace(
        "BOOT = system/first.iet","BOOT = voice_probe.iet")
    atomic_bytes(project/"system.ini",settings.encode("utf-8"))
    atomic_bytes(project/"voice_probe.iet",PROBE.encode("utf-8"))
    startup=subprocess.STARTUPINFO()
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    try:
        with(evidence/"20261109_engine.txt").open("wb")as log:
            process=subprocess.Popen([str(project/"Ruri_Artemis.exe")],cwd=project,
                startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
            try:code=process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
                raise RuntimeError("native voice audition timed out")
        path=next((p for p in(project/"voice_result.lua",project/"savedata-dev/voice_result.lua")
                   if p.is_file()),None)
        assert code==0 and path,str(evidence)
        result=table_value(create_lua().execute(path.read_text(encoding="utf-8")))
        assert result["playing"]==0 and result["finished"]and not result["loop"],result
        report={"status":"passed","build_id":read_json(ROOT/"reports/windows_build.json")["build_id"],
                "result":result,"evidence":str(evidence),"audio_copies":0,
                "scope":"actual Artemis SE decoder finish; deferred tag followed by force-once raw audition"}
        write_json(ROOT/"reports/native_voice_semantics.json",report)
        print("native voice audition: one-shot, force and natural completion passed")
        return report
    finally:clean_project(project)


if __name__=="__main__":run()
