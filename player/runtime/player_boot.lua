-- Shared Majiro VM inside the Android SDL owner thread.
-- Every host callback performs the requested operation or raises an error.
local function module(path)
  return assert(loadstring(HOST.asset_text(path),path))()
end
local function file_module(path)
  local text=HOST.game_text(path)
  return text and assert(loadstring(text,path))()
end
local mode=HOST.text_mode and HOST.text_mode()or'original'
local patch_config={raw_metadata='',text_adapter={kind='',path=''}}
if mode=='local'then
  patch_config=module('runtime/majiro_json.lua').decode(assert(HOST.patch_configuration(),'patch manifest configuration missing'))
end
local raw_patch
local patch_metadata=mode=='local'and patch_config.raw_metadata~=''and file_module(patch_config.raw_metadata)
assert(patch_metadata or patch_config.raw_metadata==''or patch_config.legacy,
  'declared raw patch metadata is missing')
if patch_metadata then
  raw_patch=module('runtime/majiro_patch.lua').new(patch_metadata,HOST.decode_bytes)
  PLAYER_PATCH_METADATA=patch_metadata
  PLAYER_DECODE_PATCH=raw_patch.decode
else
  PLAYER_PATCH_METADATA=nil;PLAYER_DECODE_PATCH=nil
end
local configuration=module('runtime/player_configuration.lua').make()
local opcodes=module('scripts/opcodes.lua')
local names=module('scripts/syscall_names.lua')
local identity=configuration.identity
local text_patch
local text_adapter=patch_config.text_adapter or{}
local text_metadata=mode=='local'and text_adapter.path and text_adapter.path~=''and HOST.game_text(text_adapter.path)
assert(text_metadata or not text_adapter.path or text_adapter.path==''or patch_config.legacy,
  'declared text patch metadata is missing')
if text_metadata then
  assert(text_adapter.kind=='sequential_text_v1','unsupported data text patch format')
  local data=assert(loadstring(text_metadata,'verified text patch data'))()
  text_patch=module('runtime/majiro_text_patch.lua').new(data,HOST.cp932)
  identity=assert(HOST.storage('sha256\n'..identity..text_patch.identity):match('^OK ([0-9a-f]+)$'),
    'text patch identity unavailable')
end
local clock=0
local last_flush=0
local last_font_quality
local submitted_frames=0
local last_present_reply
local system
local vm
local online
local codec=module('runtime/majiro_codec.lua')
local store=module('runtime/majiro_store.lua').new(codec,HOST.storage,identity,{},{partition_foreign=true})
store.path=function(name)return 'save://'..name end
local persisted=store:load_index().profile or {}
if store.foreign_identity and HOST.debug_output then
  HOST.debug_output('Previous script-set save index preserved; using separate current index','',0)
end
local options={
  names=names,width=configuration.width,height=configuration.height,
  minmax_arguments=configuration.minmax_arguments,
  string_case=configuration.string_case,
  integer_parse=configuration.integer_parse,empty_search_absent=configuration.empty_search_absent,
  ultra_disable_returns_previous=configuration.ultra_disable_returns_previous,
  text_controls=configuration.text_controls,
  voice_controls=configuration.voice_controls,
  screen_controls=configuration.screen_controls,
  platform_controls=configuration.platform_controls,
  external_controls=configuration.external_controls,
  license=HOST.license,
  platform=HOST.platform,
  graphics_controls=configuration.graphics_controls,
  dialog_controls=configuration.dialog_controls,
  config_files=configuration.config_files,config_lines=configuration.config_lines,
  environment=configuration.environment,resource_names=configuration.resource_names,
  image_metadata=configuration.image_metadata,movies=configuration.movies,
  modules=module,now=function()return clock end,
  asset=HOST.image_asset,media_asset=HOST.media_asset,
  graphics=HOST.graphics,audio=HOST.audio,movie=HOST.movie,dialog=HOST.dialog,
  debug_output=HOST.debug_output,load_font=HOST.load_font,
  wallpaper=HOST.wallpaper,change_cursor=HOST.change_cursor,
  resize_window=HOST.resize_window,
  font_filter=HOST.font_filter,
  cp932=HOST.cp932,font_face='sans-serif',font_dialog=HOST.font_dialog,
  decode_bytes=HOST.decode_bytes,service=HOST.service,
  screen_mode=HOST.screen_mode,source_identity=identity,
  screen_fit=HOST.screen_fit,
  file_read=HOST.script_file_read,file_write=HOST.script_file_write,
  external_save=HOST.external_save,
  registry=persisted.registry,progress=persisted.progress,saved_settings=persisted.settings,
  preference_tokens=not persisted.persistent_globals and persisted.preference_tokens or nil,
  store=store,set_clock=function(value)clock=value;HOST.set_clock(value)end,
  wait_for_present=function()return true end,
}
if text_patch then
  options.cp932=text_patch.bytes
  options.font_face=text_patch.font_face
  options.environment.TITLEBAR=text_patch.window_title or options.environment.TITLEBAR
  options.graphics=function(request)return HOST.graphics(text_patch.graphics(request))end
  options.dialog=function(action,token,flags,message,caption)
    return HOST.dialog(action,token,flags,text_patch.dialog(message),text_patch.window_title or caption)
  end
