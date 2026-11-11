-- Shared Majiro VM inside the Android SDL owner thread.
-- Every host callback performs the requested operation or raises an error.
local function module(path)
  return assert(loadstring(HOST.asset_text(path),path))()
end
local configuration=module('runtime/player_configuration.lua').make()
local function file_module(path)
  local text=HOST.game_text(path)
  return text and assert(loadstring(text,path))()
end
local opcodes=module('scripts/opcodes.lua')
local names=module('scripts/syscall_names.lua')
local identity=configuration.identity
local clock=0
local last_flush=0
local last_font_quality
local system
local vm
local codec=module('runtime/majiro_codec.lua')
local store=module('runtime/majiro_store.lua').new(codec,HOST.storage,identity,{})
store.path=function(name)return 'save://'..name end
local persisted=store:load_index().profile or {}
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
options.archive=function(action,name)
  local result=HOST.archive_control(action,name)
  local names={}
  for _,member in ipairs(HOST.member_names())do names[member:lower()]=true end
  options.resource_names=names
  return result
end
if configuration.translation then
  local translation=file_module('player-data/scripts/translation.lua')
  assert(translation,'formal translation mapping missing')
  local localization=module('runtime/majiro_localization.lua').new(translation,HOST.cp932)
  options.cp932=localization.cp932
  options.display_text=localization.display
end
system=module('runtime/majiro_syscalls.lua').new(options)
vm=module('runtime/majiro_vm.lua').new{
  opcodes=opcodes,syscall=system.invoke,loader=HOST.script,
  numeric_profile=configuration.numeric_profile,
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
  system.poll(vm,event)
  local requested=system.state.settings.font_quality
  if requested~=nil then
    local quality=requested==2 and(system.providers['$is_fast_mode'](vm,{})~=0 and 0 or 1)or requested
    if quality~=last_font_quality then HOST.service('font_quality',tostring(quality));last_font_quality=quality end
  end
  if not system.dialogs.active()then vm:run(30000)end
  local state=system.state
  if state.frontbuffer and state.invalidate~=false then
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
    HOST.graphics(system.screen.present(state.frontbuffer,'screen',table.concat(rows,'\n')))
    system.presented(state.present_requested)
    state.dirty=false
  end
  if state.persist_dirty and now-last_flush>=2000 then
    system.saves.flush(vm);last_flush=now
  end
  return vm.status,vm.steps,state.reading_generation or 0,
    vm:frame()and vm:frame().script.name or ''
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
    last_syscall=vm.last_syscall and{hash=vm.last_syscall.hash,opcode=vm.last_syscall.opcode}or nil}
end
