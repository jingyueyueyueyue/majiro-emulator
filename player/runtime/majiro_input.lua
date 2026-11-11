-- Edge ownership, cursor coordinates, and source-defined click suppression.
local M={}
function M.install(define,state,options,screen)
  local input={x=100000,y=100000,pressed={},down={},buttons=0,debounce_mask=0,
    debounce_remaining=0,last_click=-100000}
  state.input=input
  define(0xf65aa4c0,function(vm,args)assert(#args==0);return input.activity_count or 0 end)
  local physical_x,physical_y=input.x,input.y
  local function script_coordinate(value)
    -- SDL3 supplies subpixel floats. Original Majiro mouse callbacks receive
    -- Win32 integer client coordinates, including identity viewport builds.
    if value~=value or value>=2147483648 or value< -2147483648 then return -2147483648 end
    return value<0 and math.ceil(value)or math.floor(value)
  end
  local function project()
    if input.cursor_override then
      if screen and input.cursor_override.client then
        input.x,input.y=screen.point(input.cursor_override.x,input.cursor_override.y)
      else input.x,input.y=input.cursor_override.x,input.cursor_override.y end
    elseif screen then input.x,input.y=screen.point(physical_x,physical_y)
    else input.x,input.y=physical_x,physical_y end
    input.x,input.y=script_coordinate(input.x),script_coordinate(input.y)
  end
  if screen then screen.changed=project end
  local function areas(vm)
    local t=vm.threads[vm.thread_id]
    t.areas=t.areas or {}
    return t
  end
  define('$areaevent_reset',function(vm,args)
    assert(#args==0)
    local t=areas(vm);t.areas={};t.area_defaults={};t.area_focus=nil;return 0
  end)
  define('$areaevent_offs',function(vm,args)
    assert(#args==2 and type(args[1])=='number'and type(args[2])=='number')
    local t=areas(vm);t.area_offset_x,t.area_offset_y=args[1],args[2];return 0
  end)
  define('$areaevent_set',function(vm,args)
    assert(#args==8 or #args==9,'areaevent_set signature')
    local t=areas(vm)
    t.areas[#t.areas+1]={args=args,script=vm:frame().script.name,inside=false}
    return 0
  end)
  define('$areaevent_setdefault',function(vm,args)
    assert(#args>=2 and #args<=4)
    areas(vm).area_defaults=args;return 0
  end)
  define('$areaevent_check',function(vm,args,wants)
    assert(#args==0,'areaevent_check signature')
    project()
    local t=areas(vm)
    if state.settings.mouse==false then
      state.ui_waiting=false;state.ui_areas=t.areas
      return 0
    end
    local callbacks={}
    local chapter_menu
    local reading_before=state.reading_wait
    local active
    local area_x=input.x-(t.area_offset_x or 0)
    local area_y=input.y-(t.area_offset_y or 0)
    for i,area in ipairs(t.areas)do
      local a=area.args
      if area_x>=a[2] and area_y>=a[3] and area_x<a[2]+a[4] and area_y<a[3]+a[5] then
        active=i;break
      end
    end
    -- Menus can reset or replace their rectangles between input polls.
    -- A remembered focus only belongs to an existing area in this thread.
    local count,focus=#t.areas,t.area_focus
    if type(focus)~='number'or focus%1~=0 or focus<1 or focus>count then focus=nil end
    if count>0 then
      if input.pressed[38] or input.pressed[37] then
        active=((focus or 1)-2)%count+1
      elseif input.pressed[40] or input.pressed[39] then
        active=(focus or 0)%count+1
      elseif input.pressed[13] then active=focus or 1 end
    end
    t.area_focus=active or focus
    local function queue(area,slot)
      local hash=area.args[slot]
      if not hash or hash==0 then return end
      local parameters=vm:argument_slice(area.args,1,5)
      callbacks[#callbacks+1]={hash=hash%4294967296,script=area.script,args=parameters}
    end
    -- The fourth default is the source's keyboard/page navigation callback,
    -- not another mouse button. Original 0x477210/0x477311 delivers its
    -- combined key flags before mouse defaults or hover transitions.
    local keyboard_flags=0
    local keyboard_hash=t.area_defaults and t.area_defaults[4]
    if keyboard_hash and keyboard_hash~=0 then
      for _,entry in ipairs{{33,1},{34,2},{132,8},{133,16},{147,4}}do
        if input.pressed[entry[1]]then
          keyboard_flags=keyboard_flags+entry[2]
          input.pressed[entry[1]]=nil
        end
      end
    end
    local keyboard_dispatch=keyboard_flags~=0
    if keyboard_dispatch then
      callbacks[#callbacks+1]={hash=keyboard_hash%4294967296,
        script=vm:frame().script.name,args={input.x,input.y,keyboard_flags}}
      state.reading_wait=false
    end
    -- Original areaevent_check (e.g. 0x477470 -> 0x4774dd) dispatches
    -- the previous area's leave callback and returns before entering another
    -- area. A single list-order pass can enter a lower-index item first and
    -- let the old item's cleanup erase its new preview or highlight.
    local leaving=false
    for i,area in ipairs(t.areas)do
      if not keyboard_dispatch and area.inside and i~=active then
        queue(area,7);area.inside=false;leaving=true;break
      end
    end
    if not keyboard_dispatch and not leaving and active then
      local area=t.areas[active]
      if not area.inside then queue(area,6);area.inside=true end
    end
    if not keyboard_dispatch and not leaving and active and (input.buttons%2==1 or input.pressed[13] or input.pressed[32]) then
      queue(t.areas[active],8)
      if input.buttons%2==1 then input.buttons=input.buttons-1 end
      input.pressed[13],input.pressed[32]=nil,nil
    elseif not keyboard_dispatch and not leaving and t.area_defaults then
      local slot
      if input.buttons%2==1 or input.pressed[13] or input.pressed[32] then slot=1
      elseif math.floor(input.buttons/2)%2==1 then slot=2 end
      -- A PAUSE may already be waiting in its default click handler when
      -- skip is enabled. Re-enter the source's advance callback promptly;
      -- leave physical buttons and mode-reset rules untouched.
      if not slot and vm.thread_id==1 and state.reading_wait and
         options.fast_mode and options.fast_mode(vm)then slot=1 end
      local hash=slot and t.area_defaults[slot]
      local root=vm.frames[1]
      if slot==2 and not state.chapter_menu_active and root and
         root.script.name:match('^sub_title')and vm:frame().script.name=='buttonmenu.mjo'then
        -- Ruri's chapter right callback selects title directly. Its pause
        -- wrapper can keep that return behind explicit menu controls, but
        -- other games also use sub_title/buttonmenu with different functions.
        local script=vm.scripts['pausemenu.mjo']
        if not script and options.resource_names and options.resource_names['pausemenu.mjo']then
          script=vm:load('pausemenu.mjo')
        end
        for _,fn in ipairs(script and script.functions or{})do
          if fn[1]==3638675204 then
            chapter_menu={script=script,offset=fn[2]};break
          end
        end
        if chapter_menu then
          hash=nil
          input.buttons=0;input.pressed[13],input.pressed[32]=nil,nil
        end
      end
      if hash and hash~=0 then
        callbacks[#callbacks+1]={hash=hash%4294967296,
          script=vm:frame().script.name,args={input.x,input.y}}
        state.reading_wait=false
        input.buttons=0;input.pressed[13],input.pressed[32]=nil,nil
      end
    end
    -- A page with no voice replay rectangles still accepts its default
    -- history navigation/return handlers. It is an interactive menu even
    -- when there are no individually bounded hit areas.
    state.ui_waiting=#callbacks==0 and(#t.areas>0 or t.area_defaults~=nil)and not state.reading_wait
    state.ui_areas=t.areas
    state.active_ui_script=vm:frame().script.name
    local calls={}
    for _,callback in ipairs(callbacks)do
      local script=assert(vm.scripts[callback.script]);local offset
      for _,fn in ipairs(script.functions)do if fn[1]==callback.hash then offset=fn[2]end end
      if not offset then vm:fail('area callback not present')end
      calls[#calls+1]={script=script.name,offset=offset,args=callback.args}
    end
    local done={kind='host',name='input_return',
      reading=reading_before,done={kind='push',wants=wants}}
    if chapter_menu then
      local fn=chapter_menu
      done={kind='host',name='chapter_menu_return',thread=vm.thread_id,
        areas=t.areas,defaults=t.area_defaults,focus=t.area_focus,
        save=state.settings.save,save_menu=state.settings.save_menu,
        first_sprite=state.next_sprite,script=state.active_ui_script,done=done}
      t.areas={};t.area_defaults={};t.area_focus=nil
      state.settings.save=false;state.settings.save_menu=false
      state.chapter_menu_active=true;state.ui_waiting=false
      calls[#calls+1]={script=fn.script.name,offset=fn.offset,args={}}
    end
    vm:resume{kind='sequence',calls=calls,done=done}
    return nil,true
  end)
  local function read()
    local value=input.buttons
    input.buttons=0
    return value
  end
  define(0xb7e3e81c,function(vm,args)
    -- Original 0x42FF30 -> 0x44C810 sets a button mask and count 3.
    assert(#args<=1)
    input.debounce_mask=args[1] or 1;input.debounce_remaining=3
    return 0
  end)
  define('$mk_read',function(vm,args)assert(#args==0);return read()end)
  define('$mk_stat',function(vm,args)assert(#args==0);return input.held_buttons or 0 end)
  define('$mk_wait',function(vm,args,wants)
    assert(#args==0)
    if input.buttons~=0 then return read() end
    vm.waiting={kind='input',wants_result=wants};return nil,true
  end)
  define('$mk_unwait',function(vm,args)assert(#args==0);return read()end)
  define('$kb_read',function(vm,args)
    assert(#args==1);local value=input.pressed[args[1]]
    input.pressed[args[1]]=nil;return value and 1 or 0
  end)
  define('$curpos_x',function(vm,args)assert(#args==0);project();return input.x end)
  define('$curpos_y',function(vm,args)assert(#args==0);project();return input.y end)
  define(0xb84c9953,function(vm,args)
    -- Original inline dispatcher -> 0x44CBA0 sets the sampled logical
    -- pointer, without moving the OS pointer. Used to leave menu hover.
    assert(#args==2)
    if args[1]==-10000 and(options.screen_controls or{}).cursor_reset_sentinel~=false then
      input.cursor_override=nil
      project()
    else
      local x,y=args[1],args[2]
      if screen then x,y=screen.client(x,y)end
      input.cursor_override={x=x,y=y,client=screen~=nil}
      project()
    end
    return 0
  end)
  define('$show_mouse_cursor',function(vm,args)
    assert(#args<=1);input.cursor_visible=args[1]~=0
    if options.cursor then options.cursor(input.cursor_visible) end
    return 0
  end)
  local function sample(event)
    if state.platform_sample then state.platform_sample(event)end
    local next_x,next_y=event.x or physical_x,event.y or physical_y
    local moved=next_x~=physical_x or next_y~=physical_y
    local activity=event.input_activity or 0
    if event.input_activity==nil then
      for _,pressed in pairs(event.pressed or{})do if pressed then activity=activity+1 end end
      if(event.buttons or 0)~=0 then activity=activity+1 end
    end
    input.activity_count=((input.activity_count or 0)+activity)%4294967296
    physical_x,physical_y=next_x,next_y
    if state.settings.mouse==false then
      input.physical_x,input.physical_y=physical_x,physical_y
      input.buttons=0;input.held_buttons=0;input.pressed={};input.down={}
      return
    end
    if moved then input.cursor_override=nil end
    input.physical_x,input.physical_y=physical_x,physical_y
    project()
    if not state.settings.keyboard_disabled then
      for key,pressed in pairs(event.pressed or {})do if pressed then input.pressed[key]=true end end
      input.down=event.down or input.down
    end
    local buttons=event.buttons or 0
    if input.debounce_mask~=0 and buttons%2==1 and input.debounce_mask%2==1 then
      if options.now()-input.last_click<350 and input.debounce_remaining>0 then
        buttons=buttons-1;input.debounce_remaining=input.debounce_remaining-1
      else input.debounce_mask=0 end
    end
    if buttons~=0 then
      input.last_click=options.now()
      -- Original 0x44C896/0x44C8AC: left clears skip and preserves
      -- AUTO (command 8); right clears AUTO as well (command 0).
      -- Ultra-fast mode suppresses these automatic mode changes.
      local settings=state.settings
      if not settings.ultra_fast_mode and buttons%4~=0 then
        settings.fast_mode=false;settings.fast_read_mode=false
        if math.floor(buttons/2)%2==1 then settings.auto_mode=false end
      end
    end
    if buttons~=0 then input.buttons=buttons end
    input.held_buttons=event.held_buttons or input.held_buttons or 0
    if state.capture_sample then state.capture_sample(event)end
  end
  return {sample=sample,read=read}
end
return M