end
options.archive=function(action,name)
  local result=HOST.archive_control(action,name)
  assert(result==0,'dynamic archive host operation failed')
  local names={}
  local metadata={}
  for _,member in ipairs(HOST.member_names())do
    local key=member:lower();names[key]=true
    if key:match('%.rct$')or key:match('%.rc8$')or key:match('%.png$')then
      metadata[key]=HOST.image_metadata(member)
    end
  end
  options.resource_names=names
  options.image_metadata=metadata
  options.source_generation=(options.source_generation or 0)+1
  return result
end
if raw_patch then
  assert(not text_patch,'conflicting raw translation patch adapters')
  local localization=module('runtime/majiro_localization.lua').new(raw_patch.display,HOST.cp932)
  options.cp932=localization.cp932
  options.display_text=localization.display
end
local translation_tools=module('runtime/majiro_translation.lua')
options.display_bytes=function(text)return translation_tools.display_bytes(text,options.cp932)end
local function update_online()
  local selected=HOST.text_mode and HOST.text_mode()or mode
  if online and selected~='online'then
    options.online_translation=nil;online=nil
    if system then system.state.text_translation_ready=nil end
  end
  if selected~='online'or online then return end
  online=translation_tools.new{
    host=HOST.translation,
    json=module('runtime/majiro_json.lua'),mode='online',game=identity,opcodes=opcodes,names=names,
    now=function()return clock end,wait_ms=15000,
    speaker_popup=tonumber(configuration.environment.NAMEPOPUP)==1,
    hash=function(value)return assert(HOST.storage('sha256\n'..value):match('^OK ([0-9a-f]+)$'))end,
  }
  options.online_translation=online
end
update_online()
system=module('runtime/majiro_syscalls.lua').new(options)
vm=module('runtime/majiro_vm.lua').new{
  opcodes=opcodes,syscall=system.invoke,loader=function(name)
    local script=HOST.script(name)
    if raw_patch then script=raw_patch.apply(script)end
    return text_patch and text_patch.apply(script)or script
  end,
  numeric_profile=configuration.numeric_profile,
  before_instruction=function(owner,kind,row)
    return online and online.before_instruction(owner,kind,row)or false
  end,
  case_string_relations=configuration.case_string_relations,
  after_init=system.after_init,restart_on_return=true,
  persistent_globals=persisted.persistent_globals,persistent_changed=system.persistent_changed,
  script_loaded=system.script_loaded,resume_action=system.resume_action,
  wall_deadline=system.wall_deadline,string_ops=system.strings,text_ops=system.text,
  line_changed=system.line_changed,
}
vm:begin('start.mjo')
function PLAYER_SIZE()return configuration.width,configuration.height end
function PLAYER_TICK(now,event)
  clock=now
  update_online()
  system.poll(vm,event)
  local requested=system.state.settings.font_quality
  if requested~=nil then
    local quality=requested==2 and(system.providers['$is_fast_mode'](vm,{})~=0 and 0 or 1)or requested
    if quality~=last_font_quality then HOST.service('font_quality',tostring(quality));last_font_quality=quality end
  end
  if not system.dialogs.active()then vm:run(30000)end
  local state=system.state
  if online then pcall(online.poll,vm,state)end
  -- Source initialization can reach an input menu while redraw is suspended.
  -- A fresh host has no previous frame to preserve in that case. Publish the
  -- first input-ready scene without changing the source's nested draw counter.
  local bootstrap_frame=submitted_frames==0 and state.ui_waiting
  if state.frontbuffer and(state.invalidate~=false or bootstrap_frame)then
    system.sprites.update()
    local rows={}
    for _,id in ipairs(state.sprite_order)do
      local sprite=state.sprites[id]
      if sprite and sprite.destination==state.frontbuffer and sprite.anti<255
          and (sprite.hidden or 0)==0 then
        rows[#rows+1]=string.format('%d %d %d %d %d %.8g %.8g %.8g %.8g %.8g %d %d',
          sprite.page,sprite.sx,sprite.sy,sprite.width,sprite.height,sprite.x,sprite.y,
          sprite.xscale,sprite.yscale,sprite.rotate,sprite.anti,sprite.extended and 1 or 0)
      end
    end
    last_present_reply=HOST.graphics(system.screen.present(state.frontbuffer,'screen',table.concat(rows,'\n')))
    assert(type(last_present_reply)=='string'and last_present_reply:sub(1,2)=='OK',
      'frame composition failed: '..tostring(last_present_reply))
    submitted_frames=submitted_frames+1
    system.presented(state.present_requested)
    state.dirty=false
  end
  if state.persist_dirty and now-last_flush>=2000 then
    system.saves.flush(vm);last_flush=now
  end
  return vm.status,vm.steps,state.reading_generation or 0,
    vm:frame()and vm:frame().script.name or ''
