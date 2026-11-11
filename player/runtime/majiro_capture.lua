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
    c.buttons={};c.events={};c.sampled_buttons=state.input.held_buttons or 0;c.cancelled=nil;return 0
  end)
  define('$mouse_capture_end',function(vm,args)
    -- Original end only reduces the global listener depth. A nested
    -- caller may finish while another still owns a pending click/drag.
    -- Only a new init resets the captured button history.
    assert(#args==0);local c=capture();c.depth=c.depth-1
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
    c.x,c.y=state.input.x,state.input.y
    local held=event.held_buttons or state.input.held_buttons or 0
    local previous=c.sampled_buttons or 0;c.sampled_buttons=held
    local now=(options.wall_now or options.now)()
    for _,entry in ipairs{{'left',1},{'right',2}}do
      local name,bit=entry[1],entry[2]
      local button=c.buttons[name]or{down=false};c.buttons[name]=button
      local down=math.floor(held/bit)%2==1
      local was_down=math.floor(previous/bit)%2==1
      if down and not was_down then
        local double=button.pending_click and math.abs(now-button.pending_click)<=250 and
          math.max(math.abs(c.x-(button.x or c.x)),math.abs(c.y-(button.y or c.y)))<=8
        button.x,button.y=c.x,c.y;button.down=true;button.release_pending=nil
        button.drag=false;button.double=double or nil
        if not double then button.pending_click=now end
      elseif not down and was_down then
        -- WM_*BUTTONUP is applied after do_mouse_capture dispatch. The
        -- release poll still exposes down=1; drag-end arrives next poll.
        button.release_pending=true
      end
    end
    if c.buttons.left and c.buttons.left.down and c.buttons.right and c.buttons.right.down then
      c.cancelled=true;c.buttons.left.down=false;c.buttons.right.down=false
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
    keys[33],keys[34],keys[147]=nil,nil,nil
    local now=(options.wall_now or options.now)()
    if c.cancelled then
      -- Both original cancel branches pass the saved left-button press
      -- coordinates (0x463AC3/0x463AE8), including a right-button drag.
      local origin=c.buttons.left or{}
      for _,name in ipairs{'left','right'}do
        local b=c.buttons[name]
        if b and b.previous_drag then
          queue((c[name]or{})[7],{origin.previous_x or 0,origin.previous_y or 0})
        end
      end
      c.buttons={};c.cancelled=nil;c.x,c.y,c.last_x,c.last_y=0,0,0,0
    else
      for _,name in ipairs{'left','right'}do
        local b=c.buttons[name]
        local function button_call(slot,x,y)queue((c[name]or{})[slot],{x,y})end
        if b then
          if b.down and not b.previous_down then button_call(1,c.x,c.y)end
          if b.double then
            button_call(3,c.x,c.y);b.double=nil;b.pending_click=nil;b.double_held=true
          elseif b.pending_click and(math.max(math.abs(c.x-b.x),math.abs(c.y-b.y))>8 or
              math.abs(now-b.pending_click)>250)then
            if b.down then b.drag=true else button_call(2,b.x,b.y)end
            b.pending_click=nil
          end
          if b.drag then
            if b.previous_drag then button_call(5,c.x,c.y)else button_call(4,b.x,b.y)end
          end
          if b.drag_ended then button_call(6,c.x,c.y);b.drag_ended=nil;b.drag=false end
        end
      end
    end
    local left=c.buttons.left and c.buttons.left.down and 1 or 0
    local right=c.buttons.right and c.buttons.right.down and 1 or 0
    if c.x~=c.last_x or c.y~=c.last_y then queue(c.callbacks[2],{c.x,c.y,left,right})end
    queue(c.callbacks[1],{c.x,c.y,left,right});c.last_x,c.last_y=c.x,c.y
    for _,b in pairs(c.buttons)do
      b.previous_down,b.previous_drag=b.down,b.drag
      b.previous_x,b.previous_y=b.x,b.y
      if b.release_pending then
        b.release_pending=nil;b.down=false
        if b.drag then b.drag_ended=true;b.drag=false end
        if b.double_held then b.double_held=nil;b.pending_click=nil end
      end
    end
    vm:resume{kind='sequence',calls=calls,done={kind='push',wants=wants}}
    return nil,true
  end)
end
return M
