-- Channel ownership is explicit. Playback requires an actual host provider.
local M={}
function M.install(define,state,options)
  state.media={channels={},movie=nil,generation=0}
  local media=state.media
  local voice_profile=options.voice_controls or{marker='exact',force_overrides_skip=true}
  local function voice_marker(name)
    return(voice_profile.marker=='prefix'and name:sub(1,2)=='##')
      or(voice_profile.marker=='exact'and name=='##nw##')
  end
  local wall_now=options.wall_now or options.now
  local function resolve_audio(name)
    -- Original _splitpath retains the stem before trying WAV/OGG preference.
    local alias=options.audio_aliases and options.audio_aliases[name:lower()]
    local base=(alias or name):gsub('\\','/'):match('[^/]+$')or''
    local stem=base:gsub('%.[^%.]*$',''):lower()
    for _,extension in ipairs((options.audio_controls or{}).extensions or{'.wav','.ogg'})do
      local candidate=stem..extension
      if options.resource_names[candidate]then return candidate end
    end
  end
  local function refresh_audio(vm,spec)
    -- Cache paths belong to one host session. Old saves retain name/loop_name;
    -- new saves also retain the exact resolved members, including aliases.
    local resource=spec.resource or(spec.name and resolve_audio(spec.name))
    if not resource then
      if spec.name and options.media_asset then vm:fail('restored audio resource missing: '..spec.name)end
      return -- Direct-file transports can provide their own durable paths.
    end
    if not options.media_asset then vm:fail('audio resource provider missing')end
    local asset=options.media_asset(resource)
    if not asset then vm:fail('restored audio resource missing: '..resource)end
    local loop_resource=spec.loop_resource or(spec.loop_name and resolve_audio(spec.loop_name))
    local loop_asset
    if spec.loop_file then
      if not loop_resource then vm:fail('restored audio loop resource missing: '..(spec.loop_name or''))end
      loop_asset=options.media_asset(loop_resource)
      if not loop_asset then vm:fail('restored audio loop resource missing: '..loop_resource)end
    end
    spec.resource=resource;spec.file=asset.file;spec.duration_ms=asset.duration_ms
    if loop_asset then
      spec.loop_resource=loop_resource;spec.loop_file=loop_asset.file
      spec.loop_duration_ms=loop_asset.duration_ms
    end
  end
  local function host(vm,action,spec)
    if not options.audio then vm:fail('audio host provider missing: '..action) end
    return options.audio(action,spec)
  end
  local function effective(spec,category)
    category=category or(spec.id=='music'and'musicvolume'or(spec.id:sub(1,5)=='voice'and'voicevolume'or'soundvolume'))
    local a,b=(spec.channel_volume or 1000)%4294967296,(state.settings[category]or 1000)%4294967296
    local low=(a%65536)*(b%65536)
    local high=(math.floor(a/65536)*(b%65536)+(a%65536)*math.floor(b/65536))%65536
    local product=((low+high*65536+2147483648)%4294967296)-2147483648
    local gain=product/1000
    return gain<0 and math.ceil(gain)or math.floor(gain)
  end
  media.volume_changed=function(vm,field)
    for _,spec in pairs(media.channels)do
      if spec.id:sub(1,9)~='sound_ch_'then
        spec.volume=effective(spec);spec.fade_ms=0;host(vm,'volume',spec)
      end
    end
  end
  media.music_stack={}
  local function fast(vm)
    return options.fast_mode and options.fast_mode(vm)or false
  end
  -- The completion latch is independent of the live transport. Resetting it
  -- never restarts, seeks or replaces the stream.
  define('$music_stat_reset',function(vm,args)
    assert(#args==0)
    local spec=media.channels.music
    if spec then
      if host(vm,'stat_reset',spec)==false then vm:fail('music completion reset rejected by audio host')end
      spec.completed_latch=false
    end
    return 0
  end)
  define(0xeeaa9104,function(vm,args)
    assert(#args==1 and type(args[1])=='number'and args[1]%1==0,'music frequency signature')
    local spec=media.channels.music
    if spec then
      spec.frequency=args[1]
      if host(vm,'frequency',spec)==false then vm:fail('music playback frequency rejected by host')end
    end
    return 0
  end)
  define('$music_pause',function(vm,args)
    assert(#args==0)
    local spec=media.channels.music
    if spec and not spec.pause_requested then
      spec.pause_requested=true;spec.resume_requested=false
    end
    return 0
  end)
  define('$music_resume',function(vm,args)
    assert(#args==0)
    local spec=media.channels.music
    if spec and spec.pause_requested then spec.resume_requested=true end
    return 0
  end)
  media.update_pending=function(vm)
    -- Numbered waits use a polling completion latch and can be interrupted by
    -- fast mode. The generic audio wait below continues to own generation.
    local function waiting_audio()
      local wait=vm.waiting
      if wait and wait.kind=='movie'and wait.generation and
         (not media.movie or media.movie.generation~=wait.generation)then
        vm.waiting=nil;return
      end
      if wait and wait.audio_completion_wait then
        local spec=media.channels[wait.channel]
        if not spec or spec.generation~=wait.generation then vm.waiting=nil
        elseif fast(vm)then
          host(vm,'stop',spec);media.channels[wait.channel]=nil;vm.waiting=nil
        elseif host(vm,'completed',spec)then vm.waiting=nil end
      end
    end
    if vm.each_thread then vm:each_thread(waiting_audio)else waiting_audio()end
    local spec=media.channels.music
    if not spec or not spec.pause_requested then return end
    if spec.resume_requested then
      if spec.paused_at~=nil then
        if host(vm,'resume',spec)==false then media.channels.music=nil;return end
        spec.started=spec.started+wall_now()-spec.paused_at;spec.paused_at=nil
      end
      spec.pause_requested=false;spec.resume_requested=false
    elseif spec.paused_at==nil then
      if not host(vm,'playing',spec)then media.channels.music=nil;return end
      if host(vm,'pause',spec)==false then media.channels.music=nil;return end
      spec.paused_at=wall_now()
    end
  end
  media.replay=function(vm,spec)
    refresh_audio(vm,spec)
    local elapsed=math.max(0,(spec.paused_at or wall_now())-spec.started)
    -- Slot restore preserves current process-wide master settings.
    spec.volume=effective(spec)
    local request={}
    for key,value in pairs(spec)do request[key]=value end
    if spec.loop_file and elapsed>=spec.duration_ms then
      request.file=spec.loop_file;request.loop_file=nil;request.loop=true
      request.duration_ms=spec.loop_duration_ms or spec.duration_ms
      request.offset_ms=elapsed-spec.duration_ms
      if(spec.loop_duration_ms or 0)>0 then request.offset_ms=request.offset_ms%spec.loop_duration_ms end
    else
      request.offset_ms=elapsed
      if spec.loop and not spec.loop_file and spec.duration_ms>0 then
        request.offset_ms=elapsed%spec.duration_ms
      end
    end
    if host(vm,'play',request)==false then vm:fail('audio replay rejected by host')end
    if spec.frequency and host(vm,'frequency',spec)==false then
      vm:fail('restored music frequency rejected by host')
    end
    if spec.paused_at~=nil then
      if host(vm,'pause',spec)==false then vm:fail('restored audio pause rejected by host')end
    end
  end
  define('$music_push',function(vm,args)
    assert(#args==0)
    local spec=media.channels.music
    media.music_stack[#media.music_stack+1]=spec or false
    if spec then host(vm,'stop',spec);media.channels.music=nil end
    return 0
  end)
  define('$music_pop',function(vm,args)
    assert(#args==0)
    local spec=media.music_stack[#media.music_stack]
    if spec==nil then vm:fail('music stack underflow')end
    if spec then refresh_audio(vm,spec)end
    table.remove(media.music_stack)
    local current=media.channels.music
    if current then host(vm,'stop',current)end
    media.channels.music=spec or nil
    if spec then
      spec.started=wall_now();host(vm,'play',spec)
      if spec.frequency and host(vm,'frequency',spec)==false then
        vm:fail('popped music frequency rejected by host')
      end
    end
    return 0
  end)
  local function exists(args)
    assert(#args==1);return resolve_audio(args[1])~=nil
  end
  define('$sound_is_exist',function(vm,args)return exists(args) and 1 or 0 end)
  define('$voice_is_exist',function(vm,args)return exists(args) and 1 or 0 end)
  local channel_names={'music','voice','sound','sound2','sound3','sound4'}
  local players,stoppers={},{}
  for i=0,19 do
    channel_names[#channel_names+1]='voice_ch_'..i
    channel_names[#channel_names+1]='sound_ch_'..i
  end
  for _,name in ipairs(channel_names) do
    local channel=name
    local voice=channel:sub(1,5)=='voice'
    local sound=channel:sub(1,5)=='sound'
    local numbered_sound=channel:sub(1,9)=='sound_ch_'
    local function stop(vm,args,keep_level)
      assert(#args==0)
      local spec=media.channels[channel]
      if spec then host(vm,'stop',spec);media.channels[channel]=nil end
      if channel=='voice'and not keep_level then media.voice_volume=1000 end
      if sound and not keep_level then
        if numbered_sound then media.sound_ch_volume=1000
        else media[channel..'_volume']=1000 end
      end
      return 0
    end
    stoppers[channel]=function(vm,args)return stop(vm,args,true)end
    define('$'..channel..'_stop',stop)
    define('$'..channel..'_fadeout',function(vm,args)
      assert(#args==1)
      local spec=media.channels[channel]
      if spec then
        spec.fade_ms=math.max(0,args[1])
        if numbered_sound and options.fast_mode and options.fast_mode(vm)then spec.fade_ms=0 end
        host(vm,'fadeout',spec)
        media.channels[channel]=nil
      end
      if numbered_sound then media.sound_ch_volume=1000 end
      return 0
    end)
    define('$'..channel..'_vol',function(vm,args)
      assert(#args==1)
      if numbered_sound and options.audio_disabled then return 0 end
      local spec=media.channels[channel]
      if spec then spec.channel_volume=args[1];spec.volume=effective(spec,numbered_sound and'voicevolume'or nil);spec.fade_ms=0;host(vm,'volume',spec)end
      if numbered_sound then media.sound_ch_volume=args[1]
      else media[channel..'_volume']=args[1]end
      return 0
    end)
    define('$'..channel..'_fade',function(vm,args)
      assert(#args==2)
      local spec=media.channels[channel]
      if spec then
        spec.channel_volume=args[1];spec.volume=effective(spec,numbered_sound and'voicevolume'or nil)
        spec.fade_ms=numbered_sound and math.max(1,args[2])or args[2];host(vm,'volume',spec)
      end
      return 0
    end)
    define('$'..channel..'_pan',function(vm,args)
      assert(#args==1 and type(args[1])=='number')
      local value=numbered_sound and args[1]or math.max(-10000,math.min(10000,args[1]))
      if numbered_sound then media.sound_ch_pan=value end
      local spec=media.channels[channel]
      if spec then spec.pan=value;host(vm,'pan',spec)
      else media[channel..'_pan']=value end
      return 0
    end)
    define('$'..channel..'_wait',function(vm,args)
      assert(#args==0)
      local spec=media.channels[channel]
      if spec and host(vm,'playing',spec)then
        vm.waiting={kind='audio',channel=channel,generation=spec.generation,
          until_ms=not spec.loop and spec.paused_at==nil and(spec.started+spec.duration_ms)or nil}
      end
      return 0
    end)
    define('$'..channel..'_stat',function(vm,args)
      assert(#args==0)
      if numbered_sound and options.audio_disabled then return 0 end
      local spec=media.channels[channel]
      if not spec then return 0 end
      local playing=host(vm,'playing',spec)
      if not playing then media.channels[channel]=nil;return 0 end
      -- Original 0x485340 reports ordinary playback as 1 regardless of
      -- loop mode. BUTTONMENU's music-out hook advances on any other value.
      return 1
    end)
    define('$'..channel..'_is_loop',function(vm,args)
      assert(#args==0);local spec=media.channels[channel];return spec and spec.loop and 1 or 0
    end)
    define('$'..channel..'_get_fn$',function(vm,args)
      assert(#args==0)
      if voice or numbered_sound then
        return media[channel..'_requested']or''
      end
      local spec=media.channels[channel];return spec and spec.name or ''
    end)
    local function play(vm,args,loop_name,extended,voice_options)
      assert(#args>=1 and #args<=2 and type(args[1])=='string','audio play signature')
      if sound then
        if options.audio_disabled then return 0 end
        stop(vm,{},true)
        if numbered_sound then media[channel..'_requested']=args[1]end
      end
      local voice_gain
      if voice then
        if not(voice_options and voice_options.preserve_request)then media[channel..'_requested']=args[1]end
        if voice_marker(args[1])then return 0 end
        voice_gain=media[channel..'_volume']or 1000
        media[channel..'_volume']=1000;media.voice_previous_volume=voice_gain
        stop(vm,{},true)
      end
      if args[1]==''and not voice and not numbered_sound and not(sound and extended)then return stop(vm,{}) end
      local name=resolve_audio(args[1])
      local function missing(request)
        -- Original production paths tolerate references absent from the
        -- actual ARC set. Music keeps its previous stream; voice/SE stop
        -- their previous stream before lookup. A present-but-unprepared
        -- member remains an error in media_asset below.
        if channel~='music'then stop(vm,{},true)end
        media.missing_files=media.missing_files or{}
        media.missing_files[request]=(media.missing_files[request]or 0)+1
        media.missing_requests=media.missing_requests or{}
        if #media.missing_requests<1000 then
          local frame=vm.frame and vm:frame()
          media.missing_requests[#media.missing_requests+1]={channel=channel,name=request,
            script=frame and frame.script.name,offset=vm.current_offset}
        end
        if numbered_sound then media.sound_ch_volume=1000;media.sound_ch_pan=0 end
        return 0
      end
      if not name then return missing(args[1])end
      if voice and((voice_options and not voice_options.allowed)
        or options.audio_disabled
        or(((args[2]or 0)==0 or(channel=='voice'and not voice_profile.force_overrides_skip))
          and options.fast_mode and options.fast_mode(vm)))then return 0 end
      local loop_source
      if loop_name and loop_name~=''then
        loop_source=resolve_audio(loop_name)
        if not loop_source then return missing(loop_name)end
      end
      local channel_volume=voice_gain or(numbered_sound and media.sound_ch_volume)or media[channel..'_volume']or 1000
      local channel_pan=(numbered_sound and media.sound_ch_pan)or media[channel..'_pan']or 0
      if sound then
        if numbered_sound then media.sound_ch_volume=1000;media.sound_ch_pan=0
        else media[channel..'_volume']=1000;media[channel..'_pan']=0 end
        if state.settings.ultra_fast_mode or(state.settings.soundvolume or 0)==0 then return 0 end
      end
      if not options.media_asset then vm:fail('audio resource provider missing')end
      local asset=options.media_asset(name)
      if not asset then vm:fail('audio resource missing: '..args[1])end
      local loop_asset
      if loop_source then
        loop_asset=options.media_asset(loop_source)
        if not loop_asset then vm:fail('audio loop resource missing: '..loop_name)end
      end
      media.generation=media.generation+1
      local loop=args[2]
      if loop==nil then loop=channel=='music' and 1 or 0 end
      if voice then loop=0 end
      local spec={id=channel,name=args[1],resource=name,file=asset.file,duration_ms=asset.duration_ms,
        loop=loop~=0,generation=media.generation,started=wall_now(),channel_volume=channel_volume,
        pan=channel_pan}
      if voice then spec.force=(args[2]or 0)~=0 end
      if loop_asset then
        spec.loop=true;spec.loop_name=loop_name;spec.loop_resource=loop_source;spec.loop_file=loop_asset.file
        spec.loop_duration_ms=loop_asset.duration_ms
      end
      spec.volume=effective(spec)
      host(vm,'play',spec);media.channels[channel]=spec;return 0
    end
    players[channel]=play
    define('$'..channel,play)
  end
  define('$extmusic',function(vm,args)
    -- Original 0x46D2C0: first file once, then repeat the second file.
    assert(#args==2 and type(args[1])=='string'and type(args[2])=='string')
    return players.music(vm,{args[1],1},args[2])
  end)
  local function replace_music(vm,first,second,flags)
    local source=resolve_audio(first)
    local loop_source=second~=''and resolve_audio(second)or nil
    if not source or(second~=''and not loop_source)then
      media.missing_files=media.missing_files or{}
      local missing=not source and first or second
      media.missing_files[missing]=(media.missing_files[missing]or 0)+1
      return 0
    end
    media.music_replacement_name=first;media.music_replacement_loop=second
    media.music_replacement_flags=flags
    local current=media.channels.music
    if not current or options.audio_disabled then return 0 end
    if not host(vm,'playing',current)then return 0 end
    if not options.media_asset then vm:fail('audio resource provider missing')end
    local asset=options.media_asset(source)
    if not asset then vm:fail('audio replacement resource missing: '..first)end
    local loop_asset=loop_source and options.media_asset(loop_source)or nil
    if loop_source and not loop_asset then vm:fail('audio replacement loop resource missing: '..second)end
    local replacement={}
    for key,value in pairs(current)do replacement[key]=value end
    replacement.name=first;replacement.resource=source;replacement.file=asset.file;replacement.duration_ms=asset.duration_ms
    replacement.loop=flags~=0;replacement.loop_name=second~=''and second or nil
    replacement.loop_file=loop_asset and loop_asset.file or nil
    replacement.loop_resource=loop_source
    replacement.loop_duration_ms=loop_asset and loop_asset.duration_ms or nil
    replacement.started=wall_now();replacement.completed_latch=false
    replacement.replace_requested=true;replacement.offset_ms=0
    if current.paused_at~=nil then replacement.paused_at=wall_now()end
    -- Same original handle, hence same generation. Old completion callbacks
    -- must be rejected by the host's per-player ownership token.
    if host(vm,'replace',replacement)==false then vm:fail('audio replacement rejected by host')end
    if replacement.frequency and host(vm,'frequency',replacement)==false then
      vm:fail('replacement music frequency rejected by host')
    end
    media.channels.music=replacement
    return 0
  end
  define('$music_replace',function(vm,args)
    assert((#args==1 or #args==2)and type(args[1])=='string'
      and(args[2]==nil or type(args[2])=='number'),'music replacement signature')
    return replace_music(vm,args[1],'',args[2]==nil and 1 or args[2])
  end)
  define('$extmusic_replace',function(vm,args)
    assert(#args==2 and type(args[1])=='string'and type(args[2])=='string',
      'extended music replacement signature')
    return replace_music(vm,args[1],args[2],1)
  end)
  define('$music_fadeout_wait',function(vm,args)
    assert(#args==1 and type(args[1])=='number','music fade wait signature')
    local duration=fast(vm)and 0 or args[1]
    local spec=media.channels.music
    if spec then
      spec.fade_ms=math.max(0,duration)
      host(vm,duration==0 and'stop'or'fadeout',spec)
      media.channels.music=nil
    end
    if duration~=0 then
      media.music_volume=1000
      media.music_replacement_name='';media.music_replacement_loop=''
      vm.waiting={kind='audio',until_ms=wall_now()+duration,audio_fade_wait=true}
    end
    return 0
  end)
  for _,channel in ipairs{'sound','sound2','sound3','sound4'}do
    local id=channel
    define('$ext'..id,function(vm,args)
      assert(#args==2 and type(args[1])=='string'and type(args[2])=='string','extended sound signature')
      return players[id](vm,{args[1],1},args[2],true)
    end)
  end
  local function flag(value)
    if value==true then return 1 end
    return value or 0
  end
  media.voice_tag=function(vm,name,force,mode,male,skip)
    if name~=''and not voice_marker(name)then state.voice_for_history=name end
    local allowed=skip==0
    if mode==0 and allowed then
      local talk=male~=0 and state.settings.talk_male or state.settings.talk_female
      allowed=talk~=0 and(state.settings.voicevolume or 0)~=0
    end
    return players.voice(vm,{name,force},nil,nil,{preserve_request=true,allowed=allowed})
  end
  media.text_voice=function(vm,tag,mode)
    assert(#tag>=2,'voice text tag requires a flag byte')
    local force=tag:byte(2)-48
    local result=media.voice_tag(vm,tag:sub(3),force,mode,
      flag(state.pending_voice_male),flag(state.pending_voice_skip))
    state.pending_voice_male=false
    local skip=tonumber((options.environment or{}).VOICESKIPDEF)or 0
    state.pending_voice_skip=skip~=0 and skip or false
    return result
  end
  define('$voice',function(vm,args)
    assert(#args>=1 and #args<=2 and type(args[1])=='string')
    if options.audio_disabled then return 0 end
    media.voice_requested=args[1]
    return media.text_voice(vm,'v'..string.format('%01d',args[2]or 0)..args[1],0)
  end)
  for _,operation in ipairs{'play','stop','stat','vol','fade','fadeout','wait','pan'}do
    local op=operation
    define('$voice_ch'..(op=='play' and '' or '_'..op),function(vm,args)
      assert(#args>=1 and args[1]>=0 and args[1]<=19,'voice channel')
      local key='$voice_ch_'..args[1]..(op=='play' and '' or '_'..op)
      local values={};for i=2,#args do values[i-1]=args[i]end
      return assert(state.media_providers[key],'voice provider missing')(vm,values)
    end)
  end
  define('$voice_ch_get_fn$',function(vm,args)
    assert(#args==1 and args[1]%1==0 and args[1]>=0 and args[1]<=19,'voice channel')
    return media['voice_ch_'..args[1]..'_requested']or''
  end)
  for _,operation in ipairs{'play','stop','stat','vol','fade','fadeout','wait','pan','get_fn$'}do
    local op=operation
    define('$sound_ch'..(op=='play'and''or'_'..op),function(vm,args)
      assert(#args>=1 and args[1]%1==0 and args[1]>=0 and args[1]<=19,'sound channel')
      local key='$sound_ch_'..args[1]..(op=='play'and''or'_'..op)
      local values={};for i=2,#args do values[i-1]=args[i]end
      return assert(state.media_providers[key],'sound provider missing')(vm,values)
    end)
  end
  define('$sound_ch_wait',function(vm,args)
    assert(#args==1 and args[1]%1==0 and args[1]>=0 and args[1]<=19,'sound channel wait')
    local channel='sound_ch_'..args[1]
    local spec=media.channels[channel]
    if not spec then return 0 end
    if fast(vm)then host(vm,'stop',spec);media.channels[channel]=nil;return 0 end
    if host(vm,'playing',spec)and not host(vm,'completed',spec)then
      vm.waiting={kind='audio',channel=channel,generation=spec.generation,
        audio_completion_wait=true,until_ms=wall_now()+10}
    end
    return 0
  end)
  define('$voice_raw',function(vm,args)
    -- Original 0x46EFA0 passes the explicit filename to 0x46CE90.
    -- Resource resolution and transport are shared; no text lookup occurs.
    assert(#args==1 or #args==2)
    if options.audio_disabled then return 0 end
    return players.voice(vm,args)
  end)
  define('$movie_volume',function(vm,args)
    assert(#args==1)
    media.movie_volume=math.max(0,math.min(1000,args[1]))
    if media.movie then
      media.movie.volume=media.movie_volume;options.movie('volume',media.movie)
    end
    return 0
  end)
  define('$movie_play',function(vm,args)
    assert(#args>=1 and #args<=6 and type(args[1])=='string')
    for i=2,#args do assert(type(args[i])=='number','movie argument must be numeric')end
    local key=args[1]:gsub('\\','/'):match('[^/]+$'):lower()
    local asset=options.movies and options.movies[key]
    if not asset then vm:fail('movie resource missing: '..args[1])end
    if not options.movie then vm:fail('movie host provider missing')end
    media.generation=media.generation+1
    local spec={file=asset.file,name=args[1],started=wall_now(),duration_ms=asset.duration_ms,
      volume=media.movie_volume or 1000,skippable=true,generation=media.generation,
      clear=args[2]==nil and 1 or args[2],x=args[3]or 0,y=args[4]or 0,
      width=args[5]or options.width,height=args[6]or options.height}
    if options.movie('play',spec)==false then vm:fail('movie playback rejected by host')end
    if media.movie_texture then state.pages[media.movie_texture.page]=nil;media.movie_texture=nil end
    media.movie=spec
    return 0
  end)
  local function movie_variant(vm,args,range)
    local minimum=range and 3 or 1
    local maximum=range and 8 or 6
    assert(#args>=minimum and #args<=maximum and type(args[1])=='string','movie variant signature')
    for i=2,#args do assert(type(args[i])=='number','movie argument must be numeric')end
    local key=args[1]:gsub('\\','/'):match('[^/]+$'):lower()
    local asset=options.movies and options.movies[key]
    if not asset then vm:fail('movie resource missing: '..args[1])end
    if not options.movie then vm:fail('movie host provider missing')end
    local base=range and 4 or 2
    media.generation=media.generation+1
    local spec={file=asset.file,name=args[1],started=wall_now(),duration_ms=asset.duration_ms,
      generation=media.generation,
      volume=media.movie_volume or 1000,skippable=true,repeat_play=not range,
      clear=args[base]==nil and 1 or args[base],x=args[base+1]or 0,y=args[base+2]or 0,
      width=args[base+3]or options.width,height=args[base+4]or options.height}
    if range then
      spec.range_from=args[2];spec.range_to=args[3]
    end
    local action=range and'play_fromto'or'play_repeat'
    if options.movie(action,spec)==false then vm:fail('movie variant rejected by host: '..action)end
    if media.movie_texture then state.pages[media.movie_texture.page]=nil;media.movie_texture=nil end
    media.movie=spec
    -- The native backend starts playback and registers its timer callback;
    -- waiting belongs to the separate movie_wait call.
    return 0
  end
  define('$movie_play_repeat',function(vm,args)return movie_variant(vm,args,false)end)
  define('$movie_play_fromto',function(vm,args)return movie_variant(vm,args,true)end)
  define('$movie_texture_play',function(vm,args)
    assert(#args>=1 and #args<=4 and type(args[1])=='string','movie texture signature')
    for i=2,#args do assert(type(args[i])=='number','movie texture argument must be numeric')end
    if not options.movie then vm:fail('movie host provider missing')end
    local key=args[1]:gsub('\\','/'):match('[^/]+$'):lower()
    local asset=options.movies and options.movies[key]
    if not asset then vm:fail('movie texture resource missing: '..args[1])end
    local spec={file=asset.file,name=args[1],started=wall_now(),duration_ms=asset.duration_ms,
      volume=media.movie_texture_volume or 1000,repeat_play=(args[2]==nil and 1 or args[2])~=0,
      range_from=args[3]or 0,range_to=args[4]==nil and -1 or args[4],texture=true,
      page_hint=state.next_page}
    local accepted=options.movie('texture_play',spec)
    if accepted==false or(type(accepted)=='number'and accepted<0)then
      vm:fail('movie texture playback rejected by host')
    end
    local page=options.movie('texture',spec)
    local width=options.movie('texture_width',spec)
    local height=options.movie('texture_height',spec)
    if type(page)~='number'or page<=0 or page%1~=0 or type(width)~='number'or width<=0
      or type(height)~='number'or height<=0 then
      vm:fail('movie texture host did not provide a decoded drawable page')
    end
    spec.page=page;spec.width=width;spec.height=height
    local previous=media.movie_texture
    if previous and previous.page~=page then state.pages[previous.page]=nil end
    if page>=state.next_page then state.next_page=page+1 end
    state.pages[page]={width=width,height=height,depth=32,movie_texture=true}
    media.movie=nil
    media.movie_texture=spec
    return 0
  end)
  define('$movie_texture',function(vm,args)
    assert(#args==0)
    local spec=media.movie_texture
    if not spec then return 0 end
    if not options.movie then vm:fail('movie host provider missing')end
    local page=options.movie('texture',spec)
    if type(page)~='number'or page<0 or page%1~=0 then vm:fail('invalid movie texture page result')end
    if page==0 then return 0 end
    if page~=spec.page then vm:fail('movie texture changed its drawable page unexpectedly')end
    state.dirty=true
    return page
  end)
  define('$movie_texture_volume',function(vm,args)
    assert(#args==1 and type(args[1])=='number')
    media.movie_texture_volume=args[1]
    local spec=media.movie_texture
    if spec then
      spec.volume=args[1]
      if not options.movie then vm:fail('movie host provider missing')end
      if options.movie('texture_volume',spec)==false then vm:fail('movie texture volume rejected')end
    end
    return 0
  end)
  define('$movie_texture_stop',function(vm,args)
    assert(#args==0)
    local spec=media.movie_texture
    if spec then
      if not options.movie then vm:fail('movie host provider missing')end
      if options.movie('texture_stop',spec)==false then vm:fail('movie texture stop rejected')end
      state.pages[spec.page]=nil;media.movie_texture=nil
    end
    return 0
  end)
  define('$movie_wait',function(vm,args)
    assert(#args<=1)
    if media.movie then
      if not options.movie then vm:fail('movie provider missing')end
      if options.movie('status',media.movie)~=0 then
        vm.waiting={kind='movie',generation=media.movie.generation,
          until_ms=media.movie.started+media.movie.duration_ms}
      end
    end
    return 0
  end)
  define('$movie_stat',function(vm,args)
    assert(#args==0)
    if not media.movie then return 0 end
    if not options.movie then vm:fail('movie provider missing') end
    return options.movie('status',media.movie)
  end)
  define('$movie_stop',function(vm,args)
    assert(#args==0)
    if media.movie then
      if not options.movie then vm:fail('movie provider missing') end
      options.movie('stop',media.movie);media.movie=nil
    end
    return 0
  end)
  return media
end
return M
