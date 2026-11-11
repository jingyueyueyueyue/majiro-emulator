-- Source callback-driven mouse capture, independent of areaevent menus.
local M={}
function M.install(define,state,options)
  state.mouse_capture=state.mouse_capture or{depth=0,left={},right={},buttons={}}
  local function capture()return state.mouse_capture end
  local function register(args,count)
    assert(#args<=count,'mouse capture callback signature')
    local values={}
    for i=1,count do
      local value=args[i]or 0
      assert(type(value)=='number'and value%1==0,'mouse capture callback hash required')
      values[i]=value%4294967296
    end
    return values
  end
  define('$mouse_capture_init',function(vm,args)
    local c=capture();c.callbacks=register(args,3);c.depth=c.depth+1
    c.x,c.y=state.input.x,state.input.y;c.last_x,c.last_y=c.x-999,c.y-999
    c.buttons={};c.events={};return 0
  end)
  define('$mouse_capture_end',function(vm,args)
    assert(#args==0);local c=capture();c.depth=c.depth-1;c.buttons={};c.events={}
    return 0
  end)
  for _,name in ipairs{'left','right'}do
    local button=name
    define('$set_mouse_capture_'..button,function(vm,args)
      capture()[button]=register(args,7);return 0
    end)
  end
  define('$set_mouse_capture_mid',function(vm,args)
    -- The recovered original wrapper only pushes integer zero; it has no
    -- callback table or platform operation, even when given arguments.
    return 0
  end)
  state.capture_sample=function(event)
    local c=capture();if c.depth<=0 then return end
    c.events=c.events or{}
    c.x,c.y=state.input.x,state.input.y
    local held=event.held_buttons or state.input.held_buttons or 0
    local now=(options.wall_now or options.now)()
    for _,entry in ipairs{{'left',1},{'right',2}}do
      local name,bit=entry[1],entry[2]
      local button=c.buttons[name]or{down=false};c.buttons[name]=button
      local down=math.floor(held/bit)%2==1
      local function queue(slot,x,y)
        c.events[#c.events+1]={hash=(c[name]or{})[slot]or 0,args={x,y}}
      end
      if down and not button.down then
        button.x,button.y,button.began=c.x,c.y,now;queue(1,c.x,c.y)
      elseif down and button.down then
        if not button.drag and(math.max(math.abs(c.x-button.x),math.abs(c.y-button.y))>8
          or math.abs(now-button.began)>250)then
          button.drag=true;queue(4,button.x,button.y)
        end
        if button.drag then queue(5,c.x,c.y)end
      elseif not down and button.down then
        if button.drag then queue(6,c.x,c.y)
        elseif button.last_click and math.abs(now-button.last_click)<250 and
          math.max(math.abs(c.x-(button.last_x or c.x)),math.abs(c.y-(button.last_y or c.y)))<=8 then
          queue(3,c.x,c.y);button.last_click=nil
        else queue(2,button.x,button.y);button.last_click=now;button.last_x,button.last_y=c.x,c.y end
        button.drag=false
      end
      button.down=down
    end
  end
  define('$do_mouse_capture',function(vm,args,wants)
    assert(#args==0);local c=capture()
    if c.depth<=0 then vm:fail('mouse capture is not initialized')end
    local calls={}
    local function queue(hash,values)
      if not hash or hash==0 then return end
      local fn=vm:resolve(hash)
      if not fn then vm:fail('mouse capture function missing: '..tostring(hash))end
      calls[#calls+1]={script=fn.script.name,offset=fn.offset,args=values}
    end
    local keys=state.input.pressed or{}
    local mask=(keys[33]and 1 or 0)+(keys[34]and 2 or 0)+(keys[147]and 4 or 0)
    if mask~=0 then queue(c.callbacks[3],{c.x,c.y,mask})end
    for _,event in ipairs(c.events or{})do queue(event.hash,event.args)end;c.events={}
    local left=c.buttons.left and c.buttons.left.down and 1 or 0
    local right=c.buttons.right and c.buttons.right.down and 1 or 0
    if c.x~=c.last_x or c.y~=c.last_y then queue(c.callbacks[2],{c.x,c.y,left,right})end
    queue(c.callbacks[1],{c.x,c.y,left,right});c.last_x,c.last_y=c.x,c.y
    vm:resume{kind='sequence',calls=calls,done={kind='push',wants=wants}}
    return nil,true
  end)
end
return M
