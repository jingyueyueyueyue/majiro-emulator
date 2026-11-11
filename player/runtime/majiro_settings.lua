-- All options are retained as actual state; host consumers use the same table.
local M={}
function M.install(define,state,options)
  if options.ultra_fast_mode_available==false then
    local register=define
    local unavailable={['$set_ultra_fast_mode']=true,['$ultra_fast_enable']=true,
      ['$ultra_fast_disable']=true,['$is_ultra_fast_mode']=true,['$reset_ultra_fast_mode']=true}
    define=function(name,provider)
      if not unavailable[name]then register(name,provider)end
    end
  end
  local s=state.settings
  state.registry=options.registry or {}
  s.nanido=tonumber((options.saved_settings or{}).nanido)or tonumber(state.registry.nanido)or 0
  s.game_speed=tonumber((options.saved_settings or{}).game_speed)or tonumber(state.registry.gamespeed)or 1
  define('$get_nanido',function(vm,args)assert(#args==0);return s.nanido end)
  define('$get_game_speed',function(vm,args)assert(#args==0);return s.game_speed end)
  define('$strtime$',function(vm,args)assert(#args==0);return os.date('%H:%M:%S')end)
  define('$strdate$',function(vm,args)assert(#args==0);return os.date('%y/%m/%d')end)
  define('$get_current_time$',function(vm,args)assert(#args==0);return os.date('%m/%d/%y %H:%M:%S')end)
  define(0xf66cd6ea,function(vm,args)
    assert(#args==0)
    if not options.service then vm:fail('system locale host unavailable')end
    return assert(tonumber(options.service('system_lcid','')),'invalid system locale result')
  end)
  define('$joy_is_enable',function(vm,args)assert(#args==0);return tonumber(state.registry.joyuse)or 0 end)
  define('$joy_set_enable',function(vm,args)
    assert(#args==1 and type(args[1])=='number'and args[1]%1==0)
    state.registry.joyuse=args[1];s.joyuse=args[1];state.persist_dirty=true;return args[1]
  end)
  define('$set_watchlist',function(vm,args)
    assert(#args==1 and type(args[1])=='string','watch list string required')
    state.registry.watchlist=args[1]:match('^[^%z]*');state.persist_dirty=true;return 0
  end)
  define('$set_syukusyou',function(vm,args)
    assert(#args<=1 and(args[1]==nil or(type(args[1])=='number'and args[1]%1==0)))
    local previous=tonumber(state.registry.regsyukusyou)or 0
    if args[1]~=nil then
      local value=math.max(0,math.min(2,args[1]))
      if value~=previous then
        if not options.platform then vm:fail('display scale host provider unavailable')end
        local result=options.platform('display_scale',{value})
        assert(result==0,'display scale host operation failed')
        state.registry.regsyukusyou=value;state.persist_dirty=true
      end
    end
    return previous
  end)
  for _,entry in ipairs{{'$full_screen_width',1},{'$full_screen_height',2}}do
    local name,index=entry[1],entry[2]
    define(name,function(vm,args)
      assert(#args==2 and args[1]>0 and args[2]>0,'full screen resolution signature')
      if not options.platform then vm:fail('display modes host unavailable')end
      local value=options.platform('fullscreen_mode',args)
      assert(type(value)=='table'and value[index]>0,'display mode result invalid')
      return value[index]
    end)
  end
  define('$force_key_paste',function(vm,args)
    assert(#args==0)
    if not options.service then vm:fail('clipboard host unavailable')end
    assert(options.service('force_key_paste','')=='OK','paste target unavailable')
    return 0
  end)
  define('$web_access',function(vm,args)
    assert(#args==1 and type(args[1])=='string')
    if not options.service then vm:fail('web browser host unavailable')end
    assert(options.service('open_url','http://'..args[1])=='OK','web browser launch failed')
    return 0
  end)
  define('$open_help',function(vm,args)
    assert(#args==0)
    local path=(options.environment or{}).HELPFILE or(options.environment or{}).HELP
    if not path or path:lower()=='none'then return 0 end
    if not options.service then vm:fail('help document host unavailable')end
    assert(options.service('open_help',path)=='OK','help document launch failed');return 0
  end)
  for _,entry in ipairs{{'$set_clipbord','clipboard_set',1},{'$get_clipbord$','clipboard_get',0},
    {'$clipbord_is_change','clipboard_changed',0}}do
    local name,action,arity=entry[1],entry[2],entry[3]
    define(name,function(vm,args)
      assert(#args==arity and(arity==0 or type(args[1])=='string'),'clipboard signature')
      if not options.service then vm:fail('clipboard host provider unavailable')end
      local value=options.service(action,args[1]or'')
      if action=='clipboard_changed'then
        local number=assert(tonumber(value),'clipboard returned invalid change state')
        return number~=0 and 1 or 0
      end
      if action=='clipboard_get'then
        assert(type(value)=='string','clipboard returned invalid text')
        assert(not options.cp932 or #options.cp932(value)<2032,'clipboard exceeds original read buffer')
        return value
      end
      assert(value=='OK','clipboard write failed');return 0
    end)
  end
  define('$reg_read',function(vm,args)
    assert(#args==1 or #args==2)
    local value=state.registry[args[1]:lower()]
    if value==nil then value=args[2] or 0 end
    return value
  end)
  define('$reg_write',function(vm,args)
    assert(#args==2)
    state.registry[args[1]:lower()]=args[2];state.persist_dirty=true;return 0
  end)
  define('$reg_read$',function(vm,args)
    assert((#args==1 or #args==2)and type(args[1])=='string'
      and(args[2]==nil or type(args[2])=='string'),'reg_read$ signature')
    local value=state.registry[args[1]:lower()]
    if type(value)~='string'then value=args[2]or''end
    assert(not options.cp932 or #options.cp932(value)<2048,'registry string exceeds original read buffer')
    return value:match('^[^%z]*')
  end)
  define('$reg_write_string',function(vm,args)
    assert(#args==2 and type(args[1])=='string'and type(args[2])=='string','reg_write_string signature')
    local key=args[1]:lower()
    local value=args[2]:match('^[^%z]*')
    if key=='fullscreenfit'then
      if not options.screen_fit then vm:fail('screen fit host provider unavailable')end
      options.screen_fit(value)
    end
    state.registry[key]=value;state.persist_dirty=true
    return 0
  end)
  for _,entry in ipairs{{'quickload',2},{'quicksave',0}}do
    local name,fallback=entry[1],entry[2]
    define('$is_'..name..'_failsafe',function(vm,args)
      assert(#args==0);return state.registry[name..'_failsafe']or fallback
    end)
    define('$set_'..name..'_failsafe',function(vm,args)
      assert(#args==1);state.registry[name..'_failsafe']=args[1];state.persist_dirty=true;return 0
    end)
  end
  define('$set_fast_mode_flg',function(vm,args)
    assert(#args==1);s.fast_mode_flag=args[1];s.fast_mode_config=args[1]
    state.registry.fast_mode=args[1];state.persist_dirty=true;return args[1]
  end)
  define('$get_fast_stat',function(vm,args)
    assert(#args==0)
    if state.input.down[17]then return 0 end
    return(s.fast_mode and 1 or 0)+(s.fast_read_mode and 2 or 0)+(s.auto_mode and 4 or 0)
  end)
  define('$set_fast_stat',function(vm,args)
    assert(#args==1);local value=args[1]%8
    s.fast_mode=value%2==1;s.fast_read_mode=math.floor(value/2)%2==1
    s.auto_mode=math.floor(value/4)%2==1;return 0
  end)
  local defaults=options.environment or {}
  local persisted=options.saved_settings or{}
  s.font_name=persisted.font_name
  s.font_proportional=persisted.font_proportional
  local fields={mojispeed='MOJISPEED',autospeed='AUTOSPEED',effectspeed='EFFECTSPEED',
    musicvolume='MUSICVOL',soundvolume='SOUNDVOL',voicevolume='VOICEVOL'}
  for field,source in pairs(fields) do
    local name=field
    s[name]=tonumber(persisted[name])or tonumber(defaults[source]) or (name=='effectspeed' and 1000 or 0)
    define('$set_'..name,function(vm,args)
      assert(#args==1)
      if s[name]~=args[1]then state.persist_dirty=true end
      s[name]=args[1]
      if name:match('volume$')and state.media and state.media.volume_changed then
        state.media.volume_changed(vm,name)
      end
      return 0
    end)
    define('$get_'..name,function(vm,args)assert(#args==0);return s[name]end)
  end
  local function reset_modes()
    s.auto_mode=false;s.fast_mode=false;s.fast_read_mode=false;s.ultra_fast_mode=false
  end
  reset_modes()
  define('$set_auto_mode',function(vm,args)
    -- Original 0x441FB0: no argument; AUTO replaces both skip modes.
    assert(#args==0);reset_modes();s.auto_mode=true;return 0
  end)
  define('$set_fast_mode',function(vm,args)
    assert(#args==0);reset_modes()
    s.fast_read_mode=(s.fast_mode_config or 0)~=0
    s.fast_mode=not s.fast_read_mode;return 0
  end)
  define('$set_fast_mode_force',function(vm,args)
    assert(#args==0);reset_modes();s.fast_mode=true;return 0
  end)
  define('$set_fast_mode_ar',function(vm,args)
    assert(#args==0);reset_modes();s.fast_read_mode=true;return 0
  end)
  define('$set_ultra_fast_mode',function(vm,args)
    assert(#args==0)
    if(s.ultra_fast_allowed or 1)~=0 then reset_modes();s.ultra_fast_mode=true;s.fast_mode=true end
    return 0
  end)
  define('$ultra_fast_enable',function(vm,args)
    assert(#args==0);s.ultra_fast_allowed=1;return 0
  end)
  define('$ultra_fast_disable',function(vm,args)
    assert(#args==0);local previous=s.ultra_fast_allowed or 1
    s.ultra_fast_allowed=0
    return options.ultra_disable_returns_previous~=false and previous or 0
  end)
  define('$is_auto_mode',function(vm,args)assert(#args==0);return s.auto_mode and 1 or 0 end)
  define('$is_ultra_fast_mode',function(vm,args)assert(#args==0);return s.ultra_fast_mode and 1 or 0 end)
  define('$is_fast_mode',function(vm,args)
    assert(#args==0)
    if s.fast_allowed==false then return 0 end
    local page=state.fast_page
    -- Fast text is instantaneous. Only prevent an entire page from being
    -- discarded before the presenter has actually displayed its pixels.
    if state.reading_wait and page and not page.started then return 0 end
    if s.fast_mode or s.ultra_fast_mode then return 1 end
    local read_requested=s.fast_read_mode or state.input.down[17]
    if read_requested and state.current_text_read then return 1 end
    if read_requested then s.fast_read_mode=false end
    return 0
  end)
  define('$reset_fast_mode',function(vm,args)assert(#args==0);reset_modes();return 0 end)
  define('$reset_ultra_fast_mode',function(vm,args)
    assert(#args==0);local was=s.ultra_fast_mode
    if was then reset_modes()end
    return was and 1 or 0
  end)
  define('$reset_auto_mode',function(vm,args)assert(#args==0);s.auto_mode=false;return 0 end)
  for _,entry in ipairs{{'auto_mode_config','auto_mode_config'},{'fast_mode_config','fast_mode_config'},
    {'set_joy','joy'},
    {'set_mouse_capture_left','capture_left'},{'set_mouse_capture_right','capture_right'}} do
    local key=entry[2]
    define('$'..entry[1],function(vm,args)assert(#args==1);s[key]=args[1];return 0 end)
  end
  define('$set_voice_skip',function(vm,args)
    -- Original 0x447440 marks the next text voice tag; its legacy
    -- argument is ignored. The raw voice/voice-channel APIs are separate.
    assert(#args<=1);state.pending_voice_skip=true;return 0
  end)
  define('$set_voice_male',function(vm,args)
    assert(#args<=1);state.pending_voice_male=true;return 0
  end)
  s.voice_setup_levels={}
  for index=0,29 do
    s.voice_setup_levels[index+1]=(persisted.voice_setup_levels or{})[index+1]
      or tonumber(state.registry['voicesetuplvl'..index])or 1000
  end
  s.voice_musicdown=tonumber(persisted.voice_musicdown)or tonumber(state.registry.voicemusicdown)or 0
  s.voice_musicdown_vol=tonumber(persisted.voice_musicdown_vol)
    or tonumber(state.registry.voicemusicdownvol)or 500
  s.talk_male=tonumber(persisted.talk_male)or tonumber(state.registry.talkmale)
    or tonumber(defaults.ISTALKMALE)or 1
  s.talk_female=tonumber(persisted.talk_female)or tonumber(state.registry.talkfemale)
    or tonumber(defaults.ISTALKFEMALE)or 1
  define('$get_voice_setup',function(vm,args)
    assert(#args==1 and type(args[1])=='number'and args[1]%1==0)
    local index=math.max(0,math.min(29,args[1]))
    if s.talk_female==0 then return 0 end
    return s.voice_setup_levels[index+1]
  end)
  define('$is_voice_musicdown',function(vm,args)
    assert(#args==0);return s.voice_musicdown
  end)
  define('$voice_musicdown_vol',function(vm,args)
    assert(#args==0);return s.voice_musicdown_vol
  end)
  for _,entry in ipairs{{'$voice_setup','voice_setup_labels',30},
    {'$voice_text_setup','voice_text_labels',15}}do
    local field,capacity=entry[2],entry[3]
    define(entry[1],function(vm,args)
      assert(#args<=capacity,'voice label table capacity exceeded')
      local values={}
      for index=1,capacity do
        local label=args[index]or''
        assert(type(label)=='string','voice label must be a string')
        assert(not options.cp932 or #options.cp932(label)<128,'voice label exceeds original byte slot')
        values[index]=label
      end
      state[field]=values;return 0
    end)
  end
  define('$set_font_name',function(vm,args)
    assert(#args==1 or #args==2)
    if s.font_name~=args[1]or s.font_proportional~=(args[2]or 0)then state.persist_dirty=true end
    s.font_name=args[1];s.font_proportional=args[2] or 0;return 0
  end)
  define('$get_font_name$',function(vm,args)assert(#args==0);return s.font_name or state.font_cache.face end)
  define('$font_dialog$',function(vm,args)
    assert(#args==2 and type(args[1])=='string')
    if not options.font_dialog then vm:fail('font chooser host provider missing')end
    local face=options.font_dialog(args[1],args[2])
    assert(type(face)=='string'and face~='','font chooser returned no family')
    s.font_name=face;s.font_proportional=args[2];state.persist_dirty=true
    return face
  end)
  define('$screen_mode',function(vm,args)
    assert(#args<=1)
    if #args==1 then
      local desired=args[1]~=0
      if options.screen_mode then s.fullscreen=options.screen_mode(desired) else s.fullscreen=desired end
    end
    return s.fullscreen and 1 or 0
  end)
  for _,entry in ipairs{{'save_enable','save',true},{'save_disable','save',false},
    {'save_menu_enable','save_menu',true},{'save_menu_disable','save_menu',false}} do
    local key,value=entry[2],entry[3]
    define('$'..entry[1],function(vm,args)assert(#args==0);s[key]=value;return 0 end)
  end
  define('$fast_mode_enable',function(vm,args)
    assert(#args==0);s.fast_disable_depth=0;s.fast_allowed=true;return 0
  end)
  define('$fast_mode_disable',function(vm,args)
    assert(#args==0)
    local previous=s.fast_disable_depth or(s.fast_allowed==false and 1 or 0)
    s.fast_disable_depth=((previous+1+2147483648)%4294967296)-2147483648
    s.fast_allowed=s.fast_disable_depth==0
    return previous==0 and 1 or 0
  end)
  define('$mouse_disable',function(vm,args)
    -- Original inline 0x440104 returns -1 if it was enabled, 0 otherwise.
    assert(#args==0);local enabled=s.mouse~=false;s.mouse=false
    return enabled and -1 or 0
  end)
  define('$mouse_enable',function(vm,args)assert(#args==0);s.mouse=true;return 0 end)
  define('$prevsel_disable',function(vm,args)
    -- 0x43E008 returns the previous flag for nested help/menu restoration.
    assert(#args==0);local previous=s.prevsel;s.prevsel=false
    return previous and 1 or 0
  end)
  define('$prevsel_enable',function(vm,args)assert(#args==0);s.prevsel=true;return 0 end)
  define('$is_save_menu_disable',function(vm,args)assert(#args==0);return s.save_menu==false and 1 or 0 end)
  define('$is_testplay',function(vm,args)assert(#args==0);return options.testplay and 1 or 0 end)
  -- Original 0x42FD80/0x42FDB0 suppress these developer diagnostics in
  -- production mode. Missing source pictures then follow the script's
  -- own fallback branch instead of becoming an unimplemented syscall.
  for _,name in ipairs{'report_no_pic','log_no_pic','report_no_voice'}do
    local key=name
    define('$is_'..key,function(vm,args)
      assert(#args==0)
      return options.testplay and (tonumber(options[key])or 0) or 0
    end)
  end
  define('$is_talk_female',function(vm,args)assert(#args==0);return tonumber(defaults.ISTALKFEMALE) or 1 end)
  define('$is_talk_male',function(vm,args)assert(#args==0);return tonumber(defaults.ISTALKMALE)or 1 end)
  define('$is_voice_continue',function(vm,args)assert(#args==0);return tonumber(defaults.ISVOICECONTINUE)or 0 end)
  define('$is_voice_panning',function(vm,args)assert(#args==0);return tonumber(defaults.ISVOICEPANNING) or 0 end)
  return s
end
return M
