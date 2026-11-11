-- Majiro's virtual client coordinates are independent of the host's final
-- phone/tablet aspect fit. SDL maps device input into this client first.
local M={}
local function signed(value)return(value+2147483648)%4294967296-2147483648 end
local function trunc(value)return value<0 and math.ceil(value)or math.floor(value)end
local function converted(value,x87)
  if x87 then
    -- MSVC's x87 conversion returns the low dword of a signed 64-bit
    -- result; SSE cvttsd2si instead returns INT_MIN on 32-bit overflow.
    if value~=value or value>=9223372036854775808 or value< -9223372036854775808 then return 0 end
    return signed(trunc(value))
  end
  if value~=value or value>=2147483648 or value< -2147483648 then return -2147483648 end
  return trunc(value)
end
local function multiply(a,b)
  a,b=a%4294967296,b%4294967296
  local low=(a%65536)*(b%65536)
  local high=(math.floor(a/65536)*(b%65536)+(a%65536)*math.floor(b/65536))%65536
  return signed(low+high*65536)
end
function M.install(define,state,options)
  local profile=options.screen_controls or{coordinate_mode='double',home_direction=1,
    home_modulo=false,home_affects_input=true}
  local view={x=0,y=0,sx=1,sy=1}
  local context={}
  local function screen()
    state.screen=state.screen or{home_x=0,home_y=0}
    return state.screen
  end
  screen()
  function context.configure(x,y,sx,sy)
    assert(sx>0 and sy>0 and sx<math.huge and sy<math.huge,'invalid emulated viewport scale')
    if profile.coordinate_mode=='fixed1000'then
      assert(trunc(sx*1000)>0 and trunc(sy*1000)>0,'emulated viewport scale is too small')
    end
    view.x,view.y,view.sx,view.sy=x,y,sx,sy
  end
  local function coordinate(value,origin,home,scale)
    if profile.coordinate_mode=='identity'then return signed(value)end
    local n=signed(trunc(value)-trunc(origin))
    if profile.home_affects_input then n=signed(n-profile.home_direction*home)end
    if profile.coordinate_mode=='fixed1000'then
      return converted(signed(n*1000)/trunc(scale*1000))
    end
    return converted(n/scale,profile.integer_conversion=='x87')
  end
  function context.point(x,y)
    local c=screen()
    return coordinate(x,c.zoom_x or view.x,c.home_x,c.zoom_sx or view.sx),
      coordinate(y,c.zoom_y or view.y,c.home_y,c.zoom_sy or view.sy)
  end
  local function client(value,origin,home,scale)
    if profile.coordinate_mode=='identity'then return signed(value)end
    local base=signed(origin+profile.home_direction*home)
    if profile.coordinate_mode=='fixed1000'then
      return signed(trunc(multiply(value,trunc(scale*1000))/1000)+base)
    end
    return converted(value*scale+base,profile.integer_conversion=='x87')
  end
  function context.client(x,y)
    local c=screen()
    return client(x,c.zoom_x or view.x,c.home_x,c.zoom_sx or view.sx),
      client(y,c.zoom_y or view.y,c.home_y,c.zoom_sy or view.sy)
  end
  function context.kind()
    return profile.coordinate_mode=='identity'and 2 or profile.coordinate_mode=='fixed1000'and 1 or 0
  end
  function context.present(front,path,rows)
    local c=screen()
    if c.zoom_extent then
      return string.format('presentzoom %d %d %d %d %d %s\n%s',
        front,context.kind(),c.home_x,c.home_y,c.zoom_extent,path,rows)
    end
    return string.format('presentview %d %d %d %d %s\n%s',
      front,context.kind(),c.home_x,c.home_y,path,rows)
  end
  define('$grp_home',function(vm,args)
    assert(#args==2 and type(args[1])=='number'and type(args[2])=='number'
      and args[1]%1==0 and args[2]%1==0,'grp_home requires two integers')
    local c=screen();c.home_x,c.home_y=signed(args[1]),signed(args[2])
    state.dirty=true
    if context.changed then context.changed()end
    return 0
  end)
  define('$grp_zoom',function(vm,args)
    assert(#args==1 and type(args[1])=='number','grp_zoom signature')
    local f32=options.modules('runtime/majiro_vm.lua').f32
    local extent=converted(f32(args[1]*1000))
    if extent%2~=0 then extent=extent-1 end
    assert(extent>0 and extent<=32768,'grp_zoom extent outside supported window bounds')
    local c=screen();c.zoom_extent=extent
    c.zoom_x=trunc((options.width-extent)/2);c.zoom_y=trunc((options.height-extent)/2)
    c.zoom_sx,c.zoom_sy=extent/options.width,extent/options.height
    state.dirty=true;if context.changed then context.changed()end;return 0
  end)
  define(0x09c43040,function(vm,args)
    assert(#args>=2 and#args<=4,'virtual screen dimensions signature')
    assert(args[1]>0 and args[2]>0 and args[1]<=32768 and args[2]<=32768,'virtual screen bounds')
    local c=screen()
    c.viewport_width,c.viewport_height=args[1],args[2]
    c.viewport_x,c.viewport_y=args[3]or 0,args[4]or 0
    c.zoom_extent=nil;c.zoom_x,c.zoom_y=c.viewport_x,c.viewport_y
    c.zoom_sx,c.zoom_sy=1,1;state.dirty=true
    if context.changed then context.changed()end
    return 0
  end)
  define(0x29098ec0,function(vm,args)
    assert(#args>=2 and#args<=4,'window resize signature')
    assert(args[1]>0 and args[2]>0,'window resize bounds')
    if not options.resize_window then vm:fail('window resize host provider missing')end
    options.resize_window(args[1],args[2])
    local c=screen()
    c.zoom_x,c.zoom_y=c.viewport_x or 0,c.viewport_y or 0
    c.zoom_sx=(args[3]or 1000)/1000;c.zoom_sy=(args[4]or 1000)/1000
    assert(c.zoom_sx>0 and c.zoom_sy>0,'window coordinate scale')
    state.dirty=true;if context.changed then context.changed()end
    return 0
  end)
  return context
end
return M
