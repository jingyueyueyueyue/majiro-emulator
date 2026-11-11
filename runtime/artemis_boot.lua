-- Real original-script execution and presentation inside Artemis.
local M={}
local context
local function module(e,path)
  return assert(loadstring(assert(e:file(path),'missing '..path),path))()
end
local function serialize(value)
  if type(value)=='string' then return string.format('%q',value)end
  if type(value)=='number' or type(value)=='boolean' then return tostring(value)end
  if value==nil then return 'nil'end
  local values={}
  for k,v in pairs(value)do values[#values+1]='['..serialize(k)..']='..serialize(v)end
  return '{'..table.concat(values,',')..'}'
end
local function label(e,id,text,x,y,size,color)
  e:tag{'chgmsg',id=id,layered=1};e:tag{'rp'}
  e:tag{'font',stack=0,face='font/SourceHanSansSC-Regular.otf',size=size,
    left=0,top=0,width=1180,height=90,color=color or 'FFFFFF',overflow=1,
    shadow=0,outline=0,alpha=255,entirealpha=255}
  e:tag{'print',data=text};e:tag{'/chgmsg'}
  e:tag{'lyprop',id='~'..id,left=x,top=y,visible=1,alpha=255,anchorx=0,anchory=0}
end
local function now()
  -- Original Majiro's timer reads one cached timestamp per engine tick.
  return context.clock
end
local function result()
  local c=context;local vm=c.vm;local names={};local libraries=0
  for name in pairs(vm.scripts)do names[#names+1]=name end;table.sort(names)
  for _ in pairs(vm.libraries)do libraries=libraries+1 end
  local state=c.system.state
  return {schema=2,host='native Artemis',status=
    (state.ui_waiting and vm.status~='blocked') and 'waiting_for_input' or vm.status,
    steps=vm.steps,problem=vm.problem,loaded_scripts=names,library_count=libraries,
    calls=state.calls,build_id=c.build_id,release_allowed=false,simulated_ms=now(),
    ui_areas=state.ui_areas,presented_frames=c.presenter.state.frames,
    console=state.console,reading_wait=state.reading_wait,scenario=c.probe_scenario,
    navigation_phase=c.navigation_phase,navigation_trace=c.navigation_trace,media=state.media.channels,
    save_probe_phase=c.save_probe_phase,last_saved_slot=state.last_saved_slot,
    settings=state.settings,reading_generation=state.reading_generation,
    load_generation=state.load_generation,save_stats=c.system.saves.stats(),
    display_text=c.localization.display(state.console.text),
    audio_transport=c.audio.state}
end
local function write_result()
  local out=assert(io.open('native_result.lua','wb'))
  out:write('return '..serialize(result())..'\n');out:close()
end
local function diagnostic(e,message)
  local c=context
  e:tag{'lyc',id='900',width=1280,height=430,color='EB101621'}
  e:tag{'lyprop',id='900',left=0,top=290,visible=1,anchorx=0,anchory=0}
  label(e,'ruri_title',c.ui.title,55,304,32)
  label(e,'ruri_status',c.ui.blocked,55,358,30,'FFE5B4')
  label(e,'ruri_counts',c.ui.steps..' '..c.vm.steps,55,408,23)
  label(e,'ruri_detail',message,55,454,21)
  label(e,'ruri_subtitle',c.ui.subtitle,55,565,23)
  label(e,'ruri_keys',c.ui.retry,55,657,22,'B6DAFF')
end
local function start_vm(e)
  local c=context
  for _,id in ipairs{'900','~ruri_title','~ruri_status','~ruri_counts','~ruri_detail',
    '~ruri_subtitle','~ruri_keys'}do e:tag{'lydel',id=id}end
  c.clock=0;c.began=e:now();c.settled=nil;c.probe_complete=false;c.navigation_phase=nil;c.navigation_trace={}
  c.probe_poll=false
  local native_module=module(e,'runtime/artemis_native_library.lua')
  local function native(request)
    local began=c.profile and c.profile.began and e:now()
    e:tag{'callnative',module=native_module,method='RuriPixels',
      param=request,result='t.ruri_pixels'}
    local value=e:var('t.ruri_pixels')
    if began then
      local key='native:'..(request:match('^(%w+)')or'unknown')
      local entry=c.profile.costs[key]or{count=0,total=0,max=0}
      local elapsed=e:now()-began
      entry.count=entry.count+1;entry.total=entry.total+elapsed;entry.max=math.max(entry.max,elapsed)
      c.profile.costs[key]=entry
    end
    if type(value)~='string' or value:sub(1,2)~='OK' then error('native pixels: '..tostring(value))end
    return value
  end
  native('reset')
  native('fontfile font/SourceHanSansSC-Regular.otf')
  c.store=module(e,'runtime/majiro_store.lua').new(module(e,'runtime/majiro_codec.lua'),native,
    c.save_identity,c.save_compatible_identities)
  local persisted=c.store:load_index().profile or{}
  c.presenter=module(e,'runtime/artemis_presenter.lua').install(e,native,'savedata-dev',
    c.localization.display,c.profile~=nil)
  c.system=module(e,'runtime/majiro_syscalls.lua').new{
    names=c.names,config_files=c.config_files,width=1280,height=720,
    image_metadata=c.image_metadata,environment=c.environment,config_lines=c.config_lines,
    resource_names=c.resource_names,modules=function(path)return module(e,path)end,
    cp932=c.localization.cp932,display_text=c.localization.display,font_face='Source Han Sans SC',
    source_identity=c.save_identity,store=c.store,registry=persisted.registry,progress=persisted.progress,
    saved_settings=persisted.settings,
    preference_tokens=not persisted.persistent_globals and persisted.preference_tokens or nil,
    movies=c.movies,audio_aliases=c.audio_aliases,
    movie=function(action,spec)
      if action=='status'then
        if c.probe then return now()<spec.started+spec.duration_ms and 1 or 0 end
        return c.active_movie and 1 or 0
      elseif action=='play'then
        if c.probe then return true end
        c.active_movie=spec
        e:tag{'var',name='s.videovol',data=spec.volume}
        e:tag{'var',name='t.ruri_movie_file',data=spec.file}
        e:tag{'call',file='system/movie.iet'}
      elseif action=='volume'then e:tag{'var',name='s.videovol',data=spec.volume}
      elseif action=='stop'then c.active_movie=nil end
      return true
    end,
    set_clock=function(value)c.clock=value;c.began=e:now()-value end,
    media_asset=function(name)
      local key=name:lower()
      local value=c.audio_assets[key] or c.audio_assets[key..'.ogg'] or c.audio_assets[key..'.wav']
      if not value then error('audio not prepared: '..name)end
      return value
    end,
    audio=function(action,spec)
      -- Virtual-time navigation must use the same clock as the model.
      -- Sound still goes through the real transport; a separate CoreAudio
      -- check measures actual BGM playback without virtual-time assumptions.
      if c.probe and action=='playing' then
        return spec.loop or now()-spec.started<spec.duration_ms
      end
      return c.audio.command(action,spec)
    end,
    screen_mode=function(desired)
      e:tag{'var',name='t.ruri_fullscreen',system='fullscreen'}
      local current=tonumber(e:var('t.ruri_fullscreen'))==1
      if current~=desired then e:tag{'exec',command='fullscreen'}end
      return desired
    end,
    font_dialog=function(face,proportional)
      local reply=native('font_dialog\n'..face)
      return reply:match('^OK selected\n(.+)$')or face
    end,
    now=now,
    asset=function(name)
      local key=c.resource_key(name)
      local value=c.images_lower[key] or c.images_lower[key..'.rct'] or c.images_lower[key..'.rc8']
      if not value then error('image not prepared: '..name)end
      return value
    end,
    graphics=native,
    wait_for_present=function()return not c.probe end,
  }
  local invoke=c.system.invoke
  if c.profile then
    invoke=function(vm,hash,args,wants)
      local began=c.profile.began and e:now()
      local value,deferred=c.system.invoke(vm,hash,args,wants)
      if began then
        local key=c.names[hash]or string.format('%08x',hash)
        local entry=c.profile.costs[key]or{count=0,total=0,max=0}
        local elapsed=e:now()-began
        entry.count=entry.count+1;entry.total=entry.total+elapsed;entry.max=math.max(entry.max,elapsed)
        c.profile.costs[key]=entry
      end
      return value,deferred
    end
  end
  c.vm=c.vm_module.new{opcodes=c.opcodes,syscall=invoke,after_init=c.system.after_init,
    restart_on_return=true,
    persistent_globals=persisted.persistent_globals,persistent_changed=c.system.persistent_changed,
    script_loaded=c.system.script_loaded,
    resume_action=c.system.resume_action,
    wall_deadline=c.system.wall_deadline,
    string_ops=c.system.strings,text_ops=c.system.text,line_changed=c.system.line_changed,loader=function(name)
      local record=c.script_names[name:lower()]
      if not record then error('script missing: '..name)end
      return module(e,'scripts/'..record.id..'.lua')
    end}
  c.vm:begin(c.catalogue.entry)
end
function M.start(e)
  context={engine=e,ui=module(e,'config/ui_zh.lua'),catalogue=module(e,'scripts/catalogue.lua'),
    opcodes=module(e,'scripts/opcodes.lua'),names=module(e,'scripts/syscall_names.lua'),
    config_files=module(e,'scripts/config_files.lua'),images=module(e,'scripts/image_catalogue.lua'),
    image_metadata=module(e,'scripts/image_metadata.lua'),environment=module(e,'scripts/environment.lua'),
    config_lines=module(e,'scripts/config_lines.lua'),resource_names=module(e,'scripts/resource_names.lua'),
    vm_module=module(e,'runtime/majiro_vm.lua'),build_id=module(e,'config/build.lua').build_id,
    source_identity=module(e,'config/build.lua').source_identity,
    save_identity=module(e,'config/build.lua').save_identity or module(e,'config/build.lua').source_identity,
    save_compatible_identities=module(e,'config/build.lua').save_compatible_identities,
    movies=module(e,'config/movies.lua'),
    audio_aliases=module(e,'config/resource_aliases.lua'),frame=0}
  local c=context
  _G.ruri_movie_finished=function(engine)
    c.active_movie=nil
  end
  c.resource_key=module(e,'runtime/majiro_strings.lua').resource_key
  c.images_lower={};for name,value in pairs(c.images)do c.images_lower[c.resource_key(name)]=value end
  c.script_names={};for name,value in pairs(c.catalogue.scripts)do c.script_names[name:lower()]=value end
  c.localization=module(e,'runtime/majiro_localization.lua').new(module(e,'scripts/translation.lua'),
    function(text)return e:convertEncoding{from='utf8',to='sjis',source=text}end)
  c.audio=module(e,'runtime/artemis_audio.lua').install(e)
  c.audio_assets={}
  if e:isFileExists('scripts/audio_catalogue.lua')then
    for name,value in pairs(module(e,'scripts/audio_catalogue.lua'))do c.audio_assets[name:lower()]=value end
  end
  c.probe=tonumber(e:var('t.args.probe'))==1
  c.probe_scenario=e:var('t.args.scenario')
  if c.probe_scenario=='chapter_return'then
    local chapter=e:var('t.args.chapter')
    assert(type(chapter)=='string'and chapter:match('^sub_title[%w_]*%.mjo$'),
      'invalid chapter probe target')
    c.save_probe_plan={{script='buttonmenu.mjo',id=1},{exec_chapter=chapter},
      {right_chapter=chapter},{script='menu.mjo',id=4}}
    if e:var('t.args.via')=='menu'then
      local extra={{right_chapter=chapter},{script='menu.mjo',id=5},
        {script='message_box.mjo',id=0},{right_chapter=chapter},
        {script='menu.mjo',id=5},{script='message_box.mjo',id=1}}
      for _,step in ipairs(extra)do c.save_probe_plan[#c.save_probe_plan+1]=step end
    else c.save_probe_plan[#c.save_probe_plan+1]={script='buttonmenu.mjo',id=99}end
    c.save_probe_plan[#c.save_probe_plan+1]={done=true,ui=true,script='buttonmenu.mjo',
      frame_script='start.mjo',settle=6000}
  elseif c.probe_scenario=='save_flow'then
    c.save_probe_plan={{script='buttonmenu.mjo',id=1},{reading=true,button=2},
      {script='menu.mjo',id=0},{script='loadsave.mjo',id=0},
      {input=true},{script='loadsave.mjo',id=1003},{done=true}}
  elseif c.probe_scenario=='load_flow'then
    c.save_probe_plan={{script='buttonmenu.mjo',id=2},{script='loadsave.mjo',id=0},
      {script='message_box.mjo',id=1},{done=true}}
  elseif c.probe_scenario=='story_page'then
    local target=math.max(0,math.min(200,tonumber(e:var('t.args.story_page'))or 0))
    c.save_probe_plan={{script='buttonmenu.mjo',id=1}}
    for i=1,target do c.save_probe_plan[#c.save_probe_plan+1]={reading=true,button=1}end
    c.save_probe_plan[#c.save_probe_plan+1]={done=true}
  elseif c.probe_scenario=='title_confirmation'then
    c.save_probe_plan={{script='buttonmenu.mjo',id=4},
      {done=true,ui=true,script='message_box.mjo',settle=600}}
  elseif c.probe_scenario=='load_header'then
    c.save_probe_plan={{script='buttonmenu.mjo',id=2},{script='loadsave.mjo',id=601},
      {done=true,ui=true,script='loadsave.mjo',settle=1000}}
  elseif c.probe_scenario=='story_config'or c.probe_scenario=='story_history'then
    c.save_probe_plan={{script='buttonmenu.mjo',id=1},{reading=true,button=2},
      {script='menu.mjo',id=c.probe_scenario=='story_config'and 3 or 2},
      {done=true,ui=true,script=c.probe_scenario=='story_config'and'config.mjo'or'history.mjo',settle=1000}}
  elseif c.probe_scenario=='extras_music'or c.probe_scenario=='extras_cg'or c.probe_scenario=='extras_replay'then
    local music=c.probe_scenario=='extras_music'
    local replay=c.probe_scenario=='extras_replay'
    c.save_probe_plan={{script='buttonmenu.mjo',id=5},
      {script='buttonmenu.mjo',id=music and 3 or(replay and 2 or 1)},
      {done=true,ui=true,script=music and'buttonmenu.mjo'or'cgmode.mjo',
        frame_script=music and'music.mjo'or'cgmode.mjo',settle=1000}}
  end
  c.save_probe_phase=1
  local duration=tonumber(e:var('t.args.profile'))
  if duration and duration>0 then
    local scene=e:var('t.args.profile_scene')
    c.profile={duration=math.min(duration,30000),scene=scene,samples={},captures=0,costs={}}
    if scene=='chapter_return'then
      c.probe=true
    elseif scene=='menu_pause'then
      local slot=tonumber(e:var('t.args.profile_slot'))
      if not slot or slot<=0 then slot=nil end
      c.probe=true
      c.save_probe_plan=slot and{{load=slot},{done=true}}or
        {{script='buttonmenu.mjo',id=1},{done=true}}
      c.save_probe_phase=1
    elseif scene=='title' or scene=='start' or scene=='coast'or scene=='fast'then
      c.probe=true;c.probe_scenario=(scene=='coast'or scene=='fast')and'start'or'title'
    elseif scene=='menu_config'or scene=='menu_save'or scene=='menu_load'then
      c.probe=true
      c.save_probe_plan={{script='buttonmenu.mjo',id=1},{reading=true,button=2},
        {done=true,ui=true,script='menu.mjo',settle=1000}}
      c.save_probe_phase=1
    else c.profile.began=e:now()end
  end
  -- The original startup owns its first visible scene. Showing the title
  -- artwork underneath an unfinished first frame causes an early flash.
  e:tag{'lyc',id='100',width=1280,height=720,color='FF000000'}
  e:tag{'lyprop',id='100',left=0,top=0,visible=1,anchorx=0,anchory=0}
  start_vm(e);e:setEventHandler{onEnterFrame='ruri_frame'};e:tag{'flip'}
end
local function event(e)
  if context.probe then
    local value=context.synthetic or {};context.synthetic=nil;return value
  end
  local p=context.profile
  if p and p.began then
    local elapsed=e:now()-p.began
    if p.scene=='menu_pause'then
      local button=0
      if elapsed>=450 and not p.opened then
        p.initial_menu=context.system.state.ui_waiting or false
        p.opened=true;button=2
      elseif elapsed>=1450 and not p.closed then p.closed=true;button=2 end
      return {x=1000,y=650,buttons=button,held_buttons=0}
    elseif p.scene=='menu_config'or p.scene=='menu_save'or p.scene=='menu_load'then
      local id=p.scene=='menu_config'and 3 or(p.scene=='menu_save'and 0 or 1)
      if not p.menu_target then
        for _,area in ipairs(context.system.state.ui_areas or{})do
          if area.script=='menu.mjo'and area.args[1]==id then
            local a=area.args;p.menu_target={x=a[2]+math.floor(a[4]/2),y=a[3]+math.floor(a[5]/2)}
          end
        end
      end
      local target=p.menu_target or{x=1000,y=650}
      local click=elapsed>=450 and not p.clicked
      if click then p.clicked=true end
      return {x=target.x,y=target.y,buttons=click and 1 or 0,held_buttons=0}
    elseif p.scene=='fast'then
      return {x=1000,y=650,held_buttons=0,down={[17]=elapsed>=250 and elapsed<2000}}
    elseif p.scene=='start' then
      if elapsed>250 and not p.clicked then p.clicked=true;return {x=172,y=242,buttons=1}end
      return {x=172,y=242,held_buttons=0}
    elseif p.scene=='coast' then
      if elapsed>600 and not p.coast_page then
        p.coast_page=1;context.system.state.reading_wait=false
        return {x=1000,y=650,buttons=1,held_buttons=0}
      elseif elapsed>1500 and p.coast_page==1 and context.system.state.reading_wait then
        p.coast_page=2;context.system.state.reading_wait=false
        return {x=1000,y=650,buttons=1,held_buttons=0}
      end
      return {x=1000,y=650,held_buttons=0}
    elseif p.scene=='title' then
      local index=math.floor(elapsed/350)%4
      return {x=172,y=({242,279,319,356})[index+1],held_buttons=0}
    end
    return {}
  end
  local mouse=e:getMousePoint()
  local pressed,down={},{}
  for _,key in ipairs{1,2,13,17,27,32,37,38,39,40}do
    pressed[key]=e:isDownEdge(key);down[key]=e:isDown(key)
  end
  return {x=mouse.x,y=mouse.y,pressed=pressed,down=down,
    buttons=(pressed[1] and 1 or 0)+(pressed[2] and 2 or 0),
    held_buttons=(down[1] and 1 or 0)+(down[2] and 2 or 0)}
end
local function save_probe(c,status)
  local step=c.save_probe_plan[c.save_probe_phase]
  local state=c.system.state
  if status~='waiting'then return end
  if step.load and state.ui_waiting then
    c.system.providers['$do_load'](c.vm,{step.load})
    c.save_probe_phase=c.save_probe_phase+1
    return
  end
  if step.exec_chapter and state.reading_wait then
    c.system.providers['$exec'](c.vm,{step.exec_chapter})
    c.save_probe_phase=c.save_probe_phase+1;c.probe_poll=true
    return
  end
  if step.right_chapter and state.ui_waiting and
     c.vm.frames[1].script.name==step.right_chapter and not state.chapter_menu_active then
    c.clock=c.clock+500;c.synthetic={x=640,y=660,buttons=2}
    state.ui_waiting=false;c.save_probe_phase=c.save_probe_phase+1
    return
  end
  if step.done then
    local ready=state.reading_wait and not state.ui_waiting
    if step.ui then
      ready=state.ui_waiting and not state.reading_wait
      if ready and step.script then
        ready=state.active_ui_script==step.script
        for _,area in ipairs(state.ui_areas or{})do if area.script==step.script then ready=true;break end end
      end
      if ready and step.frame_script then
        ready=false
        for _,frame in ipairs(c.vm.frames)do if frame.script.name==step.frame_script then ready=true;break end end
      end
    end
    if ready then
      step.until_ms=step.until_ms or c.clock+(step.settle or 0)
      if c.clock>=step.until_ms then c.probe_complete=true end
    end
  elseif step.reading then
    if step.sent then
      if state.reading_wait and state.reading_generation~=step.generation then
        c.save_probe_phase=c.save_probe_phase+1
      end
    elseif state.reading_wait and c.system.saves.stats().checkpoint then
      c.clock=c.clock+500
      c.synthetic={x=1000,y=650,buttons=step.settled and step.button or 0}
      state.reading_wait=false
      if step.settled then
        if c.probe_scenario=='story_page'then step.sent=true
        else c.save_probe_phase=c.save_probe_phase+1 end
      else step.settled=true;step.generation=state.reading_generation end
    end
  elseif step.input then
    local waiting=c.vm.threads[1].waiting
    if waiting and waiting.kind=='input'then
      c.clock=c.clock+500;c.synthetic={x=1000,y=650,buttons=1}
      state.ui_waiting=false;c.save_probe_phase=c.save_probe_phase+1
    end
  elseif state.ui_waiting and not state.reading_wait then
    for _,area in ipairs(state.ui_areas or{})do
      if area.script==step.script and area.args[1]==step.id then
        local a=area.args
        c.clock=c.clock+500
        c.synthetic={x=a[2]+math.floor(a[4]/2),y=a[3]+math.floor(a[5]/2),
          buttons=step.hovered and 1 or 0}
        state.ui_waiting=false
        if step.hovered then c.save_probe_phase=c.save_probe_phase+1 else step.hovered=true end
        break
      end
    end
  end
  if not c.synthetic and not c.probe_complete then
    local deadline=c.vm:next_deadline()
    if deadline then c.clock=math.max(c.clock+10,deadline);c.probe_poll=true end
  end
end
function M.frame(e)
  local c=context;c.frame=c.frame+1
  local profiled=c.profile and c.profile.began
  local frame_began=profiled and e:now()or 0
  if not c.probe then c.clock=e:now()-c.began end
  if e:isDownEdge(27) and c.vm.status=='blocked' then e:tag{'exit'};return end
  if e:isDownEdge(13) and c.vm.status=='blocked' then start_vm(e)end
  if c.vm.status~='blocked' and c.vm.status~='finished' and not c.probe_complete then
    local ok,why=pcall(function()
      -- Probes advance virtual time without encoding intermediate PNGs.
      -- Ordinary play continues to use one real-time tick per native frame.
      for tick=1,(c.probe and 12 or 1)do
        if c.probe_complete then break end
        if not c.probe or c.synthetic or c.probe_poll then
          c.system.poll(c.vm,event(e));c.probe_poll=false
        end
        local status=c.vm:run(10000,not c.probe and e:now()+8 or nil,function()return e:now()end)
        if c.probe then
        if c.save_probe_plan then save_probe(c,status)
        elseif status=='waiting' and (c.system.state.ui_waiting or c.system.state.reading_wait) then
          if c.probe_scenario=='start' or c.probe_scenario=='config' then
            local y=c.probe_scenario=='start' and 242 or 319
            if not c.navigation_phase then
              c.navigation_trace[#c.navigation_trace+1]={phase='title',steps=c.vm.steps,clock=c.clock}
              c.navigation_phase='hover';c.synthetic={x=172,y=y}
              c.system.state.ui_waiting=false
            elseif c.navigation_phase=='hover' then
              c.navigation_trace[#c.navigation_trace+1]={phase='hover',steps=c.vm.steps,clock=c.clock}
              c.navigation_phase='clicked';c.synthetic={x=172,y=y,buttons=1}
              c.system.state.ui_waiting=false
            else c.probe_complete=true end
          else c.probe_complete=true end
        elseif status=='waiting' then
          local deadline=c.vm:next_deadline()
          if deadline then c.clock=math.max(c.clock+10,deadline);c.probe_poll=true end
        end
        end
      end
    end)
    if not ok and c.vm.status~='blocked' then error(why)end
  end
  if c.system.state.shutdown_requested then
    if c.probe then write_result()end
    e:tag{'exit'};return
  end
  -- Loading can replace the VM during this frame. Discard any old async
  -- texture before draw() gets a chance to commit it over the loaded scene.
  if c.system.state.load_generation~=c.load_generation then
    c.load_generation=c.system.state.load_generation
    if c.load_generation then
      if c.presenter.state.pending then
        if c.presenter.state.pending.cached then e:unbindSurface(c.presenter.state.pending.path)end
        os.remove(c.presenter.state.pending.path)
      end
      c.presenter.state.pending=nil;c.presenter.state.signature=nil;c.presenter.state.last_time=-100000
      c.presenter.state.text_signature=nil
    end
  end
  if c.vm.status=='blocked' then
    if not c.settled then
      local p=c.vm.problem
      c.presenter.draw(c.system,now(),true)
      diagnostic(e,p.script..' @ '..string.format('%08X',p.offset)..'\n'..p.reason)
      write_result();c.settled=c.frame
    end
  else
    if c.probe_complete and c.profile and not c.profile.began then
      -- Only the setup is virtual. Samples below use real wall time and the
      -- normal presenter, including the START transition when requested.
      c.profile.began=e:now();c.began=e:now()-c.clock
      c.profile.initial_menu=false
      for _,frame in ipairs(c.vm.frames)do
        if frame.script.name=='menu.mjo'then c.profile.initial_menu=true end
      end
      c.profile.first_frame=true
      c.probe=false;c.probe_complete=false;c.settled=nil
    end
    local vm_finished=profiled and e:now()or 0
    local changed
    local main_wait=c.vm.threads[1].waiting
    local building=c.vm.host_budget_exhausted and(not main_wait or main_wait.kind=='yield')
    if (not c.probe or c.probe_complete)and not building then
      changed=c.presenter.draw(c.system,now(),(c.probe_complete and not c.settled)or
        (c.profile and c.profile.first_frame))
      if c.profile then c.profile.first_frame=nil end
    end
    if c.probe_complete and not c.settled then write_result();c.settled=c.frame end
    if profiled then
      local finish=e:now()
      c.profile.samples[#c.profile.samples+1]={t=frame_began-c.profile.began,
        reading_generation=c.system.state.reading_generation or 0,
        control_down=not not c.system.state.input.down[17],
        vm_ms=vm_finished-frame_began,draw_ms=finish-vm_finished,changed=not not changed,
        native_ms=c.presenter.state.last_native_ms or 0,bind_ms=c.presenter.state.last_bind_ms or 0,
        invalidate=c.system.state.invalidate,steps=c.vm.steps,clock=c.clock,
        reading_wait=c.system.state.reading_wait or false}
      c.profile.samples[#c.profile.samples].page_visible_since=
        c.system.state.fast_page and c.system.state.fast_page.started
    end
  end
  e:tag{'flip'}
  if not c.probe and c.system.state.persist_dirty and
     e:now()-(c.last_profile_flush or 0)>2000 then
    c.system.saves.flush(c.vm);c.last_profile_flush=e:now()
  end
  if c.profile and c.profile.began then
    local p=c.profile;local elapsed=e:now()-p.began
    local menu=p.scene:match('^menu_')
    local thresholds=menu and{0,450,550,650,750,850,1000,1500,2500}or
      (p.scene=='coast'and{0,1000,1600,2500,4000,5000,6000,7000,8500,9500}or{0,500,1500,4000})
    if p.captures<#thresholds and elapsed>=thresholds[p.captures+1]then
      p.captures=p.captures+1
      e:tag{'takess'};e:tag{'savess',file='profile_'..p.captures..'.png'}
    end
    if elapsed>=p.duration then
      local out=assert(io.open('native_profile.lua','wb'))
      out:write('return '..serialize{build_id=c.build_id,scene=p.scene,samples=p.samples,
        costs=p.costs,
        initial_menu=p.initial_menu,
        elapsed_ms=elapsed,problem=c.vm.problem,presenter=c.presenter.state,
        reading_wait=c.system.state.reading_wait,steps=c.vm.steps,console=c.system.state.console,
        active_ui_script=c.system.state.active_ui_script,
        ui_waiting=c.system.state.ui_waiting,frames=c.vm:export_state().threads,
        media=c.system.state.media.channels,last_syscall=c.vm.last_syscall,
        trace=c.vm.trace,audio_transport=c.audio.state}..'\n')
      out:close();e:tag{'exit'};return
    end
  end
  if c.profile and not c.profile.began and e:now()-c.began>15000 then
    write_result();e:tag{'exit'};return
  end
  if c.probe and c.settled and c.frame==c.settled+4 then
    e:tag{'takess'};e:tag{'savess',file='native_screen.png'}
  elseif c.probe and c.settled and c.frame>=c.settled+6 then e:tag{'exit'}end
end
return M
