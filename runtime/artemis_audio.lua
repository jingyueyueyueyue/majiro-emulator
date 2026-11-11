-- Native channel transport and finish generations. No audio data enters Lua.
local M={}
function M.install(e)
  local state={tracks={}}
  local function gain(volume)
    -- Both Majiro's channel volume and Artemis gain use 0..1000.
    return math.floor(math.max(0,math.min(1000,volume))+0.5)
  end
  local function pan(value)
    value=math.max(-10000,math.min(10000,value or 0))
    if value==0 then return 0 end
    if math.abs(value)==10000 then return value>0 and 1000 or -1000 end
    -- The original forwards DirectSound hundredths-of-dB attenuation.
    local amount=math.floor((1-10^(-math.abs(value)/2000))*1000+0.5)
    return value<0 and -amount or amount
  end
  local command
  _G.ruri_audio_finish=function(engine,param)
    local track=state.tracks[param.ruri_id]
    if track and track.generation==tonumber(param.ruri_generation)and
       track.token==tonumber(param.ruri_token)then
      if track.loop_spec then
        local next_spec={}
        for key,value in pairs(track.loop_spec)do next_spec[key]=value end
        next_spec.file=next_spec.loop_file;next_spec.duration_ms=next_spec.loop_duration_ms
        next_spec.loop_file=nil;next_spec.loop_duration_ms=nil
        command('play',next_spec)
        state.tracks[param.ruri_id].phase='loop'
      else track.finished=true end
    end
  end
  command=function(action,spec)
    local id='ruri_audio_'..spec.id
    local music=spec.id=='music'
    local function tag(name,values)
      values=values or {};values[1]=name
      -- splay/sstop/sfade and their queries address the single BGM stream;
      -- only SE tags have an id parameter (Artemis sound tag reference).
      if not music then values.id=id end
      e:tag(values)
    end
    if action=='play' then
      tag('delonsoundfinish')
      tag(music and 'sstop' or 'sestop',{time=0})
      state.next_token=(state.next_token or 0)+1
      state.tracks[id]={generation=spec.generation,began=e:now(),duration=spec.duration_ms,loop=spec.loop,
        token=state.next_token,loop_spec=spec.loop_file and spec or nil,
        phase=spec.loop_file and'intro'or'normal'}
      tag(music and 'splay' or 'seplay',{file=spec.file,
        loop=spec.loop and not spec.loop_file and 1 or 0,gain=gain(spec.volume),pan=pan(spec.pan),time=0,skippable=0,
        buffer=spec.id:sub(1,5)=='voice'and -1 or nil})
      tag('setonsoundfinish',{handler='calllua',['function']='ruri_audio_finish',
        ruri_id=id,ruri_generation=spec.generation,ruri_token=state.next_token})
      return true
    elseif action=='stop' then
      tag(music and 'sstop' or 'sestop',{time=0})
      tag('delonsoundfinish');state.tracks[id]=nil;return true
    elseif action=='fadeout' then
      tag(music and 'sstop' or 'sestop',{time=spec.fade_ms})
      tag('delonsoundfinish');state.tracks[id]=nil;return true
    elseif action=='volume' then
      tag(music and 'sfade' or 'sefade',{gain=gain(spec.volume),time=spec.fade_ms})
      return true
    elseif action=='pan'then
      tag(music and 'span'or'sepan',{pan=pan(spec.pan),time=0})
      return true
    elseif action=='playing' then
      local track=state.tracks[id]
      if not track or track.finished then return false end
      tag('var',{name='t.ruri_audio_query',system='get_sound_info'})
      local playing=tonumber(e:var('t.ruri_audio_query.playing'))
      e:tag{'var',name='t.ruri_audio_query',system='delete'}
      if playing==1 or playing==2 then return true end
      if playing==0 and e:now()-track.began>=100 then return false end
      if not track.loop and e:now()-track.began>track.duration+500 then return false end
      return true
    end
    error('unsupported audio action '..action)
  end
  return {command=command,state=state}
end
return M
