-- Majiro sprite identities and geometry. Pages and sprite lifetimes are separate.
local M={}
function M.install(define,state,options,graphics,providers)
  state.sprites={};state.sprite_order={};state.next_sprite=268435456
  local register=define
  define=function(name,provider)
    if type(name)=='string'and name:match('^%$sprite_')and
       not name:match('^%$sprite_create')and name~='$sprite_is_exist'then
      register(name,function(vm,args,...)
        -- Original wrappers search the live sprite list and return zero
        -- after reporting an expired handle. Background transitions can
        -- reach this path after the owning scene releases its sprite.
        if #args>0 and not state.sprites[args[1]]then
          state.expired_sprite_calls=state.expired_sprite_calls or{}
          state.expired_sprite_calls[name]=(state.expired_sprite_calls[name]or 0)+1
          return 0
        end
        return provider(vm,args,...)
      end)
    else register(name,provider)end
  end
  local function front(vm)
    if state.frontbuffer then return state.frontbuffer end
    local id=1000000
    graphics(vm,string.format('create %d %d %d 0',id,options.width,options.height))
    state.pages[id]={width=options.width,height=options.height,depth=32}
    state.frontbuffer=id
    return id
  end
  local function sprite(vm,id)
    local value=state.sprites[id]
    if not value then vm:fail('sprite not found: '..tostring(id)) end
    return value
  end
  local function update(s)
    for key,sequence in pairs(s.transform_sequences or {})do
      local elapsed=options.now()-sequence.began;local from=sequence.from
      for _,point in ipairs(sequence.points)do
        if point.duration<0 then s[key]=from;break end
        if point.duration>0 and elapsed<point.duration then
          s[key]=from+(point.value-from)*elapsed/point.duration;break
        end
        elapsed=elapsed-point.duration;from=point.value;s[key]=from
      end
    end
    if s.alpha_sequence then
      local sequence=s.alpha_sequence
      local elapsed=options.now()-sequence.began
      local from=sequence.from;local complete=true
      for _,point in ipairs(sequence.points)do
        if point.duration<0 then s.anti=point.value;break end
        if point.duration>0 and elapsed<point.duration then
          s.anti=math.floor(from+(point.value-from)*elapsed/point.duration);complete=false;break
        end
        elapsed=elapsed-point.duration;from=point.value;s.anti=from
      end
      if complete then s.alpha_sequence=nil end
    end
    for key,tween in pairs(s.tweens or {})do
      local p=tween.duration==0 and 1 or math.min(1,math.max(0,(options.now()-tween.began)/tween.duration))
      s[key]=tween.from+(tween.to-tween.from)*p
      if p==1 then s.tweens[key]=nil end
    end
    if s.animation then
      local a=s.animation
      local elapsed=options.now()-a.began
      local cycle=0;local repeating=true
      for _,frame in ipairs(a.frames)do
        if frame.duration<0 then repeating=false;break end
        cycle=cycle+frame.duration
      end
      if repeating and cycle>0 then elapsed=elapsed%cycle end
      local consumed=0;local active,active_index
      for i,frame in ipairs(a.frames)do
        if frame.duration<0 or elapsed<consumed+frame.duration then active=frame;active_index=i;break end
        consumed=consumed+frame.duration
      end
      if not active then active=a.frames[#a.frames];s.animation=nil end
      if active then
        local source=s.animation_source or s.page
        local page=state.pages[source]
        local columns=math.max(1,math.floor(page.width/s.width))
        if active.frame==-2 then s.sx,s.sy=s.base_sx,s.base_sy
        else
          if active.frame<0 then error('unknown negative animation frame')end
          s.sx=s.base_sx+(active.frame%columns)*s.width
          s.sy=s.base_sy+math.floor(active.frame/columns)*s.height
        end
        if s.smooth_page then
          local now=options.now()
          if not s.smooth_updated or now-s.smooth_updated>=50 or s.smooth_frame~=active_index then
            local following=a.frames[(active_index or#a.frames)%#a.frames+1]
            local next_frame=following.frame
            local nx,ny=s.base_sx,s.base_sy
            if next_frame>=0 then nx=s.base_sx+(next_frame%columns)*s.width
              ny=s.base_sy+math.floor(next_frame/columns)*s.height end
            local progress=active.duration>0 and math.min(1,math.abs(elapsed-consumed)/active.duration)or 0
            local anti=255-math.floor(progress*255)
            graphics(nil,string.format('smoothframe %d %d %d %d %d %d %d %d %d',
              source,s.sx,s.sy,nx,ny,s.width,s.height,s.smooth_page,anti))
            s.smooth_updated=now;s.smooth_frame=active_index
          end
          s.page=s.smooth_page;s.sx,s.sy=0,0
        end
      end
    end
    if s.fade then
      local fade=s.fade
      local progress=fade.duration==0 and 1 or math.min(1,math.max(0,(options.now()-fade.began)/fade.duration))
      s.anti=math.floor(fade.from+(fade.to-fade.from)*progress)
      if progress==1 then s.fade=nil end
    end
    if not s.move then return end
    local move=s.move
    local progress=move.duration==0 and 1 or math.min(1,math.max(0,(options.now()-move.began)/move.duration))
    local function curve(a,b)
      local flag=move.flags or 0
      if math.floor(flag/a)%2==1 then return 1-math.cos(progress*math.pi/2) end
      if math.floor(flag/b)%2==1 then return math.sin(progress*math.pi/2) end
      return progress
    end
    local function trunc(x)return x<0 and math.ceil(x) or math.floor(x)end
    s.x=trunc(move.x+(move.tx-move.x)*curve(0x40000000,0x20000000))
    s.y=trunc(move.y+(move.ty-move.y)*curve(0x10000000,0x08000000))
    if progress==1 then s.move=nil end
  end
  local function create(vm,args,extended)
    assert(#args==1 or #args==5 or #args==6,'sprite_create signature')
    local page=state.pages[args[1]]
    if not page then vm:fail('sprite source page missing') end
    local id=state.next_sprite;state.next_sprite=id+1
    local destination=args[6] or front(vm)
    if not state.pages[destination] then vm:fail('sprite destination page missing') end
    local s={id=id,page=args[1],sx=args[2] or 0,sy=args[3] or 0,
      width=args[4] or page.width,height=args[5] or page.height,
      destination=destination,x=100000,y=100000,anti=0,extended=extended,
      xscale=1,yscale=1,rotate=0,tweens={},priority_group=id}
    local frame=vm:frame()
    s.source_script=frame and frame.script.name
    s.base_sx,s.base_sy=s.sx,s.sy
    state.sprites[id]=s;state.sprite_order[#state.sprite_order+1]=id
    return id
  end
  define('$sprite_create',function(vm,args) return create(vm,args,false) end)
  define('$sprite_create_ext',function(vm,args) return create(vm,args,true) end)
  define('$sprite_create_file',function(vm,args)
    local page=providers['$page_create_file_withalfa'](vm,{args[1]})
    local input={page};for i=2,#args do input[i]=args[i] end
    local id=create(vm,input,false);state.sprites[id].auto_release=true;return id
  end)
  define('$sprite_create_file_ext',function(vm,args)
    local page=providers['$page_create_file_withalfa'](vm,{args[1]})
    local input={page};for i=2,#args do input[i]=args[i] end
    local id=create(vm,input,true);state.sprites[id].auto_release=true;return id
  end)
  define('$get_frontbuffer',function(vm,args) assert(#args==0);return front(vm) end)
  define('$set_frontbuffer',function(vm,args)
    assert(#args==1 and state.pages[args[1]],'set_frontbuffer page')
    state.frontbuffer=args[1];state.dirty=true;return 0
  end)
  define('$sprite_move',function(vm,args)
    assert(#args>=3 and #args<=4,'sprite_move signature')
    local s=sprite(vm,args[1]);update(s)
    local packed=args[4] or 0
    local duration=packed%0x1000000
    s.move={x=s.x,y=s.y,tx=args[2],ty=args[3],duration=duration,began=options.now(),flags=packed%4294967296}
    update(s);state.dirty=true;return 0
  end)
  define('$sprite_move_wait',function(vm,args)
    assert(#args==1);local s=sprite(vm,args[1]);update(s)
    if s.move then vm.waiting={kind='time',until_ms=s.move.began+s.move.duration} end
    return 0
  end)
  define('$sprite_alfa_set',function(vm,args)
    assert(#args==2 or #args==3,'sprite_alfa_set signature')
    local s=sprite(vm,args[1]);update(s)
    assert(args[2]>=0 and args[2]<=255,'sprite alpha range')
    s.fade={from=s.anti,to=args[2],duration=args[3] or 0,began=options.now()}
    s.alpha_sequence=nil
    update(s);state.dirty=true;return 0
  end)
  define('$sprite_alfa_define',function(vm,args)
    assert(#args>=2)
    local s=sprite(vm,args[1]);update(s);s.fade=nil
    local points={}
    for i=2,#args,2 do points[#points+1]={value=args[i],duration=args[i+1] or -1}end
    s.alpha_sequence={from=s.anti,points=points,began=options.now()}
    update(s);state.dirty=true;return 0
  end)
  define('$sprite_alfa_define_abs',function(vm,args)
    assert(#args>=2)
    providers['$sprite_alfa_define'](vm,args)
    local s=sprite(vm,args[1]);local duration=0
    for _,point in ipairs(s.alpha_sequence and s.alpha_sequence.points or{})do
      if point.duration>0 then duration=duration+point.duration end
    end
    duration=math.max(1,duration)
    if s.alpha_sequence then s.alpha_sequence.began=math.floor(options.now()/duration)*duration end
    update(s);return 0
  end)
  define('$sprite_tl_set',function(vm,args)
    assert(#args==2)
    local s=sprite(vm,args[1]);s.fade=nil;s.alpha_sequence=nil
    s.anti=args[2];state.dirty=true;return 0
  end)
  define('$sprite_set_smooth_animation',function(vm,args)
    assert(#args==1)
    local s=sprite(vm,args[1])
    if s.smooth_page then return 0 end
    s.animation_source=s.page
    local original=state.pages[s.page]
    s.smooth_page=providers[original.antidata and'$page_create_withalfa'or'$page_create'](
      vm,{s.width,s.height})
    if original.antidata then
      graphics(vm,string.format('fill %d 0 0 %d %d 255',
        state.pages[s.smooth_page].antidata,s.width,s.height))
    end
    s.smooth_updated=nil;update(s);state.dirty=true;return 0
  end)
  define('$sprite_alfa_wait',function(vm,args)
    assert(#args==1);local s=sprite(vm,args[1]);update(s)
    if s.fade then vm.waiting={kind='time',until_ms=s.fade.began+s.fade.duration} end
    if s.alpha_sequence then
      local duration=0
      for _,point in ipairs(s.alpha_sequence.points)do
        if point.duration<0 then break end
        duration=duration+point.duration
      end
      local until_ms=s.alpha_sequence.began+duration
      if options.now()<until_ms then vm.waiting={kind='time',until_ms=until_ms}end
    end
    return 0
  end)
  local function transform(vm,args,key)
    assert(#args==2 or #args==3)
    local s=sprite(vm,args[1]);update(s)
    s.extended=true
    s.tweens[key]={from=s[key],to=args[2],duration=args[3] or 0,began=options.now()}
    update(s);state.dirty=true;return 0
  end
  define('$sprite_xmodify_set',function(vm,args)return transform(vm,args,'xscale')end)
  define('$sprite_ymodify_set',function(vm,args)return transform(vm,args,'yscale')end)
  for _,entry in ipairs{{'x','xscale'},{'y','yscale'}}do
    local axis,field=entry[1],entry[2]
    register('$sprite_'..axis..'modify_get%',function(vm,args)
      assert(#args==1)
      local value=state.sprites[args[1]]
      if not value then
        state.expired_sprite_calls=state.expired_sprite_calls or{}
        local name='$sprite_'..axis..'modify_get%'
        state.expired_sprite_calls[name]=(state.expired_sprite_calls[name]or 0)+1
        return 1
      end
      update(value);return value[field]
    end)
  end
  define('$sprite_rotate',function(vm,args)return transform(vm,args,'rotate')end)
  local function transform_sequence(vm,args,key)
    assert(#args>=2)
    local s=sprite(vm,args[1]);update(s)
    local points={}
    for i=2,#args,2 do points[#points+1]={value=args[i],duration=args[i+1]or -1}end
    s.transform_sequences=s.transform_sequences or {}
    s.transform_sequences[key]={from=s[key],points=points,began=options.now()}
    s.extended=true;update(s);state.dirty=true;return 0
  end
  define('$sprite_xmodify_define',function(vm,args)return transform_sequence(vm,args,'xscale')end)
  define('$sprite_ymodify_define',function(vm,args)return transform_sequence(vm,args,'yscale')end)
  define('$sprite_is_move',function(vm,args)
    assert(#args==1);local s=sprite(vm,args[1]);update(s);return s.move and 1 or 0
  end)
  define('$sprite_get_pos_x',function(vm,args)
    assert(#args==1);local s=sprite(vm,args[1]);update(s);return s.x
  end)
  define('$sprite_get_pos_y',function(vm,args)
    assert(#args==1);local s=sprite(vm,args[1]);update(s);return s.y
  end)
  define('$sprite_get_page',function(vm,args)
    assert(#args==1);local s=sprite(vm,args[1]);return s.animation_source or s.page
  end)
  define('$sprite_len_x',function(vm,args) assert(#args==1);return sprite(vm,args[1]).width end)
  define('$sprite_len_y',function(vm,args) assert(#args==1);return sprite(vm,args[1]).height end)
  define('$sprite_is_exist',function(vm,args) assert(#args==1);return state.sprites[args[1]] and 1 or 0 end)
  define('$sprite_clip',function(vm,args)
    assert(#args==5);local s=sprite(vm,args[1])
    s.sx,s.sy,s.width,s.height=args[2],args[3],args[4],args[5]
    s.base_sx,s.base_sy=s.sx,s.sy;state.dirty=true;return 0
  end)
  local function animation(vm,args,append,absolute)
    assert(#args>=1)
    local s=sprite(vm,args[1])
    if #args==1 then s.animation=nil;return 0 end
    local frames=append and s.animation and s.animation.frames or {}
    for i=2,#args,2 do
      if #frames>=64 then vm:fail('animation frame limit exceeded') end
      frames[#frames+1]={frame=args[i],duration=args[i+1] or -1}
    end
    local began=append and s.animation and s.animation.began or options.now()
    s.animation={frames=frames,began=began,absolute=absolute};update(s)
    state.dirty=true;return 0
  end
  define('$sprite_animate_add',function(vm,args)return animation(vm,args,true,false)end)
  define('$sprite_animate_define',function(vm,args)return animation(vm,args,false,false)end)
  define('$sprite_animate_define_abs',function(vm,args)return animation(vm,args,false,true)end)
  define('$sprite_animate_wait',function(vm,args)
    assert(#args==1);local s=sprite(vm,args[1]);update(s)
    if s.animation then
      local duration=0
      for _,frame in ipairs(s.animation.frames)do
        if frame.duration<0 then vm:fail('waiting on unbounded animation') end
        duration=duration+frame.duration
      end
      vm.waiting={kind='time',until_ms=s.animation.began+duration}
    end
    return 0
  end)
  define('$sprite_page_auto_release',function(vm,args)
    assert(#args==1);sprite(vm,args[1]).auto_release=true;return 0
  end)
  define('$sprite_release',function(vm,args)
    assert(#args==1);local s=sprite(vm,args[1])
    if s.smooth_page then providers['$page_release'](vm,{s.smooth_page})end
    if s.auto_release then providers['$page_release'](vm,{s.animation_source or s.page}) end
    state.sprites[args[1]]=nil
    for i,id in ipairs(state.sprite_order) do if id==args[1] then table.remove(state.sprite_order,i);break end end
    state.dirty=true;return 0
  end)
  local function priority(vm,args,high,single)
    assert(#args==1 or #args==2)
    local moved=sprite(vm,args[1]);local at
    for i,id in ipairs(state.sprite_order) do if id==args[1] then at=i end end
    table.remove(state.sprite_order,at)
    local target=high and #state.sprite_order+1 or 1
    if args[2] then
      local reference=sprite(vm,args[2])
      local group=reference.priority_group or reference.id
      for i,id in ipairs(state.sprite_order) do
        if id==args[2] then
          local edge=i
          if not single then
            local direction=high and 1 or -1
            while state.sprite_order[edge+direction] do
              local adjacent=state.sprites[state.sprite_order[edge+direction]]
              if (adjacent.priority_group or adjacent.id)~=group then break end
              edge=edge+direction
            end
          end
          target=high and edge+1 or edge
          break
        end
      end
      moved.priority_group=group
    end
    table.insert(state.sprite_order,target,args[1]);state.dirty=true;return 0
  end
  define('$sprite_priority_high',function(vm,args)return priority(vm,args,true,false)end)
  define('$sprite_priority_low',function(vm,args)return priority(vm,args,false,false)end)
  define('$sprite_priority_high_single',function(vm,args)return priority(vm,args,true,true)end)
  define('$sprite_priority_low_single',function(vm,args)return priority(vm,args,false,true)end)
  define('$sprite_paste',function(vm,args)
    assert(#args==2 or #args==4,'sprite_paste signature')
    local s=sprite(vm,args[1]);update(s)
    if (s.hidden or 0)~=0 or s.anti>=255 then return 0 end
    if s.extended then
      -- Like the original 0x4726F0/0x47271C, transformed sprites are
      -- positioned by their center, including when pasted into a page.
      graphics(vm,string.format('paste %d %d %d %d %d %d %.8g %.8g %.8g %.8g %.8g %d 1',
        args[2],s.page,s.sx,s.sy,s.width,s.height,args[3] or s.x,args[4] or s.y,
        s.xscale,s.yscale,s.rotate,s.anti))
      return 0
    end
    graphics(vm,string.format('paste %d %d %d %d %d %d %.8g %.8g 1 1 0 %d 0',
      args[2],s.page,s.sx,s.sy,s.width,s.height,args[3]or s.x,args[4]or s.y,s.anti))
    return 0
  end)
  define('$sprite_push',function(vm,args)
    assert(#args<=1)
    if #args==0 and state.before_hide then state.before_hide(vm)end
    if #args==1 then
      local s=sprite(vm,args[1]);s.hidden=(s.hidden or 0)-1
    else for _,s in pairs(state.sprites)do s.hidden=(s.hidden or 0)-1 end end
    state.dirty=true;return 0
  end)
  define('$sprite_pop',function(vm,args)
    assert(#args<=1)
    if #args==1 then
      local s=sprite(vm,args[1]);s.hidden=math.min(0,(s.hidden or 0)+1)
    else for _,s in pairs(state.sprites)do s.hidden=math.min(0,(s.hidden or 0)+1)end end
    if #args==0 and state.after_show then state.after_show(vm)end
    state.dirty=true;return 0
  end)
  return {reset=function()
    -- Original 0x470070 runs before HOT_RESET: retained sprites move off
    -- screen and their pending animations stop before script handles reset.
    for _,s in pairs(state.sprites)do
      s.x,s.y=100000,100000
      s.move=nil;s.fade=nil;s.animation=nil;s.alpha_sequence=nil
      s.transform_sequences=nil;s.tweens={};s.hidden=0
    end
    state.dirty=true
  end,update=function()
    for _,s in pairs(state.sprites) do update(s) end
  end,
  capture=function(vm,destination,source,exclude_script)
    -- Majiro's frontbuffer contains the composed scene, including sprites.
    -- Our retained page is only its base; materialize the visible layers
    -- before a source script reads the screen into a menu/transition page.
    for _,id in ipairs(state.sprite_order)do
      local s=state.sprites[id]
      if s and s.destination==source and (s.hidden or 0)==0 and
         (not exclude_script or s.source_script~=exclude_script)then
        providers['$sprite_paste'](vm,{id,destination})
      end
    end
  end}
end
return M
