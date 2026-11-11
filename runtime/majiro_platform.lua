-- Emulated platform state; physical cursor/caps changes require host support.
local M={}
local function integer(value)
  assert(type(value)=='number'and value%1==0,'platform integer argument required')
  return((value+2147483648)%4294967296)-2147483648
end
function M.install(define,state,options,screen)
  local profile=options.platform_controls or{containment='modern_margin_inclusive',mouse_move='modern'}
  state.platform=state.platform or{client_width=options.width,client_height=options.height,
    drag_accept=0,drop_bytes='',cursor_available=true,caps_guard=-1}
  local function current()return state.platform end
  local function host(vm,name,args)
    if not options.platform then vm:fail('platform host provider missing: '..name)end
    local value=options.platform(name,args)
    if value==nil or value==false then vm:fail('platform host operation failed: '..name)end
    return value
  end
  local function noargs(args)assert(#args==0,'platform query takes no arguments')end
  local function sample(event)
    local p=current()
    if event.client_width~=nil then p.client_width=integer(event.client_width)end
    if event.client_height~=nil then p.client_height=integer(event.client_height)end
    if event.cursor_available~=nil then p.cursor_available=event.cursor_available end
    if event.drop_bytes~=nil then
      assert(type(event.drop_bytes)=='string'and not event.drop_bytes:find('\0',1,true),
        'drop filename must preserve source bytes without NUL')
      if p.drag_accept~=0 then p.drop_bytes=event.drop_bytes:sub(1,259)end
    end
  end
  state.platform_sample=sample
  define(0x2bd1709d,function(vm,args)noargs(args);return current().client_height end)
  define(0x7e0b8320,function(vm,args)noargs(args);return current().client_width end)
  define(0x42d7c922,function(vm,args)
    assert(#args==1);local p=current();p.drag_accept=integer(args[1])
    if p.drag_accept~=0 then p.drop_bytes=''end
    return 0
  end)
  define(0xc32e286f,function(vm,args)noargs(args);return current().drop_bytes end)
  define(0x3670b283,function(vm,args)
    noargs(args);local p=current();local input=state.input
    if not p.cursor_available then return 0 end
    local x,y=input.physical_x,input.physical_y
    if x==nil or y==nil then return 0 end
    local w,h=p.client_width,p.client_height
    local mode=profile.containment
    local home=state.screen or{home_x=0,home_y=0}
    if mode=='legacy_exclusive'then return x>=0 and y>=0 and x<w and y<h and 1 or 0 end
    if mode=='fixed_offset_inclusive'then x,y=x+home.home_x,y+home.home_y
    elseif mode=='modern_margin_inclusive'then w,h=w+2*home.home_x,h+2*home.home_y
    else vm:fail('unrecovered platform containment profile')end
    return x>=0 and y>=0 and x<=w and y<=h and 1 or 0
  end)
  define('$keybd_disable',function(vm,args)
    noargs(args);local previous=state.settings.keyboard_disabled
    state.settings.keyboard_disabled=true;return previous and 0 or 1
  end)
  define('$keybd_enable',function(vm,args)
    noargs(args);state.settings.keyboard_disabled=false
    state.input.pressed={};state.input.down={};state.input.keyboard_timestamps={}
    if profile.keyboard_enable_clears_caps and integer(host(vm,'caps_get',{}))%2~=0 then
      host(vm,'caps_set',{0})
    end
    current().caps_guard=-1;return 0
  end)
  define('$get_caps_stat',function(vm,args)
    noargs(args);return integer(host(vm,'caps_get',{}))%2
  end)
  define('$set_caps_stat',function(vm,args)
    assert(#args==1);local desired=integer(args[1])~=0
    if current().caps_guard==-1 then
      local old=integer(host(vm,'caps_get',{}))%2~=0
      if old~=desired then host(vm,'caps_set',{desired and 1 or 0})end
    end
    return 0
  end)
  define('$mouse_move',function(vm,args)
    assert(#args==2);local x,y=integer(args[1]),integer(args[2])
    local cx,cy=screen.client(x,y)
    local px,py=cx,cy
    if profile.mouse_move=='fixed_plus'then
      local home=state.screen;px,py=integer(cx+2*home.home_x),integer(cy+2*home.home_y)
    end
    host(vm,'cursor_move',{px,py})
    if x==-10000 then state.input.cursor_override=nil
    else state.input.cursor_override={x=cx,y=cy,client=true}end
    return 0
  end)
  return{sample=sample,profile=profile}
end
return M
