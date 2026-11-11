-- Native dialog family. Host requests are asynchronous and never invent an answer.
local M={}
local flags={
  dialog_ok=0,dialog_okcancel=1,dialog_yesno=4,dialog_retrycancel=5,
  dialog_ask_ok=32,dialog_ask_okcancel=33,dialog_ask_yesno=36,
  dialog_warning_ok=48,dialog_warning_okcancel=49,dialog_warning_yesno=52,
  dialog_warning_retrycancel=37,dialog_error_ok=16,dialog_error_okcancel=17,
  dialog_error_yesno=20,dialog_error_retrycancel=21,
}
function M.install(define,state,options,saves)
  local current,next_id=nil,0
  local error_provider
  local function failure(vm,message)
    if vm.fail then vm:fail(message)end
    error(message,0)
  end
  local function format(vm,args,display)
    local pieces={}
    for i,value in ipairs(args)do
      local kind=args._types and args._types[i]
      if kind==nil then
        kind=type(value)=='string'and 2 or(type(value)=='table'and 3+value.element_type or
          (value%1~=0 and 1 or 0))
      end
      if kind==0 then pieces[i]=string.format('%d',value)
      elseif kind==1 then
        if value~=value or math.abs(value)==math.huge then
          failure(vm,'nonfinite dialog formatting requires original CRT profile')
        end
        pieces[i]=string.format('%.6f',value)
      elseif kind==2 then pieces[i]=value:match('^[^%z]*')
      elseif kind>=3 and kind<=5 then
        pieces[i]=({'[INT_ARRAY]','[FIXED_ARRAY]','[STRING_ARRAY]'})[kind-2]
      else failure(vm,'unsupported dialog argument type '..tostring(kind))end
      pieces[i]=pieces[i]..' '
    end
    local raw=table.concat(pieces)
    -- Strings retain VM bytes/identity until the actual display boundary.
    return display~=false and options.display_text and options.display_text(raw)or raw
  end
  local function host(vm,action,request)
    if not options.dialog then failure(vm,'dialog host provider unavailable')end
    local result=options.dialog(action,request.token,request.flags,request.message,request.caption)
    if type(result)~='string'then failure(vm,'invalid dialog host response')end
    return result
  end
  for name,bits in pairs(flags)do
    local key,style=name,bits
    local provider=function(vm,args,wants)
      local error_dialog=key:sub(1,13)=='dialog_error_'
      if error_dialog and not options.testplay then return style%16==0 and 0 or 1 end
      if current then failure(vm,'nested native dialog request')end
      next_id=next_id+1
      local request={token=tostring(next_id),flags=style,message=format(vm,args),
        caption=(options.environment or{}).TITLEBAR or(options.environment or{}).APPNAME or'',
        thread=vm.thread_id,wants=wants,save=state.settings.save,save_menu=state.settings.save_menu}
      local result=host(vm,'begin',request)
      if result~='pending'then failure(vm,'cannot start dialog: '..result)end
      current=request;state.settings.save=false;state.settings.save_menu=false
      local controls=options.dialog_controls or{}
      if(error_dialog and controls.reset_error_playback_modes~=false)or
        (not error_dialog and controls.reset_playback_modes~=false)then
        state.settings.auto_mode=false;state.settings.fast_mode=false
        state.settings.fast_read_mode=false;state.settings.ultra_fast_mode=false
      end
      vm.modal_dialog=request.token
      state.ui_waiting=false
      vm.waiting={kind='dialog',token=request.token}
      return nil,true
    end
    define('$'..key,provider)
    if key=='dialog_error_ok'then error_provider=provider end
  end
  define('$debugout',function(vm,args)
    if not options.testplay then return 0 end
    if not options.debug_output then failure(vm,'diagnostic output host provider missing')end
    local frame=vm.frame and vm:frame()
    options.debug_output(format(vm,args,false),frame and frame.script.name or'',vm.current_offset or-1)
    return 0
  end)
  define('$debugout_dialog',function(vm,args,wants)
    if not options.testplay then failure(vm,'debugout_dialog production buffer is undefined in original')end
    if options.debug_output then
      local frame=vm.frame and vm:frame()
      options.debug_output(format(vm,args,false),frame and frame.script.name or'',vm.current_offset or-1)
    end
    -- This variant uses the error icon and has an unconditional dialog in
    -- the original; only its logger performs the production guard.
    return error_provider(vm,args,wants)
  end)
  local function begin_special(vm,request,wants)
    if current then failure(vm,'nested native dialog request')end
    next_id=next_id+1
    request.token=tostring(next_id);request.thread=vm.thread_id;request.wants=wants
    request.save,request.save_menu=state.settings.save,state.settings.save_menu
    local result=host(vm,'begin',request)
    if result~='pending'then failure(vm,'cannot start dialog: '..result)end
    current=request;state.settings.save=false;state.settings.save_menu=false
    vm.modal_dialog=request.token;vm.waiting={kind='dialog',token=request.token}
    return nil,true
  end
  define('$dialog_input$',function(vm,args,wants)
    assert((#args==1 or#args==2)and type(args[1])=='string'and
      (args[2]==nil or type(args[2])=='string'),'dialog_input signature')
    return begin_special(vm,{kind='input',flags=65537,
      message=options.display_text and options.display_text(args[1])or args[1],
      caption=options.display_text and options.display_text(args[2]or'')or(args[2]or'')},wants)
  end)
  local slot_picker=options.modules('runtime/majiro_slot_picker.lua').new(state,assert(saves).key,
    options.modules('runtime/majiro_json.lua'))
  define(0x9af10167,function(vm,args,wants)
    assert(#args==2 and type(args[1])=='number'and type(args[2])=='number','native slot picker signature')
    local request=slot_picker.request(args[1]~=0,args[2])
    request.kind='slot';request.flags=327681
    request.caption=args[1]~=0 and'Load game'or'Save game'
    return begin_special(vm,request,wants)
  end)
  for _,entry in ipairs{{'all_setup_dialog',131073},{'volume_setup_dialog',196609},
    {'speed_setup_dialog',262145}}do
    local name,style=entry[1],entry[2]
    define('$'..name,function(vm,args,wants)
      assert(#args==0,'setup dialog takes no arguments')
      local fields=name=='volume_setup_dialog'and{'musicvolume','soundvolume','voicevolume'}or
        name=='speed_setup_dialog'and{'mojispeed','autospeed','effectspeed'}or
        {'musicvolume','soundvolume','voicevolume','mojispeed','autospeed','effectspeed'}
      local rows={}
      for _,key in ipairs(fields)do rows[#rows+1]=key..'='..string.format('%.0f',state.settings[key]or 0)end
      return begin_special(vm,{kind='setup',flags=style,message=table.concat(rows,'\n'),
        caption=(options.environment or{}).TITLEBAR or'',fields=fields},wants)
    end)
  end
  return{
    active=function()return current~=nil end,
    poll=function(vm)
      if not current then return false end
      local request=current
      local result=host(vm,'poll',request)
      if result=='pending'then return true end
      local answer=tonumber(result:match('^result (%d+)$'))
      local payload=result:match('^data ([0-9a-f]*)$')
      if not answer and not payload then failure(vm,'dialog failed: '..result)end
      local value
      if request.kind then
        if not payload then failure(vm,'dialog returned no data')end
        value=payload:gsub('..',function(hex)return string.char(tonumber(hex,16))end)
        if request.kind=='slot'then
          value=slot_picker.answer(request,value)
        elseif request.kind=='setup'then
          local allowed={};for _,key in ipairs(request.fields)do allowed[key]=true end
          for key,number in value:gmatch('([%w_]+)=([%+%-]?%d+)')do
            if not allowed[key]then failure(vm,'unexpected setup field')end
            number=tonumber(number)
            if math.abs(number)>1000000 then failure(vm,'setup value outside bounds')end
            state.settings[key]=number;state.persist_dirty=true
            if key:match('volume$')and state.media and state.media.volume_changed then
              state.media.volume_changed(vm,key)
            end
          end
          value=0
        end
      else
      local wanted=request.flags%16
      if wanted~=0 and wanted~=1 and wanted~=4 and wanted~=5 then failure(vm,'dialog button mode')end
      if answer~=1 and answer~=2 and answer~=4 and answer~=6 and answer~=7 then
        failure(vm,'invalid native dialog answer')
      end
      value=wanted~=0 and(answer==(wanted==1 and 1 or wanted==4 and 6 or 4))and 1 or 0
      end
      local owner=vm.threads[request.thread]
      if not owner or owner.finished then failure(vm,'dialog owner no longer exists')end
      local previous=vm.thread_id
      vm:select_thread(request.thread)
      if not vm.waiting or vm.waiting.kind~='dialog'or vm.waiting.token~=request.token then
        failure(vm,'dialog owner wait changed')
      end
      vm.waiting=nil
      if request.wants then vm:push(value)end
      vm:save_thread();vm:select_thread(previous)
      state.settings.save,state.settings.save_menu=request.save,request.save_menu
      state.input.buttons=0;state.input.pressed={}
      current=nil
      vm.modal_dialog=nil
      return true
    end,
  }
end
return M