end
function PLAYER_WINDOW_MENU_RESULT()return system.state.window_menu_result or''end
function PLAYER_SHUTDOWN()
  if system.state.host_shutdown_done then return end
  system.state.host_shutdown_done=true
  local failures={}
  local function finish(action)
    local ok,why=pcall(action)
    if not ok then failures[#failures+1]=tostring(why)end
  end
  if not system.state.shutdown_requested then
    system.state.persist_dirty=true
    finish(function()system.saves.flush(vm)end)
  end
  for _,spec in pairs(system.state.media.channels)do
    finish(function()HOST.audio('stop',spec)end)
  end
  system.state.media.channels={}
  finish(function()HOST.movie('stop',{})end)
  if HOST.translation then finish(function()HOST.translation('clear','')end)end
  options.online_translation=nil;online=nil
  system.state.shutdown_requested=true;vm.status='finished'
  if #failures>0 then error(table.concat(failures,'; '),0)end
end
function PLAYER_DEBUG()
  return codec.encode{status=vm.status,steps=vm.steps,problem=vm.problem,
    last_syscall=vm.last_syscall,trace=vm.trace,opcode_counts=vm.opcode_counts,
    syscall_counts=vm.syscall_counts,reading_generation=system.state.reading_generation}
end
function PLAYER_STATUS()
  local state=system.state
  local frames={}
  for i,frame in ipairs(vm.frames)do
    local row=frame.script.code[frame.index]
    frames[i]={script=frame.script.name,offset=row and row[1]or -1}
  end
  local areas={}
  for i,area in ipairs(state.ui_areas or{})do
    local a=area.args
    areas[i]={id=a[1],x=a[2],y=a[3],width=a[4],height=a[5],script=area.script}
  end
  local scripts={}
  for name in pairs(vm.scripts)do scripts[#scripts+1]=name end
  table.sort(scripts)
  return{status=vm.status,steps=vm.steps,reading_wait=state.reading_wait or false,
    ui_waiting=state.ui_waiting or false,reading_generation=state.reading_generation or 0,
    areas=areas,frames=frames,loaded_scripts=scripts,
    active_ui_script=state.active_ui_script,chapter_menu=state.chapter_menu_active or false,
    fast_mode=state.settings.fast_mode or false,auto_mode=state.settings.auto_mode or false,
    opcode_counts=vm.opcode_counts,syscall_counts=vm.syscall_counts,
    read_positions=state.progress.read,problem=vm.problem,
    translation=online and online.stats or nil,
    last_syscall=vm.last_syscall and{hash=vm.last_syscall.hash,opcode=vm.last_syscall.opcode}or nil,
    frontbuffer=state.frontbuffer,invalidate_depth=state.invalidate_depth,
    invalidation_enabled=state.invalidate,submitted_frames=submitted_frames,last_present_reply=last_present_reply}
end
