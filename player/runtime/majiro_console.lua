-- Message window state. History and live text have separate ownership.
local M={}
function M.install(define,state,options,emit)
  local c={visible=false,text='',last_message='',history_text='',name='',x=0,y=0,
    color=0xffffff,font='',history={},history_page={},history_depth=0,
    enabled_history=true,zero_time=false,text_speed=-1}
  state.console=c
  local function notify(vm,action)
    if options.console then options.console(action,c) end
  end
  define('$console_cls',function(vm,args,wants)
    assert(#args==0);c.text='';c.page={};c.name=''
    c.last_message='';c.history_text='';c.history_page={};c.page_message=''
    c.x,c.y=c.configuration and c.configuration[2]or 0,c.configuration and c.configuration[3]or 0
    if c.configuration and options.graphics then
      local a=c.configuration;local page=state.pages[a[1]]
      if page and page.antidata then
        assert(options.graphics(string.format('fill %d %d %d %d %d 255',
          page.antidata,a[2],a[3],a[4],a[5]))=='OK','console mask clear failed')
      end
    end
    notify(vm,'clear')
    emit(vm,'CONSOLE_CLS',{}, {kind='push',wants=wants})
    return nil,true
  end)
  local function visibility(vm,args,wants,visible)
    assert(#args<=2)
    local changed=c.visible~=visible
    c.visible=visible;notify(vm,'visibility')
    if changed then
      emit(vm,visible and 'CONSOLE_ON' or 'CONSOLE_OFF',{args[1]or 0,args[2]or 0},
        {kind='push',wants=wants})
      return nil,true
    end
    return 0
  end
  define('$console_on',function(vm,args,wants)return visibility(vm,args,wants,true)end)
  define('$console_off',function(vm,args,wants)return visibility(vm,args,wants,false)end)
  define('$console_is_on',function(vm,args)assert(#args==0);return c.visible and 1 or 0 end)
  define('$console_set',function(vm,args)
    assert(#args==5)
    c.configuration=args;c.x,c.y=args[2],args[3]
    local frame=vm.frame and vm:frame()
    c.owner_script=frame and frame.script.name
    notify(vm,'configure');return 0
  end)
  define('$console_setzerotime',function(vm,args)
    -- Original 0x446A40 is an argument-free command.
    assert(#args==0);c.zero_time=true;return 0
  end)
  define('$console_resetzerotime',function(vm,args)
    assert(#args==0);c.zero_time=false;return 0
  end)
  define('$console_dispose',function(vm,args)
    assert(#args==0)
    -- The original removes the latest history group from its byte ring,
    -- preserving earlier entries and the live console configuration.
    local removed=table.remove(c.history)
    if removed then c.history_bytes=math.max(0,(c.history_bytes or 0)-(removed.bytes or#removed.text))end
    c.replay_cursor=nil;return 0
  end)
  define('$console_color',function(vm,args)
    assert(#args==1 or #args==2);c.color=args[1];c.shadow=args[2] or -1
    notify(vm,'color');return 0
  end)
  define('$console_font',function(vm,args)
    assert(#args>=1 and #args<=5 and type(args[1])=='string')
    local legacy=(options.text_controls or{}).legacy_font_defaults
    local prior=c.font_spec or{'',24,12,38,0}
    local height,width,step,style=args[2]or(legacy and 20 or -1),
      args[3]or -1,args[4]or -1,args[5]or 0
    local size=(legacy or height~=-1)and height or prior[2]
    c.font_spec={args[1]~=''and args[1]or prior[1],size,
      width==-1 and(size<0 and math.ceil(size/2)or math.floor(size/2))or width,
      step~=-1 and step or(legacy and(size<0 and math.ceil(size*5/4)or math.floor(size*5/4))or prior[4]),
      style~=-1 and style or(legacy and 0 or prior[5])}
    notify(vm,'font');return 0
  end)
  define('$console_locate',function(vm,args)
    assert(#args==2);c.x,c.y=args[1],args[2];notify(vm,'locate');return 0
  end)
  define('$console_locate_force',function(vm,args)
    assert(#args==2);c.x,c.y=args[1],args[2];notify(vm,'locate');return 0
  end)
  define('$console_curpos_x',function(vm,args)assert(#args==0);return c.x end)
  define('$console_curpos_y',function(vm,args)assert(#args==0);return c.y end)
  define('$console_font_height_get',function(vm,args)
    assert(#args==0);return c.font_spec and c.font_spec[2]or 24
  end)
  define('$get_last_msg$',function(vm,args)assert(#args==0);return c.last_message end)
  define('$console_num_hist',function(vm,args)assert(#args==0);return #c.history end)
  local function history_depth(args,delta)
    assert(#args==0)
    c.history_depth=((c.history_depth or 0)+delta+2147483648)%4294967296-2147483648
    c.enabled_history=c.history_depth==0
    return 0
  end
  define('$histbuff_enable',function(vm,args)return history_depth(args,-1)end)
  define('$histbuff_disable',function(vm,args)return history_depth(args,1)end)
  define('$histbuff_clear',function(vm,args)
    assert(#args==0);c.history={};c.history_bytes=0;return 0
  end)
  local function position(value)
    if value==-1 or value==-2 then return #c.history+1 end
    assert(type(value)=='number'and value%1==0,'invalid history position')
    return value
  end
  -- History positions are opaque to the source scripts. Keep page records
  -- with explicit glyph positions so replay never reparses translated text.
  local function previous(vm,args)
    assert(#args==1)
    local at=position(args[1])-1
    return #c.history>0 and math.max(1,at)or -1
  end
  local function next_page(vm,args)
    assert(#args==1)
    local at=position(args[1])+1
    return #c.history>0 and math.min(#c.history,at)or -1
  end
  define('$console_prev',previous);define('$console_prev_wipe',previous)
  define('$console_next',next_page);define('$console_next_wipe',next_page)
  define('$console_hist_pos',function(vm,args)
    assert(#args==1)
    local at=#c.history-math.max(1,math.floor(args[1]))+1
    return #c.history>0 and math.max(1,at)or -1
  end)
  define('$console_num_hist_wipe',function(vm,args)assert(#args==0);return #c.history end)
  local function redraw(vm,action)
    local entry=action.entry
    local font=c.font_spec or entry.font_spec
    local area=c.configuration or entry.configuration
    assert(font and area,'history redraw has no font or target')
    -- NAME_DISP owns the name line and advances the history cursor before
    -- the body. Use its resulting font, colors and position.
    local voice_top=c.y
    local part,steps='',{};local x,y=c.x,c.y
    local function segment()
      if part==''then return end
      c.page[#c.page+1]={text=part,x=x,y=y}
      local text=options.display_text and options.display_text(part)or part
      local request=string.format('glyphs %d %d %d %d %d %d %d\n%s\n%s\n%s',
        area[1],x,y,font[2],font[3],c.color,c.shadow or 0,options.font_face or font[1],
        table.concat(steps,' '),text)
      local reply=options.graphics(request)
      assert(reply:sub(1,2)=='OK','history glyph rendering failed')
      part='';steps={}
    end
    for char in entry.text:gmatch('[%z\1-\127\194-\244][\128-\191]*')do
      local encode=options.display_bytes or options.cp932
      local bytes=encode and #encode(char)or #char
      local advance=font[3]*(bytes==1 and 1 or 2)
      if char=='\n'then
        segment();c.x=area[2];c.y=c.y+font[4];x,y=c.x,c.y
      else
        if c.x>area[2]and c.x+advance>area[2]+area[4]then
          segment();c.x=area[2];c.y=c.y+font[4];x,y=c.x,c.y
        end
        part=part..char;steps[#steps+1]=tostring(advance);c.x=c.x+advance
      end
    end
    segment()
    if entry.voice then
      c.voice_rects[1]={top=voice_top,height=c.y-voice_top+font[2],
        name=entry.voice,alt_name=entry.voice_alt or''}
    end
    c.visible=true;c.raster_only=true;state.dirty=true;notify(vm,'redraw')
  end
  define('$console_redraw',function(vm,args,wants)
    assert(#args==1)
    local at=args[1]<0 and #c.history or position(args[1])
    local entry=c.history[at]
    if not entry then return -1 end
    local area=assert(c.configuration or entry.configuration,'history target missing')
    c.text,c.name=entry.text,entry.name;c.page={};c.voice_rects={}
    c.x,c.y=area[2],area[3]
    -- This continuation contains only serializable data, so a save inside
    -- a source callback can resume redraw without retaining Lua closures.
    emit(vm,'NAME_DISP',{entry.name or''},
      {kind='host',name='console_redraw',entry=entry,done={kind='push',wants=wants}})
    return nil,true
  end)
  define('$console_replay',function(vm,args,wants)
    assert(#args==1)
    local at=args[1]<0 and#c.history or position(args[1])
    local entry=c.history[at]
    if not entry then return 0 end
    local font=c.font_spec or entry.font_spec
    local area=c.configuration or entry.configuration
    if not font or not area then vm:fail('console replay has no font or target')end
    c.replay_cursor=at
    c.text,c.name=entry.text,entry.name
    c.page={}
    for _,part in ipairs(entry.page or{})do
      local text=options.display_text and options.display_text(part.text)or part.text
      local request=string.format('text %d %d %d %d %d %d 0 0 -1\n%s\n%s',
        area[1],part.x,part.y,font[2],font[3],part.color or c.color,options.font_face or font[1],text)
      assert(options.graphics(request):sub(1,2)=='OK','console replay raster failed')
      c.page[#c.page+1]={text=part.text,x=part.x,y=part.y}
    end
    c.x,c.y=entry.x or area[2],entry.y or area[3]
    if entry.voice and state.media then state.media.text_voice(vm,'v1'..entry.voice,1)end
    c.visible=true;c.raster_only=true;c.replay_cursor=nil;state.dirty=true
    notify(vm,'replay')
    emit(vm,'CONSOLE_WROTE',{area[1],area[2],area[3],area[4],area[5],c.zero_time and 1 or 0},
      {kind='push',wants=wants})
    return nil,true
  end)
  define('$voicerect_num',function(vm,args)assert(#args==0);return #(c.voice_rects or{})end)
  for _,entry in ipairs{{'$voicerect_top','top',0},{'$voicerect_height','height',0},
    -- The original keeps primary and additional voice lists in distinct
    -- buffers. Aliasing them makes source history replay the same line twice.
    {'$voicerect_name$','name',''},{'$voicerect_name_alt$','alt_name',''}}do
    local field,default=entry[2],entry[3]
    define(entry[1],function(vm,args)
      assert(#args==1)
      local rectangle=(c.voice_rects or{})[args[1]+1]
      return rectangle and rectangle[field]or default
    end)
  end
  return c,{redraw=redraw}
end
return M
