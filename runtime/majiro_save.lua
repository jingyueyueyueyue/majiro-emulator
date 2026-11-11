-- Majiro save-slot metadata and UI access. Pixel resources remain shared.
local M={}
function M.install(define,state,options,providers,graphics)
  local codec=options.modules('runtime/majiro_codec.lua')
  local graphics_checkpoint=options.modules('runtime/majiro_graphics_checkpoint.lua')
  local store=options.store
  local index=store and store:load_index()or{schema=1,identity=options.source_identity,slots={}}
  state.save_catalog=index.slots
  local checkpoint
  local function key(slot)
    assert(type(slot)=='number'and slot%1==0,'save slot must be integral')
    if slot<0 or slot==10001 then return 'quick'end
    if slot>=10000 then
      local head=tonumber(state.registry.quicknum)or 0
      return string.format('history_%d',(head+10011-slot)%8)
    end
    return string.format('slot_%05d',slot)
  end
  local function record(args)
    assert(#args==1)
    return state.save_catalog[key(args[1])]
  end
  define('$get_save_time$',function(vm,args)
    local value=record(args);return value and value.time or ''
  end)
  define('$get_save_info$',function(vm,args)
    local value=record(args);return value and value.info or ''
  end)
  define('$get_current_info$',function(vm,args)
    assert(#args==0);return state.savepoint and state.savepoint.label or ''
  end)
  define('$copy_thumbnail',function(vm,args)
    assert(#args==4)
    local value=record{args[1]}
    if not value then return -1 end
    if not store or not value.thumbnail then return -1 end
    local id=state.next_page;state.next_page=id+1
    local path=store.path(value.thumbnail)
    graphics(vm,string.format('load %d 0 %s',id,path))
    state.pages[id]={width=value.thumbnail_width,height=value.thumbnail_height,depth=32}
    providers['$grp_copy'](vm,{id,0,0,value.thumbnail_width,value.thumbnail_height,args[2],args[3],args[4]})
    providers['$page_release'](vm,{id})
    return 0
  end)
  define(0x9143dc9e,function(vm,args)
    assert(#args==2 or #args==3)
    if state.current_thumbnail then
      providers['$page_release'](vm,{state.current_thumbnail});state.current_thumbnail=nil
    end
    if args[1]==0 and args[2]==0 then return 0 end
    assert(args[1]>0 and args[2]>0 and args[1]<=4096 and args[2]<=4096)
    local source=args[3]or state.frontbuffer
    if not state.pages[source]then vm:fail('thumbnail source page missing')end
    local page=state.pages[source]
    local frozen=providers['$page_create'](vm,{page.width,page.height})
    providers['$grp_copy'](vm,{source,0,0,page.width,page.height,frozen,0,0})
    local result=providers['$page_create'](vm,{args[1],args[2]})
    graphics(vm,string.format('thumbnail %d %d',frozen,result))
    providers['$page_release'](vm,{frozen})
    state.current_thumbnail=result;return 0
  end)
  define(0x89ce8c53,function(vm,args)
    assert(#args==3)
    local id=state.current_thumbnail
    if not id then return -1 end
    local page=state.pages[id]
    providers['$grp_copy'](vm,{id,0,0,page.width,page.height,args[1],args[2],args[3]})
    return 0
  end)
  local excluded={names=true,calls=true,graphics_journal=true,save_catalog=true,restoring=true,
    media_providers=true}
  local function snapshot(vm)
    state.graphics_journal=graphics_checkpoint.compact(state.graphics_journal,state.pages)
    local saved={}
    for field,value in pairs(state)do
      if not excluded[field]and type(value)~='function'then saved[field]=value end
    end
    local packet=codec.clone{schema=1,identity=options.source_identity,clock=options.now(),
      wall_clock=(options.wall_now or options.now)(),
      vm=vm:export_state(),state=saved}
    packet.graphics={}
    for i,request in ipairs(state.graphics_journal)do packet.graphics[i]=request end
    return packet
  end
  local function capture(vm,args,wants)
    assert(#args<=1)
    -- 0x45125C checks both source enable flags before capturing. UI scripts
    -- call save_point() while save-menu capture is disabled; that call must
    -- not replace the story checkpoint with an in-progress menu.
    if state.settings.save==false or state.settings.save_menu==false then
      if args[1]and state.savepoint then state.savepoint.label=args[1]end
      return 0
    end
    local frame=vm:frame();local row=frame.script.code[frame.index]
    state.savepoint={label=args[1]or(state.savepoint and state.savepoint.label)or'',
      script=frame.script.name,offset=row and row[1]or -1,thread=vm.thread_id,
      source_sha256=frame.script.source_sha256}
    if state.reading_wait then
      state.reading_context={thread=vm.thread_id,depth=#vm.frames}
    end
    if wants then vm:push(0)end
    checkpoint=snapshot(vm)
    return nil,true
  end
  define('$save_point',capture)
  local function command_batches(commands,consume)
    local pieces={'batch\n'};local length=6
    local function publish()
      if length>6 then consume(table.concat(pieces))end
      pieces={'batch\n'};length=6
    end
    for _,command in ipairs(commands)do
      local entry=#command..'\n'..command
      assert(#entry<60000,'graphics command exceeds save replay bound')
      if length+#entry>55000 then publish()end
      pieces[#pieces+1]=entry;length=length+#entry
    end
    publish()
  end
  local function graphics_objects(commands)
    local objects={}
    command_batches(commands,function(batch)objects[#objects+1]=store:put(codec.encode(batch))end)
    return objects
  end
  local function profile(vm)
    local tokens={}
    for at,binding in pairs(state.preference_bindings)do
      local value=vm.globals[0][binding.hash]
      if type(value)=='number'or type(value)=='string'then tokens[at]=value end
    end
    return {registry=codec.clone(state.registry),settings=codec.clone(state.settings),
      progress=codec.clone(state.progress),preference_tokens=tokens,
      persistent_globals=codec.clone(vm.globals[0])}
  end
  local function save(vm,args)
    assert(#args==1)
    if not checkpoint then vm:fail('no source save point is available')end
    if not store then vm:fail('persistent save storage is unavailable')end
    local slot=key(args[1])
    local data={schema=checkpoint.schema,identity=checkpoint.identity,clock=checkpoint.clock,
      wall_clock=checkpoint.wall_clock,
      vm=checkpoint.vm,state=checkpoint.state}
    local object=store:put(codec.encode(data))
    local graphic_objects=graphics_objects(checkpoint.graphics)
    local thumbnail='thumb_'..store:object_key(object)..'.png'
    local thumbnail_page=state.current_thumbnail
    if thumbnail_page and not store.exists(thumbnail)then
      graphics(vm,'png '..thumbnail_page..' '..store.path(thumbnail))
    end
    local page=thumbnail_page and state.pages[thumbnail_page]
    local old=index.slots[slot]
    local time=options.wall_time and options.wall_time()or os.date('%Y/%m/%d %H:%M:%S')
    index.slots[slot]={time=time,info=checkpoint.state.savepoint.label,snapshot=object,
      graphics=graphic_objects,thumbnail=page and thumbnail or nil,
      thumbnail_width=page and page.width,thumbnail_height=page and page.height}
    index.profile=profile(vm)
    store:commit(index);store:collect(old,index)
    state.persist_dirty=false;state.last_saved_slot=slot
    return 0
  end
  define('$do_save',save)
  local function install_state(saved)
    -- Keep tables captured by installed provider closures (console, settings,
    -- input, media) alive while restoring their data and shared references.
    local memo={[saved]=state}
    for _,name in ipairs{'console','settings','input','media'}do
      if saved[name]and state[name]then memo[saved[name]]=state[name]end
    end
    local filled={}
    local function fill(value)
      if type(value)~='table'then return value end
      local target=memo[value]
      if filled[value]then return target end
      if not target then target={};memo[value]=target end
      filled[value]=true
      for k,v in pairs(target)do
        if type(v)~='function'and not(target==state and excluded[k])then target[k]=nil end
      end
      for k,v in pairs(value)do target[fill(k)]=fill(v)end
      return target
    end
    return fill(saved),memo,fill
  end
  local function restore(vm,packet,commands,staged)
    commands=graphics_checkpoint.compact(commands,packet.state.pages)
    local saved_registry=state.registry;local saved_progress=state.progress
    local saved_persistent=vm.globals[0]
    local saved_preferences={}
    for _,binding in pairs(state.preference_bindings)do
      saved_preferences[binding.hash]=vm.globals[0][binding.hash]
    end
    local saved_settings={}
    for _,field in ipairs{'musicvolume','soundvolume','voicevolume','mojispeed','autospeed',
      'effectspeed','font_name','font_proportional','fullscreen'}do saved_settings[field]=state.settings[field]end
    local generation=state.media.generation
    for _,spec in pairs(state.media.channels)do options.audio('stop',spec)end
    local _,memo,fill=install_state(packet.state)
    state.time_control=state.time_control or{scale=1,anchor=0,offset=0}
    local restored_vm=fill(packet.vm)
    -- Reinstall state entries which are process-wide rather than per-slot.
    state.registry=saved_registry;state.save_catalog=index.slots
    for name,value in pairs(saved_settings)do state.settings[name]=value end
    for name,value in pairs(saved_progress.pictures or{})do state.progress.pictures[name]=value end
    for position,value in pairs(saved_progress.read or{})do state.progress.read[position]=value end
    state.progress.lines=state.progress.lines or{}
    for position,value in pairs(saved_progress.lines or{})do state.progress.lines[position]=value end
    state.progress.scripts=state.progress.scripts or{}
    for name,value in pairs(saved_progress.scripts or{})do state.progress.scripts[name]=value end
    state.graphics_journal={}
    state.restoring=true
    if staged then
      assert(options.graphics('checkpoint_commit')=='OK','external graphics commit failed')
    else
      options.graphics('reset_pages')
      command_batches(commands,function(batch)
        local result=options.graphics(batch);assert(result=='OK','graphics checkpoint replay failed')
      end)
    end
    state.graphics_journal=commands
    state.restoring=false
    vm:import_state(restored_vm)
    vm.globals[0]=saved_persistent
    for hash,value in pairs(saved_preferences)do vm.globals[0][hash]=value end
    assert(options.set_clock,'checkpoint clock restore is unavailable')
    options.set_clock(packet.wall_clock or packet.clock)
    state.present_completed=state.present_requested
    state.input.buttons=0;state.input.held_buttons=0;state.input.pressed={};state.input.down={}
    state.input.cursor_override=nil
    state.media.generation=math.max(generation,state.media.generation)
    for _,spec in pairs(state.media.channels)do
      state.media.generation=state.media.generation+1;spec.generation=state.media.generation
      local at=spec.paused_at or(packet.wall_clock or packet.clock)
      if spec.loop or at-spec.started<spec.duration_ms then
        if state.media.replay then state.media.replay(vm,spec)else options.audio('play',spec)end
      end
    end
    state.dirty=true;state.load_generation=(state.load_generation or 0)+1
    -- The saved frames replace the caller; a value must not enter their stack.
    checkpoint=snapshot(vm)
    return nil,true
  end
  define('$do_load',function(vm,args)
    local saved=record(args)
    if not saved then return -1 end
    if not store then vm:fail('persistent load storage is unavailable')end
    local packet=codec.decode(store:get(saved.snapshot))
    assert(packet.schema==1 and store:accepts_identity(packet.identity),'save source identity changed')
    local commands={}
    for _,object in ipairs(saved.graphics)do
      local data=store:get(object)
      local batch=data:sub(1,5)=='MJS1\n'and codec.decode(data)or data
      assert(batch:sub(1,6)=='batch\n','invalid graphics checkpoint')
      local offset=7
      while offset<=#batch do
        local ending=assert(batch:find('\n',offset,true))
        local length=assert(tonumber(batch:sub(offset,ending-1)))
        assert(length>0 and length%1==0 and ending+length<=#batch,'truncated graphics command')
        commands[#commands+1]=batch:sub(ending+1,ending+length)
        offset=ending+length+1
      end
    end
    return restore(vm,packet,commands)
  end)
  local function external(vm,action,data)
    if not options.external_save then vm:fail('external save document provider unavailable')end
    local result=options.external_save(action,data or'')
    assert(type(result)=='string','external save document result missing')
    if result=='CANCEL'then return nil end
    if action=='save'then assert(result=='OK','external save publication failed');return true end
    assert(result:sub(1,3)=='OK\n','external load read failed')
    return result:sub(4)
  end
  define('$do_extsave',function(vm,args)
    assert(#args==0,'external save signature')
    if not checkpoint then vm:fail('no source save point is available')end
    local packet=codec.clone(checkpoint)
    packet.graphics=nil
    local encoded=codec.encode{format='MajiroPlayerExternalSave',schema=1,
      identity=options.source_identity,packet=packet,graphics=checkpoint.graphics}
    return external(vm,'save',encoded)and 0 or -1
  end)
  define('$do_extload',function(vm,args)
    assert(#args==0,'external load signature')
    local encoded=external(vm,'load')
    if not encoded then return -1 end
    local exported=codec.decode(encoded)
    assert(exported.format=='MajiroPlayerExternalSave'and exported.schema==1,
      'external document is not a portable player save; native Windows import is separate')
    assert(exported.identity==options.source_identity and exported.packet.identity==options.source_identity,
      'external save script identity changed')
    local packet,commands=exported.packet,exported.graphics
    assert(packet.schema==1 and type(packet.state)=='table'and type(packet.state.pages)=='table'
      and type(packet.vm)=='table'and type(commands)=='table','invalid external checkpoint structure')
    assert(type(packet.clock)=='number'and type(packet.wall_clock or packet.clock)=='number',
      'external save clock missing')
    for _,command in ipairs(commands)do
      assert(type(command)=='string'and #command<60000,'invalid external graphics command')
      local op=command:match('^(%w+)')
      assert(op and op~='png'and op~='present'and op~='fontfile'and op~='font_dialog'
        and not op:match('^save_'),'external checkpoint contains a side effect')
      assert(not command:find('save://',1,true)and not command:find('..',1,true),
        'external checkpoint references an unsafe path')
    end
    -- Validate scripts, identity and instruction positions in an isolated VM.
    local staging=options.modules('runtime/majiro_vm.lua').new{
      loader=vm.loader,opcodes=vm.opcodes,syscall=vm.syscall,numeric_profile=vm.numeric_profile,
      string_ops=vm.string_ops,text_ops=vm.text_ops,case_string_relations=vm.case_string_relations}
    staging:import_state(codec.clone(packet.vm))
    local begun=options.graphics('checkpoint_begin')
    assert(begun=='OK','external load requires transactional graphics staging')
    local ok,problem=pcall(function()
      assert(options.graphics('reset_pages')=='OK','external graphics staging reset failed')
      command_batches(commands,function(batch)
        assert(options.graphics(batch)=='OK','external graphics staging failed')
      end)
    end)
    if not ok then options.graphics('checkpoint_rollback');error(problem,0)end
    -- Inputs have passed VM and complete graphics staging; restore cannot
    -- observe partially decoded script/position or a failed graphics command.
    return restore(vm,packet,commands,true)
  end)
  define('$set_save_info',function(vm,args)
    assert(#args==2 and type(args[2])=='string')
    local saved=index.slots[key(args[1])]
    if not saved then return -1 end
    saved.info=args[2];if store then store:commit(index)end;return 0
  end)
  define('$delete_save',function(vm,args)
    assert(#args==1 and store)
    local slot=key(args[1]);local old=index.slots[slot]
    index.slots[slot]=nil;store:commit(index);store:collect(old,index);return 0
  end)
  define('$copy_save',function(vm,args)
    assert(#args==2 and store)
    local source=index.slots[key(args[1])]
    if not source then return -1 end
    local target=key(args[2]);local old=index.slots[target]
    index.slots[target]=codec.clone(source)
    store:commit(index);store:collect(old,index);return 0
  end)
  return {key=key,capture=capture,
    flush=function(vm)
      if not store or not state.persist_dirty then return end
      index.profile=profile(vm);store:commit(index);state.persist_dirty=false
    end,
    stats=function()return {checkpoint=checkpoint~=nil,journal=#state.graphics_journal}end}
end
return M
