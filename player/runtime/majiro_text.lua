-- Source text/proc/ctrl instructions and page-control events.
local M={}
function M.install(state,options,emit,providers)
  state.text_buffer=''
  local profile=options.text_controls or{}
  local style_fields={'x','y','font','font_spec','configuration','color','shadow',
    'zero_time','text_speed','indentation','force_locate'}
  local function copy(value)
    if type(value)~='table'then return value end
    local result={}
    for key,item in pairs(value)do result[key]=copy(item)end
    return result
  end
  local function integer(vm)
    local value,kind=vm:pop_typed()
    if kind~=0 then vm:fail('integer text control operand required')end
    return value
  end
  local function notify(action)
    state.dirty=true
    if options.console then options.console(action,state.console)end
  end
  local function execute(vm,kind,value)
    local console=state.console
    if kind=='ctrl'then
      local suppress=console.skip_initial_newline
      console.skip_initial_newline=nil
      local area=console.configuration
      if suppress and value:sub(1,1)=='n'and area
        and console.x==area[2]and console.y==area[3]then return end
    end
    if options.online_translation then pcall(options.online_translation.instruction,vm,kind,value)end
    if kind=='text' then
      if state.text_buffer==''then state.text_translation_ready=nil end
      local key=vm:frame().script.name..':'..(vm.current_offset or -1)
      state.reading_positions=state.reading_positions or{}
      if not state.reading_positions[key]then
        local read=state.progress.read[key]or false
        if next(state.reading_positions)==nil then state.current_text_read=read
        else state.current_text_read=state.current_text_read and read end
        state.reading_positions[key]=true
      end
      state.text_buffer=state.text_buffer..value
      return
    elseif kind=='proc' then
      if state.text_buffer==''then return end
      console.raster_only=true
      -- Original 0x4451C5 -> 0x443320 enters CONSOLE_ON then retries proc.
      -- The hook moves the message-window/letterbox sprites into place.
      if not console.visible then
        vm:frame().index=vm:frame().index-1
        providers['$console_on'](vm,{},false)
        return
      end
      state.text_buffer=state.text_buffer:gsub('|',' ')
      -- Original 0x445240/0x4441C0 consumes the speaker first, dispatches
      -- NAME_DISP, and retries this instruction with the remaining dialogue.
      -- The source hooks own name rendering, color and portrait behavior.
      if not state.text_translation_ready and tonumber((options.environment or{}).NAMEPOPUP)==1 then
        local quote=state.text_buffer:find('「',1,true)
        if quote and quote>1 then
          local name=state.text_buffer:sub(1,quote-1)
          -- Keep annotations for the source voice/character callbacks.
          -- console.mjo 0x401E alone normalizes the visible nameplate:
          -- leading ＠, note separators, brackets and two-character spacing.
          -- For example 女の子＠依知子 displays 女の子 while the callback
          -- still receives the hidden character identity 依知子.
          state.text_buffer=state.text_buffer:sub(quote)
          console.name=name
          vm:frame().index=vm:frame().index-1
          emit(vm,'NAME_DISP',{name},nil)
          return
        end
      end
      if not state.text_translation_ready and options.online_translation then
        local ok,display,waiting=pcall(options.online_translation.display,vm,state.text_buffer)
        if ok and waiting then
          vm:frame().index=vm:frame().index-1
          vm.waiting={kind='time',until_ms=options.now()+50}
          return
        end
        if ok and type(display)=='string'then state.text_buffer=display end
        state.text_translation_ready=true
      end
      local startx,starty=console.x,console.y
      local font=console.font_spec or {'',24,12,38}
      local area=assert(console.configuration,'console destination missing')
      if console.y>area[3]+area[5]-font[2]then
        console.x,console.y=area[2],area[3];console.indentation=0
        vm:frame().index=vm:frame().index-1
        notify('overflow')
        emit(vm,'CONSOLE_CLS',{},nil)
        return
      end
      local part,steps,consumed='',{},0
      for char in state.text_buffer:gmatch('[%z\1-\127\194-\244][\128-\191]*')do
        local encode=options.display_bytes or options.cp932
        local encoded=encode and encode(char)or char
        local advance=(font[3]or 12)*(#encoded==1 and 1 or 2)
        local area=console.configuration
        if area and console.x>area[2]and console.x+advance>area[2]+area[4]then
          break
        end
        part=part..char
        steps[#steps+1]=tostring(advance);consumed=consumed+#char
        console.x=console.x+advance
      end
      console.text=console.text..part
      console.page=console.page or {}
      console.page[#console.page+1]={text=part,x=startx,y=starty,font_spec=font,color=console.color,
        script=vm:frame().script.name,offset=vm.current_offset}
      if console.enabled_history and not console.overlay_active then
        console.page_message=(console.page_message or'')..part
        if not(profile.message_first_line_only and console.message_line_closed)then
          console.last_message=(console.last_message or'')..part
        end
        console.history_text=(console.history_text or'')..part
        console.history_page=console.history_page or{}
        console.history_page[#console.history_page+1]=copy(console.page[#console.page])
      end
      local face=state.settings.font_name or font[1]
      if not face or face==''or face=='ＭＳ ゴシック'or face=='MS Gothic'then
        face=options.font_face or face
      end
      local reply=options.graphics(string.format('glyphs %d %d %d %d %d %d %d\n%s\n%s\n%s',
        area[1],startx,starty,font[2],font[3],console.color,console.shadow or 0,
        face,table.concat(steps,' '),options.display_text and options.display_text(part)or part))
      assert(reply:sub(1,2)=='OK','console raster failed: '..reply)
      local width=console.x-startx
      state.text_buffer=state.text_buffer:sub(consumed+1)
      if state.text_buffer~=''then
        console.x=area[2];console.y=console.y+(font[4]or font[2])
        console.message_line_closed=true
      end
      -- The original always retries proc after CONSOLE_WROTE, including
      -- a fully consumed buffer. The callback observes the retry position.
      vm:frame().index=vm:frame().index-1
      console.visible=true;notify('text')
      -- Original 0x4457B7: page, x, y, width, height, zero-time.
      -- CONSOLE_WROTE copies the glyphs into the visible page and animates
      -- that copy. Its sprite supplies the final screen position and alpha.
      emit(vm,'CONSOLE_WROTE',{area[1],startx,starty,width,font[2],console.zero_time and 1 or 0},nil)
      return
    end
    local command=value:sub(1,1)
    if command=='w' then
      state.reading_wait=false
      local reader=state.reading_context
      if not reader or(reader.thread==vm.thread_id and #vm.frames<=reader.depth)then
        state.reading_context=nil
      end
      console.text='';console.page={};console.name='';console.zero_time=false
      console.last_message='';console.history_text='';console.history_page={};console.page_message=''
      console.message_line_closed=false
      console.text_speed=-1;console.indentation=0;console.overlay_active=false
      state.voice_for_history=nil;state.text_translation_ready=nil
      -- Original ctrl-w keeps the previous read flag while CONSOLE_CLS
      -- runs. Its animation may query skip before any next-page text
      -- exists. The first real text opcode recomputes the flag above.
      state.reading_positions={}
      console.x,console.y=console.configuration and console.configuration[2]or 0,
        console.configuration and console.configuration[3]or 0
      notify('clear')
      emit(vm,'CONSOLE_CLS',{},nil)
    elseif command=='p'or command=='P' then
      if command=='P'and profile.uppercase_pause_event==false then return end
      console.zero_time=false;console.text_speed=-1
      for key in pairs(state.reading_positions or{})do
        if not state.progress.read[key]then state.progress.read[key]=true;state.persist_dirty=true end
      end
      state.reading_wait=true
      state.reading_generation=(state.reading_generation or 0)+1
      state.present_requested=state.present_requested+1
      state.fast_page={generation=state.present_requested,
        started=not(options.wait_for_present and options.wait_for_present())and
          (options.wall_now or options.now)()or nil}
      if console.enabled_history and not console.overlay_active and(console.history_text or'')~='' then
        local history_text=console.history_text or''
        local encode=options.display_bytes or options.cp932
        local bytes=encode and #encode(history_text)or #history_text
        console.history[#console.history+1]={text=history_text,name=console.name,
          page=console.history_page,bytes=bytes,
          x=console.x,y=console.y,color=console.color,shadow=console.shadow,
          configuration=console.configuration,font_spec=console.font_spec,
          voice=state.voice_for_history or
            (state.media and state.media.channels.voice and state.media.channels.voice.name)}
        console.history_bytes=(console.history_bytes or 0)+bytes
        -- Original 0x443100 walks a fixed 40,000-byte history ring.
        while console.history_bytes>40000 and #console.history>1 do
          local removed=table.remove(console.history,1)
          console.history_bytes=console.history_bytes-(removed.bytes or #removed.text)
        end
      end
      emit(vm,command=='P'and 0x7ee67053 or'PAUSE',{},nil)
    elseif command=='n'or command=='N' then
      console.page_message=''
      console.message_line_closed=true
      console.text=console.text..'\n'
      if console.enabled_history and not console.overlay_active then
        console.history_text=(console.history_text or'')..'\n'
      end
      console.y=console.y+((console.font_spec and console.font_spec[4])or 38)
      console.x=console.configuration and console.configuration[2]or 0;notify('newline')
      emit(vm,'CRLF',{},nil)
    elseif command=='x' then
      local text,kind=vm:pop_typed()
      if kind~=2 then vm:fail('string text control operand required')end
      emit(vm,'X_CONTROL',{text},nil)
    elseif command=='d' then
      local text,kind=vm:pop_typed()
      if kind==0 then text=string.format('%d',text)
      elseif kind==1 then text=string.format('%f',text)
      elseif kind~=2 then vm:fail('numeric or string text control operand required')end
      state.text_buffer=state.text_buffer..text
      if state.text_buffer==text then state.text_translation_ready=nil end
      if options.online_translation then pcall(options.online_translation.literal,vm,text)end
    elseif command=='f'then
      -- Original 0x4459C3/0x443EC2 consumes five values: style, family,
      -- line step, halfwidth advance, height. The syscall applies the
      -- recovered version's defaults to the same formatted control.
      local style=integer(vm)
      local face,kind=vm:pop_typed()
      if kind~=2 then vm:fail('font family string required')end
      local step,width,height=integer(vm),integer(vm),integer(vm)
      providers['$console_font'](vm,{face,height,width,step,style})
    elseif command=='c'then
      local shadow,color=vm:pop(),vm:pop()
      console.color,console.shadow=color,shadow;notify('color')
    elseif command=='s' then
      console.text_speed=integer(vm)
    elseif command=='t'then
      local milliseconds=integer(vm)
      if not console.zero_time then
        vm.waiting={kind='time',until_ms=options.now()+milliseconds,fast_interrupt=true}
      end
    elseif command=='z' then console.zero_time=true
    elseif command=='o' then
      local y,x=integer(vm),integer(vm)
      local saved={x=console.x,y=console.y}
      if profile.overlay_saves_style~=false then
        for _,field in ipairs(style_fields)do saved[field]=copy(console[field])end
      end
      console.overlay_active=profile.overlay_suppresses_message~=false
      console.saved_style=saved
      console.x,console.y=console.x+x,console.y+y
    elseif command=='r' then
      local style=console.saved_style
      if style then
        if profile.overlay_saves_style~=false then
          for _,field in ipairs(style_fields)do console[field]=copy(style[field])end
        else console.x,console.y=style.x,style.y end
      end
      console.overlay_active=false
    elseif command=='l'then
      local y,x=integer(vm),integer(vm)
      console.x,console.y=x,y;notify('locate')
    elseif command=='g'then
      local values,args={},{}
      for i=6,1,-1 do values[i]=integer(vm)end
      for _,item in ipairs(values)do if item~=-99 then args[#args+1]=item end end
      emit(vm,'GAIZI_OUT',args,nil)
    elseif command=='v'then
      state.media.text_voice(vm,value,1)
    else vm:fail('text control not implemented: '..value)end
  end
  return {execute=execute}
end
return M
