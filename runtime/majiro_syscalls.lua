-- Explicit syscall providers. Missing behavior is never treated as successful.
local M={}
function M.new(options)
  local state={files={},next_file=1,events={},settings={},calls={},names=options.names,
    pages={},next_page=1,invalidate_depth=0,invalidate=true,present_requested=0,present_completed=0,
    graphics_journal={},preference_bindings={}}
  -- Majiro caches a virtual timer per tick. time_accera scales that
  -- timer for ending credits; audio transport continues on wall time.
  local wall_now=options.now
  options.wall_now=wall_now
  state.time_control={scale=1,anchor=0,offset=0}
  options.now=function()
    local timer=state.time_control
    local raw=wall_now()
    if timer.scale==1 then return raw+timer.offset end
    local elapsed=raw-timer.anchor
    local scaled=elapsed*timer.scale
    return timer.anchor+timer.offset+(scaled<0 and math.ceil(scaled)or math.floor(scaled))
  end
  local function wall_deadline(value)
    local timer=state.time_control
    if timer.scale==1 then return value-timer.offset end
    if timer.scale<=0 then return nil end
    return timer.anchor+math.ceil((value-timer.anchor-timer.offset)/timer.scale)
  end
  local raw_graphics=options.graphics
  local pixel_mutations={create=true,load=true,load8=true,alpha=true,antidata=true,copy=true,
    extcopy=true,swap=true,fill=true,extfill=true,mulfill=true,set=true,sepia=true,alphatable=true,
    text=true,glyphs=true,paste=true,modify=true,thumbnail=true,release=true,drawmode=true,
    reverse=true,revmulcopy=true,mulcopy=true,overlayfill=true,mosaic=true,
    thumbrect=true,fuchidori=true,tablecopy=true,smoothframe=true}
  if raw_graphics then options.graphics=function(request)
    local reply=raw_graphics(request)
    if not state.restoring and type(reply)=='string'and reply:sub(1,2)=='OK'and
       pixel_mutations[request:match('^(%w+)')]then
      state.graphics_journal[#state.graphics_journal+1]=request
    end
    return reply
  end end
  local providers={}
  local sprites,strings
  local function release_screen_snapshot(vm)
    if state.screen_snapshot then
      options.graphics('release '..state.screen_snapshot)
      state.pages[state.screen_snapshot]=nil;state.screen_snapshot=nil
    end
  end
  local function define(name,fn) providers[name]=fn end
  define('$time_accera',function(vm,args)
    assert(#args==1 and type(args[1])=='number'and args[1]==args[1])
    local timer=state.time_control
    local cached=options.now()
    local raw=wall_now()
    if args[1]==1 then timer.anchor=0;timer.offset=cached-raw
    else timer.anchor=raw~=0 and raw or 1 end
    timer.scale=args[1];return 0
  end)
  define('$rand',function(vm,args)
    assert(#args==1 and type(args[1])=='number'and args[1]%1==0)
    -- Original 0x42FF60 uses MSVCRT rand() % divisor. Keep its stream in
    -- the snapshot rather than relying on process-global Lua random state.
    if args[1]==0 then return 0 end
    state.random_seed=((state.random_seed or 1)*214013+2531011)%4294967296
    return math.floor(state.random_seed/65536)%32768%math.abs(args[1])
  end)
  define('$rand!',function(vm,args)
    assert(#args==0,'rand! takes no arguments')
    local value
    repeat
      state.random_seed=((state.random_seed or 1)*214013+2531011)%4294967296
      value=math.floor(state.random_seed/65536)%32768
    until value~=32767
    return vm:float(value==0 and math.huge or 32767/value)
  end)
  local function register_hook(vm,args,after)
    assert(#args==2 and type(args[1])=='string' and type(args[2])=='number','event hook signature')
    local hash=args[2]%4294967296
    local fn=vm:resolve(hash)
    if not fn then vm:fail('event function missing')end
    local hook={hash=hash,after=after,script=fn.script.name,offset=fn.offset,owner=vm.thread_id}
    local list=state.events[args[1]] or {};state.events[args[1]]=list
    if after then list[#list+1]=hook else table.insert(list,1,hook) end
    return 0
  end
  local function emit(vm,name,args,done)
    local calls={}
    local groups={}
    if type(name)=='number'then
      for key,hooks in pairs(state.events)do
        if strings.hash(key)==name then groups[#groups+1]=hooks end
      end
    else groups[1]=state.events[name]or{}end
    for _,hooks in ipairs(groups)do
      for _,hook in ipairs(hooks)do
        calls[#calls+1]={script=hook.script,offset=hook.offset,args=args}
      end
    end
    vm:resume{kind='sequence',calls=calls,done=done}
  end
  local function dispatch_event(vm,name,args,target,wants)
    -- Reserve the native zero result in the sender before pushing callback
    -- frames. Ordinary events push head-to-tail on their registered owners;
    -- explicit-target events push tail-to-head on the requested context.
    if wants then vm:push(0)end
    local hooks=state.events[name]or{}
    local function inject(hook,owner)
      vm:inject_event(owner,{{script=hook.script,offset=hook.offset,args=args}})
    end
    if target then
      for i=#hooks,1,-1 do inject(hooks[i],target)end
    else
      for _,hook in ipairs(hooks)do inject(hook,hook.owner or 1)end
    end
    return nil,true
  end
  local function graphics(vm,request)
    if not options.graphics then vm:fail('page pixel provider missing') end
    local result=options.graphics(request)
    if type(result)~='string' or result:sub(1,2)~='OK' then
      vm:fail('page operation failed: '..tostring(result))
    end
    if not request:match('^pixel ') and not request:match('^stats') and not request:match('^png ') then
      state.dirty=true
    end
    return result
  end
  define('$load_library',function(vm,args,wants)
    assert(#args==1,'load_library signature')
    vm:load_library(args[1],wants); return nil,true
  end)
  define('$pic_key_set',function(vm,args)
    assert(#args==1 and type(args[1])=='string','pic_key_set signature')
    state.picture_key=args[1];return 0
  end)
  for _,entry in ipairs{{'$regist_arcfile','register'},{'$release_arcfile','release'}}do
    local name,action=entry[1],entry[2]
    define(name,function(vm,args)
      assert(#args==1 and type(args[1])=='string','archive control signature')
      if not options.archive then vm:fail('dynamic archive host provider unavailable')end
      local result=options.archive(action,args[1])
      assert(result==0,'dynamic archive operation failed')
      return 0
    end)
  end
  define('$set_font_cache',function(vm,args)
    assert(#args==3 and type(args[1])=='string' and args[2]>0 and args[3]>0,
      'set_font_cache signature')
    -- Retain the requested cache identity; actual glyph caching is owned by
    -- Artemis when this font is rendered. It has no Majiro-visible handle.
    state.font_cache={face=args[1],height=args[2],width=args[3]}
    return 0
  end)
  define('$event_hook',function(vm,args)
    return register_hook(vm,args,false)
  end)
  define('$event_hook_after',function(vm,args)
    return register_hook(vm,args,true)
  end)
  define('$event_release',function(vm,args)
    assert(#args==2 and type(args[1])=='string'and type(args[2])=='number','event release signature')
    local fn=vm:resolve(args[2]%4294967296)
    if not fn then vm:fail('event function missing')end
    local hooks=state.events[args[1]]
    if hooks and not state.event_release_locked then
      for index=#hooks,1,-1 do
        local hook=hooks[index]
        if(hook.owner or 1)==vm.thread_id and not hook.protected and
          hook.script==fn.script.name and hook.offset==fn.offset then table.remove(hooks,index)end
      end
      if #hooks==0 then state.events[args[1]]=nil end
    end
    return 0
  end)
  define('$do_event',function(vm,args,wants)
    assert(#args>=1 and type(args[1])=='string','do_event signature')
    local parameters=vm:argument_slice(args,2)
    return dispatch_event(vm,args[1],parameters,nil,wants)
  end)
  define('$do_event_single',function(vm,args,wants)
    assert(#args>=2 and type(args[1])=='number' and type(args[2])=='string')
    local target=args[1]==-1 and vm.thread_id or args[1]
    local parameters=vm:argument_slice(args,3)
    if target~=vm.thread_id then
      dispatch_event(vm,args[2],parameters,target,false)
      return 0
    end
    return dispatch_event(vm,args[2],parameters,target,wants)
  end)
  define('$file_open',function(vm,args)
    assert(#args==1,'file_open signature')
    local tokens=options.config_files[args[1]]
    if not tokens and options.file_read then
      local text=options.file_read(args[1])
      if text then
        local parse=options.config_parse or options.modules('runtime/player_configuration.lua').tokens
        local lines
        tokens,lines=parse(text)
        options.config_files[args[1]]=tokens;options.config_lines[args[1]]=lines
      end
    end
    if not tokens then vm:fail('configuration file not indexed: '..tostring(args[1])) end
    local handle=state.next_file;state.next_file=handle+1
    -- CONFIG stores user preferences after the third "check" marker.
    -- Earlier tokens describe immutable layout and must never be overridden.
    local preferences=args[1]=='config.cfg'
    state.files[handle]={tokens=tokens,cursor=1,name=args[1],preferences=preferences}
    return handle
  end)
  local function readfile(vm,args,numeric)
    assert(#args==1,'file_read signature')
    local file=state.files[args[1]]
    if not file then vm:fail('invalid file handle') end
    local at=file.cursor
    local value=file.tokens[at]
    if file.preferences and value=='check'then
      file.preference_checks=(file.preference_checks or 0)+1
    end
    if file.preferences and(file.preference_checks or 0)>=3 then
      local frame=vm:frame()
      local row=frame.script.code[frame.index]
      local opcode=row and vm.opcodes[row[2]]
      if opcode and opcode[1]:match('^stp?%.')and
         math.floor(row[3]/32)%8==0 then
        state.preference_bindings[at]={hash=row[4],numeric=numeric}
        if file.apply_preferences==nil then
          file.apply_preferences=not state.preference_loaded
          state.preference_loaded=true
        end
        local saved
        if file.apply_preferences and options.preference_tokens then saved=options.preference_tokens[at]end
        if saved~=nil then value=tostring(saved)end
      end
    end
    if value==nil then value='' end
    file.cursor=file.cursor+1
    if numeric then
      -- Original $file_read passes the token to decimal atoi. Markers and
      -- actual EOF therefore convert to zero, rather than raising an error.
      local prefix=value:match('^%s*([%+%-]?%d+)')
      local number=prefix and tonumber(prefix) or 0
      return ((number+2147483648)%4294967296)-2147483648
    end
    return value
  end
  define('$file_read',function(vm,args) return readfile(vm,args,true) end)
  define('$file_read$',function(vm,args) return readfile(vm,args,false) end)
  define('$file_read_line$',function(vm,args)
    assert(#args==1)
    local file=state.files[args[1]]
    if not file then vm:fail('invalid file handle') end
    local remainder=options.config_lines[file.name][file.cursor]
    if not remainder then return '' end
    file.cursor=remainder.next
    local value=remainder.text
    if value:sub(1,1)=='"' and value:sub(-1)=='"' then value=value:sub(2,-2) end
    return value
  end)
  define('$file_read_line_raw$',function(vm,args)
    assert(#args==1)
    local file=assert(state.files[args[1]],'invalid raw line handle')
    local lines=assert(options.config_lines[file.name],'raw file content missing')
    while true do
      local row=lines[file.cursor]
      if not row then return''end
      file.cursor=row.next
      local value=row.text:match('^[^;]*'):gsub('//.*$',''):match('^[%z\1-\32]*(.-)[%z\1-\32]*$')
      if value~=''then return value end
    end
  end)
  define('$file_write_open',function(vm,args)
    assert(#args==1 and type(args[1])=='string','file_write_open signature')
    if not options.file_write then vm:fail('script file writer unavailable')end
    local handle=state.next_file;state.next_file=handle+1
    options.file_write(args[1],'')
    options.config_files[args[1]]=nil;options.config_lines[args[1]]=nil
    state.files[handle]={name=args[1],writer=true,text=''}
    return handle
  end)
  define('$file_puts',function(vm,args)
    assert(#args==2 and type(args[2])=='string','file_puts signature')
    local file=assert(state.files[args[1]],'invalid output file handle')
    assert(file.writer,'file_puts requires output handle')
    local text=file.text..args[2]:match('^[^%z]*')..'\n'
    assert(#text<=64*1024*1024,'script output file exceeds memory limit')
    options.file_write(file.name,text)
    options.config_files[file.name]=nil;options.config_lines[file.name]=nil
    file.text=text
    return args[1]
  end)
  define('$file_close',function(vm,args)
    assert(#args==1,'file_close signature')
    if not state.files[args[1]] then vm:fail('invalid close handle') end
    state.files[args[1]]=nil;return 0
  end)
  define('$strupr$',function(vm,args)
    assert(#args==1,'strupr signature');return string.upper(args[1])
  end)
  define('$strlwr$',function(vm,args)
    assert(#args==1,'strlwr signature');return string.lower(args[1])
  end)
  define('$dim_create#',function(vm,args)
    if #args<1 or #args>3 then vm:fail('dim_create dimension count') end
    local dims={}
    for i,n in ipairs(args) do
      if type(n)~='number' or n%1~=0 or n<=0 then vm:fail('invalid integer array dimension') end
      dims[i]=n
    end
    return {kind='array',element_type=0,dims=dims,values={}}
  end)
  define('$dim_create$#',function(vm,args)
    if #args<1 or #args>3 then vm:fail('dim_create dimension count')end
    local dims={}
    for i,n in ipairs(args)do
      if type(n)~='number'or n%1~=0 or n<0 then vm:fail('invalid array dimension')end
      dims[i]=n
    end
    return{kind='array',element_type=2,dims=dims,values={}}
  end)
  define('$dim_create%#',function(vm,args)
    local array=providers['$dim_create$#'](vm,args)
    array.element_type=1;return array
  end)
  for _,entry in ipairs{{'$dim_copy#',0},{'$dim_copy%#',1},{'$dim_copy$#',2}}do
    local element=entry[2]
    define(entry[1],function(vm,args)
      assert(#args==1 and type(args[1])=='table'and args[1].kind=='array'
        and args[1].element_type==element,'dim_copy array signature')
      local source=args[1];local result={kind='array',element_type=element,dims={},values={}}
      for i,value in ipairs(source.dims)do result.dims[i]=value end
      for i,value in pairs(source.values)do result.values[i]=value end
      return result
    end)
  end
  for _,entry in ipairs{{'$dim_release#',0},{'$dim_release%#',1},{'$dim_release$#',2}}do
    local element=entry[2]
    define(entry[1],function(vm,args)
      -- 0x42F190..0x42F21C returns a new default array. Assigning that
      -- result releases the previous reference; aliases remain valid.
      assert(#args==0)
      return {kind='array',element_type=element,dims={1},values={}}
    end)
  end
  define('$client_width',function(vm,args)
    assert(#args==0,'client_width signature');return options.width
  end)
  define('$client_height',function(vm,args)
    assert(#args==0,'client_height signature');return options.height
  end)
  define('$min',function(vm,args)
    assert(#args>=2,'min signature')
    if options.minmax_arguments==2 then return math.min(args[1],args[2])end
    return math.min(unpack(args))
  end)
  define('$max',function(vm,args)
    assert(#args>=2,'max signature')
    if options.minmax_arguments==2 then return math.max(args[1],args[2])end
    return math.max(unpack(args))
  end)
  define('$abs',function(vm,args)
    assert(#args==1)
    local value=math.abs(args[1])
    return value==2147483648 and -2147483648 or value
  end)
  for _,operation in ipairs{'sin','cos','tan'}do
    local name=operation
    define('$'..name..'%',function(vm,args)
      assert(#args==1 and type(args[1])=='number')
      -- Original 0x4305B0/0x42E270/0x430D50 takes degrees and rounds
      -- the result to binary32; its stored pi constant is 3.1415926535.
      return vm:float(math[name](vm:float(args[1])*3.1415926535/180))
    end)
  end
  define('$timer',function(vm,args)
    assert(#args==0,'timer signature');return math.floor(options.now())
  end)
  local function create_page(vm,args,alpha)
    assert(#args==1 and type(args[1])=='string','page_create_file signature')
    if not options.asset then vm:fail('image resource provider missing') end
    local asset=options.asset(args[1],alpha)
    if not asset then vm:fail('image resource unavailable: '..args[1]) end
    local id=state.next_page;state.next_page=id+1
    local depth=asset.source_depth or 32
    graphics(vm,string.format('%s %d %d %s',depth==8 and 'load8' or 'load',id,alpha and 1 or 0,asset.file))
    state.pages[id]={asset=asset,width=asset.width,height=asset.height,alpha=alpha,depth=depth}
    if alpha and depth==32 then
      local mask=state.next_page;state.next_page=mask+1
      graphics(vm,string.format('alpha %d %d 1',id,mask))
      state.pages[id].antidata=mask;state.pages[id].owns_alpha=true
      state.pages[mask]={width=asset.width,height=asset.height,depth=8}
    end
    return id
  end
  define('$page_create_file',function(vm,args) return create_page(vm,args,false) end)
  define('$page_create_file_withalfa',function(vm,args) return create_page(vm,args,true) end)
  local function blank_page(vm,args,alpha)
    assert(#args==2 or #args==3,'page_create signature')
    for i=1,2 do
      local n=args[i]
      if type(n)~='number' or n%1~=0 or n<=0 or n>32768 then
        vm:fail('invalid page dimensions')
      end
    end
    if args[1]*args[2]>16*1024*1024 then vm:fail('page allocation budget exceeded') end
    local id=state.next_page;state.next_page=id+1
    local depth=(args[3]==0) and 8 or 32
    graphics(vm,string.format('create %d %d %d 0 %d',id,args[1],args[2],depth))
    state.pages[id]={width=args[1],height=args[2],alpha=alpha,depth=depth,operations={}}
    if alpha then
      if depth~=32 then vm:fail('indexed page with alpha needs palette semantics') end
      local mask=state.next_page;state.next_page=mask+1
      graphics(vm,string.format('alpha %d %d 0',id,mask))
      state.pages[id].antidata=mask;state.pages[id].owns_alpha=true
      state.pages[mask]={width=args[1],height=args[2],depth=8}
    end
    return id
  end
  define('$page_create',function(vm,args) return blank_page(vm,args,false) end)
  define('$page_create_withalfa',function(vm,args) return blank_page(vm,args,true) end)
  define('$page_drawmode',function(vm,args)
    assert(#args==2)
    local page=state.pages[args[1]]
    if not page or page.depth~=32 then vm:fail('drawmode requires a 32-bit page')end
    graphics(vm,string.format('drawmode %d %d',args[1],args[2]))
    page.drawmode=args[2];return 0
  end)
  define('$page_len_x',function(vm,args)
    assert(#args==1,'page_len_x signature')
    local page=state.pages[args[1]]
    if not page then vm:fail('page not found') end
    return page.width
  end)
  define('$page_len_y',function(vm,args)
    assert(#args==1,'page_len_y signature')
    local page=state.pages[args[1]]
    if not page then vm:fail('page not found') end
    return page.height
  end)
  define('$page_is_exist',function(vm,args)
    assert(#args==1,'page_is_exist signature')
    return state.pages[args[1]] and 1 or 0
  end)
  local function copy(vm,args,extended)
    assert(#args>=6 and #args<=(extended and 9 or 8),'grp_copy signature')
    if not state.pages[args[1]] or not state.pages[args[6]] then
      state.expired_copy_calls=(state.expired_copy_calls or 0)+1
      return 0
    end
    local request={extended and 'extcopy' or 'copy'}
    local snapshot
    if args[1]==state.frontbuffer and state.screen_snapshot then
      snapshot=state.screen_snapshot
    elseif args[1]==state.frontbuffer and sprites then
      local page=state.pages[args[1]]
      snapshot=state.next_page;state.next_page=snapshot+1
      graphics(vm,string.format('create %d %d %d 0',snapshot,page.width,page.height))
      graphics(vm,string.format('copy %d 0 0 %d %d %d 0 0',args[1],page.width,page.height,snapshot))
      state.pages[snapshot]={width=page.width,height=page.height,depth=32}
      sprites.capture(vm,snapshot,args[1])
    end
    for index=1,(extended and 9 or 8) do
      local value=index==1 and(snapshot or args[index])or args[index]
      if value==nil then value=(index==7 and args[2]) or (index==8 and args[3]) or 0 end
      if type(value)~='number' or value%1~=0 then vm:fail('nonintegral grp_copy argument') end
      request[#request+1]=string.format('%.0f',value)
    end
    graphics(vm,table.concat(request,' '))
    if snapshot and snapshot~=state.screen_snapshot then
      graphics(vm,'release '..snapshot);state.pages[snapshot]=nil
    end
    return 0
  end
  define('$grp_copy',function(vm,args) return copy(vm,args,false) end)
  define('$grp_extcopy',function(vm,args) return copy(vm,args,true) end)
  define('$grp_reverse',function(vm,args)
    assert(#args==5)
    if not state.pages[args[1]]then return 0 end
    graphics(vm,string.format('reverse %d %d %d %d %d',unpack(args)));return 0
  end)
  define('$grp_revmulcopy',function(vm,args)
    assert(#args>=6 and #args<=8)
    if not state.pages[args[1]]or not state.pages[args[6]]then return 0 end
    graphics(vm,string.format('revmulcopy %d %d %d %d %d %d %d %d',
      args[1],args[2],args[3],args[4],args[5],args[6],args[7]or args[2],args[8]or args[3]))
    return 0
  end)
  define('$grp_mulcopy',function(vm,args)
    assert(#args==8 or((options.graphics_controls or{}).optional_mulcopy_coordinates~=false
      and #args>=6 and #args<=8),'grp_mulcopy signature')
    if not state.pages[args[1]]or not state.pages[args[6]]then return 0 end
    graphics(vm,string.format('mulcopy %d %d %d %d %d %d %d %d',
      args[1],args[2],args[3],args[4],args[5],args[6],args[7]or args[2],args[8]or args[3]))
    return 0
  end)
  define('$grp_overlayboxfill',function(vm,args)
    assert(#args==6,'grp_overlayboxfill signature')
    if not state.pages[args[1]]then return 0 end
    graphics(vm,string.format('overlayfill %d %d %d %d %d %d',unpack(args)));return 0
  end)
  define('$make_mosaic',function(vm,args)
    assert(#args==9,'make_mosaic signature')
    if not state.pages[args[1]]or not state.pages[args[6]]then return 0 end
    -- Mosaic and rand() share Majiro's single-thread CRT stream. The seed is
    -- part of the command, so graphics checkpoint replay is deterministic.
    local request={'mosaic'}
    for _,value in ipairs(args)do
      assert(type(value)=='number'and value%1==0,'make_mosaic requires integers')
      request[#request+1]=string.format('%.0f',value)
    end
    request[#request+1]=string.format('%.0f',state.random_seed or 1)
    local reply=graphics(vm,table.concat(request,' '))
    state.random_seed=assert(tonumber(reply:match('^OK (%d+)$')),'mosaic random result missing')
    return 0
  end)
  define('$grp_swap',function(vm,args)
    assert(#args>=6 and #args<=8)
    if not state.pages[args[1]]or not state.pages[args[6]]then return 0 end
    local values={'swap'}
    for i=1,8 do values[#values+1]=string.format('%.0f',args[i]or(i==7 and args[2]or args[3]))end
    graphics(vm,table.concat(values,' '));return 0
  end)
  define('$grp_modify_copy',function(vm,args)
    assert(#args==13,'grp_modify_copy signature')
    if not state.pages[args[1]]or not state.pages[args[6]]then vm:fail('transform page missing')end
    local request={'modify'}
    for _,value in ipairs(args)do request[#request+1]=string.format('%.9g',value)end
    graphics(vm,table.concat(request,' '));return 0
  end)
  define('$grp_modcopy',function(vm,args)
    assert(#args==10,'grp_modcopy signature')
    if args[4]<=0 or args[5]<=0 or args[9]<=0 or args[10]<=0 then return 0 end
    local transform={}
    for i=1,10 do transform[i]=args[i]end
    transform[11],transform[12],transform[13]=0,args[9]/args[4],args[10]/args[5]
    return providers['$grp_modify_copy'](vm,transform)
  end)
  local function fill(vm,args,extended)
    assert(#args==(extended and 7 or 6),'grp_boxfill signature')
    if not state.pages[args[1]] then vm:fail('fill page missing') end
    local request={extended and 'extfill' or 'fill'}
    for _,value in ipairs(args) do
      if type(value)~='number' or value%1~=0 then vm:fail('nonintegral fill argument') end
      request[#request+1]=string.format('%.0f',value)
    end
    graphics(vm,table.concat(request,' '));return 0
  end
  define('$grp_boxfill',function(vm,args)return fill(vm,args,false)end)
  define('$grp_extboxfill',function(vm,args)return fill(vm,args,true)end)
  define('$grp_mulboxfill',function(vm,args)
    assert(#args==6)
    local request={'mulfill'}
    for _,n in ipairs(args)do request[#request+1]=string.format('%.0f',n)end
    graphics(vm,table.concat(request,' '));return 0
  end)
  define('$grp_point_set',function(vm,args)
    assert(#args==4)
    graphics(vm,string.format('set %d %d %d %d',args[1],args[2],args[3],args[4]))
    return 0
  end)
  define('$grp_point_get',function(vm,args)
    assert(#args==3)
    local reply=graphics(vm,string.format('get %d %d %d',args[1],args[2],args[3]))
    return assert(tonumber(reply:match('^OK ([-%d]+)')),'point query invalid result')
  end)
  define('$grp_sepia',function(vm,args)
    assert(#args==7 or #args==8,'grp_sepia signature')
    local request={'sepia'}
    for _,n in ipairs(args)do request[#request+1]=string.format('%.0f',n)end
    graphics(vm,table.concat(request,' '));return 0
  end)
  define('$make_alfa_table',function(vm,args)
    assert(#args==3 and type(args[3])=='table','alpha lookup signature')
    local array=args[3]
    if array.kind~='array' or array.dims[1]<256 then vm:fail('alpha lookup length')end
    local request={'alphatable',tostring(args[1]),tostring(args[2])}
    for i=0,255 do request[#request+1]=tostring(array.values[i]or 0)end
    graphics(vm,table.concat(request,' '));return 0
  end)
  local function lookup_values(vm,array)
    if type(array)~='table'or array.kind~='array'or array.element_type~=0 then
      vm:fail('pixel lookup requires an integer array')
    end
    local count=1
    for _,value in ipairs(array.dims)do count=count*value end
    if count<256 then vm:fail('pixel lookup requires 256 values')end
    local values={}
    for i=0,255 do values[#values+1]=string.format('%.0f',(array.values[i]or 0)%4294967296)end
    return table.concat(values,' ')
  end
  define('$fuchidori_copy',function(vm,args)
    assert(#args==6,'fuchidori_copy signature')
    for i=1,3 do if not state.pages[args[i]]then return 0 end end
    graphics(vm,string.format('fuchidori %d %d %d %d %d ',unpack(args,1,5))..
      lookup_values(vm,args[6]));return 0
  end)
  define('$grp_alfa_tabled_copy',function(vm,args)
    assert(#args==10,'grp_alfa_tabled_copy signature')
    for _,i in ipairs{1,6,9}do if not state.pages[args[i]]then return 0 end end
    local values={'tablecopy'}
    for i=1,9 do values[#values+1]=string.format('%.0f',args[i])end
    values[#values+1]=lookup_values(vm,args[10])
    graphics(vm,table.concat(values,' '));return 0
  end)
  define('$make_thumbnail',function(vm,args)
    assert(#args==10,'make_thumbnail signature')
    if not state.pages[args[1]]or not state.pages[args[6]]then return 0 end
    local values={'thumbrect'}
    for _,value in ipairs(args)do values[#values+1]=string.format('%.0f',value)end
    graphics(vm,table.concat(values,' '));return 0
  end)
  define('$write_pic_cache',function(vm,args)
    assert(#args==2 and type(args[2])=='string','write_pic_cache signature')
    local page=state.pages[args[1]]
    if not page or page.depth~=32 then return 0 end
    if not options.file_write then vm:fail('picture cache storage provider missing')end
    local reply=graphics(vm,'cachebmp '..args[1])
    local hex=assert(reply:match('^OK data ([0-9a-f]+)$'),'picture cache encoder failed')
    local bytes=hex:gsub('..',function(value)return string.char(tonumber(value,16))end)
    local folder=(options.environment or{}).CACHEFOLDER or''
    local name=(folder~=''and(folder..'/')or'')..args[2]
    local result=options.file_write(name,bytes)
    if result==false then vm:fail('picture cache storage write failed')end
    return 0
  end)
  define('$change_cursor',function(vm,args)
    assert(#args==1 and type(args[1])=='string','change_cursor signature')
    if not options.change_cursor then vm:fail('cursor host provider missing')end
    options.change_cursor(args[1]);return 0
  end)
  define('$set_wallpaper',function(vm,args)
    assert(#args==2 and type(args[1])=='string'and type(args[2])=='number','set_wallpaper signature')
    if not options.wallpaper then vm:fail('wallpaper host provider missing')end
    return options.wallpaper(args[1],args[2])
  end)
  define('$page_release',function(vm,args)
    assert(#args==1,'page_release signature')
    -- Original 0x44B510 diagnoses an invalid page and returns zero.
    -- Shipped menu cleanup may release its unused (zero) capture page.
    if not state.pages[args[1]] then return 0 end
    local page=state.pages[args[1]]
    graphics(vm,string.format('release %d',args[1]))
    if page.owns_alpha then state.pages[page.antidata]=nil end
    state.pages[args[1]]=nil;return 0
  end)
  define('$page_set_antidata',function(vm,args)
    assert(#args==2,'page_set_antidata signature')
    local page,mask=state.pages[args[1]],state.pages[args[2]]
    if not page or (args[2]~=0 and not mask) then vm:fail('alpha page handle missing') end
    graphics(vm,string.format('antidata %d %d',args[1],args[2]))
    if page.owns_alpha then state.pages[page.antidata]=nil end
    page.antidata=args[2];page.owns_alpha=false;return 0
  end)
  define('$get_alfapage',function(vm,args)
    assert(#args==1,'get_alfapage signature')
    local page=state.pages[args[1]]
    if not page then vm:fail('alpha owner missing') end
    return page.antidata or 0
  end)
  local image_key=options.modules('runtime/majiro_strings.lua').resource_key
  local function picture(name)
    local key=image_key(name)
    local meta=options.image_metadata
    return meta[key] or meta[key..'.rct'] or meta[key..'.rc8']
  end
  define('$pic_len_x',function(vm,args)
    assert(#args==1);local meta=picture(args[1])
    if not meta then vm:fail('picture dimensions missing: '..args[1]) end
    return meta.width
  end)
  define('$pic_len_y',function(vm,args)
    assert(#args==1);local meta=picture(args[1])
    if not meta then vm:fail('picture dimensions missing: '..args[1]) end
    return meta.height
  end)
  define('$pic_is_exist',function(vm,args)
    assert(#args==1)
    local found=picture(args[1])
    if not found then
      state.missing_picture_queries=state.missing_picture_queries or{}
      local log=state.missing_picture_queries
      if #log<1000 then
        local frame=vm:frame()
        log[#log+1]={name=args[1],script=frame and frame.script.name,offset=vm.current_offset}
      end
    end
    return found and 1 or 0
  end)
  define('$pic_unpack',function(vm,args)
    assert(#args==2 or #args==4,'pic_unpack signature')
    if not state.pages[args[1]] then vm:fail('picture destination missing') end
    -- Original 0x44BA32 attaches filename_.rc8 and uses the masked blitter.
    -- UI overlays contain black RGB outside their mask; raw copying erases
    -- the background already present on the destination page.
    local temporary=create_page(vm,{args[2]},true)
    local page=state.pages[temporary]
    providers['$grp_extcopy'](vm,{temporary,0,0,page.width,page.height,args[1],args[3] or 0,args[4] or 0,0})
    providers['$page_release'](vm,{temporary})
    return 0
  end)
  define('$rgb',function(vm,args)
    assert(#args==3,'rgb signature')
    local result=0
    for i,n in ipairs(args)do result=result+math.max(0,math.min(255,math.floor(n)))*256^(i-1)end
    return result
  end)
  define('$rgba',function(vm,args)
    assert(#args==4,'rgba signature')
    local result=0
    for i,n in ipairs(args)do result=result+math.max(0,math.min(255,math.floor(n)))*256^(i-1)end
    return result>=2147483648 and result-4294967296 or result
  end)
  sprites=options.modules('runtime/majiro_sprites.lua').install(define,state,options,graphics,providers)
  state.before_hide=function(vm)
    if not state.frontbuffer then return end
    release_screen_snapshot(vm)
    local page=state.pages[state.frontbuffer]
    local id=state.next_page;state.next_page=id+1
    graphics(vm,string.format('create %d %d %d 0',id,page.width,page.height))
    graphics(vm,string.format('copy %d 0 0 %d %d %d 0 0',state.frontbuffer,page.width,page.height,id))
    state.pages[id]={width=page.width,height=page.height,depth=32}
    local frame=vm:frame()
    local target=frame and frame.script.name
    -- The pause menu leaves its exit animations running on other threads.
    -- Keep those transient buttons out of the next modal's frozen backdrop.
    local leaving_menu=state.active_ui_script=='menu.mjo'and target~='menu.mjo'
    sprites.capture(vm,id,state.frontbuffer,leaving_menu and'menu.mjo'or nil)
    state.screen_snapshot=id
  end
  state.after_show=release_screen_snapshot
  local console,console_actions=options.modules('runtime/majiro_console.lua').install(define,state,options,emit)
  options.modules('runtime/majiro_settings.lua').install(define,state,options)
  state.media_providers=providers
  options.modules('runtime/majiro_media.lua').install(define,state,options)
  options.modules('runtime/majiro_fontout.lua').install(define,state,options,graphics)
  strings=options.modules('runtime/majiro_strings.lua').install(define,state,options)
  options.fast_mode=function(vm)return providers['$is_fast_mode'](vm,{})~=0 end
  local screen=options.modules('runtime/majiro_screen.lua').install(define,state,options)
  local input=options.modules('runtime/majiro_input.lua').install(define,state,options,screen)
  options.modules('runtime/majiro_capture.lua').install(define,state,options)
  options.modules('runtime/majiro_platform.lua').install(define,state,options,screen)
  local text=options.modules('runtime/majiro_text.lua').install(state,options,emit,providers)
  local saves=options.modules('runtime/majiro_save.lua').install(define,state,options,providers,graphics)
  local dialogs=options.modules('runtime/majiro_dialogs.lua').install(define,state,options,saves)
  define('$call',function(vm,args,wants)
    -- Original 0x438620 preserves the caller frame and enters the named
    -- script at its main offset. Unlike exec, the script returns here.
    assert(#args>=1 and type(args[1])=='string')
    local script=vm:load(args[1])
    local parameters=vm:argument_slice(args,2)
    state.ui_waiting=false
    vm:enter(script,script.main,parameters,wants)
    return nil,true
  end)
  define('$call_sub_str',function(vm,args,wants)
    -- Original 0x4387A0 hashes the function string as CP932, loads the
    -- named file, then resolves that function in the target file first.
    assert(#args>=2 and type(args[1])=='string'and type(args[2])=='string')
    local script=vm:load(args[1]);local hash=strings.hash(args[2]);local offset
    for _,fn in ipairs(script.functions)do if fn[1]==hash then offset=fn[2];break end end
    if not offset then
      local fn=vm.functions[hash]
      if fn then script,offset=fn.script,fn.offset end
    end
    if not offset then vm:fail('named script function missing: '..args[1]..' '..args[2])end
    local parameters=vm:argument_slice(args,3)
    state.ui_waiting=false
    vm:enter(script,offset,parameters,wants)
    return nil,true
  end)
  define('$call_sub',function(vm,args,wants)
    assert(#args>=2 and type(args[1])=='string'and type(args[2])=='number'
      and args[2]%1==0,'call_sub signature')
    local script=vm:load(args[1]);local hash=args[2]%4294967296;local offset
    for _,fn in ipairs(script.functions)do if fn[1]==hash then offset=fn[2];break end end
    if not offset then
      local fn=vm.functions[hash]
      if fn then script,offset=fn.script,fn.offset end
    end
    if not offset then vm:fail('script function missing: '..args[1])end
    local parameters=vm:argument_slice(args,3)
    state.ui_waiting=false
    vm:enter(script,offset,parameters,wants)
    return nil,true
  end)
  define('$system_end',function(vm,args)
    -- Dispatcher 0x44063A persists engine state, tears down, then exits.
    assert(#args==0)
    state.persist_dirty=true;saves.flush(vm)
    for _,spec in pairs(state.media.channels)do options.audio('stop',spec)end
    state.media.channels={};state.shutdown_requested=true;vm.status='finished'
    return 0
  end)
  define('$exec',function(vm,args)
    assert(#args>=1 and type(args[1])=='string')
    local script=vm:load(args[1])
    local parameters=vm:argument_slice(args,2)
    vm:exec(script,parameters)
    state.ui_waiting=false;state.reading_wait=false;state.reading_context=nil
    return nil,true
  end)
  define('$exec_func',function(vm,args)
    assert(#args>=1 and type(args[1])=='number'and args[1]%1==0,'exec_func signature')
    vm:exec_function(args[1],vm:argument_slice(args,2))
    state.ui_waiting=false;state.reading_wait=false;state.reading_context=nil
    return nil,true
  end)
  define('$reset_exec',function(vm,args)
    assert(#args==1 and type(args[1])=='string','reset_exec signature')
    local script=vm:load(args[1])
    assert(script.positions[script.main],'reset_exec target has no main entry')
    vm.entry_script=script.name
    vm:exit_context(4)
    state.ui_waiting=false;state.reading_wait=false;state.reading_context=nil
    return 0
  end)
  define('$thread_atend',function(vm,args)
    assert(#args==1 and type(args[1])=='number'and args[1]%1==0,'thread_atend signature')
    local target=vm:resolve(args[1])
    if not target then vm:fail('thread_atend function missing')end
    vm.threads[vm.thread_id].at_end={script=target.script.name,offset=target.offset}
    return 0
  end)
  define(0x0542e1b7,function(vm,args)
    assert(#args==0)
    state.console.skip_initial_newline=true
    return 0
  end)
  define(0x5131e234,function(vm,args)
    assert(#args==0)
    local c=state.console
    return((c.x-(c.configuration and c.configuration[2]or 0)+2147483648)%4294967296)-2147483648
  end)
  define(0xcc3e0342,function(vm,args)
    assert(#args==0)
    local c=state.console
    return((c.y-(c.configuration and c.configuration[3]or 0)+2147483648)%4294967296)-2147483648
  end)
  define(0xc46925cf,function(vm,args)
    assert(#args==0)
    -- The source activates its own window and drains queued window events.
    -- Yield at the host service boundary instead of recursively pumping JNI.
    if not options.platform then vm:fail('platform host provider missing: service_events')end
    local result=options.platform('service_events',{})
    if result==nil or result==false then vm:fail('platform event service failed')end
    vm.waiting={kind='yield'};return 0
  end)
  define(0x7547c477,function(vm,args)
    assert(#args==1 and type(args[1])=='string')
    state.execution_label=strings.uppercase(args[1]);return 0
  end)
  define(0x3dbd5408,function(vm,args)
    assert(#args==1 and type(args[1])=='string')
    local script=vm:load(args[1])
    assert(script.positions[script.main],'reset_exec target has no main entry')
    for name,spec in pairs(state.media.channels)do
      if name:sub(1,5)=='voice'then options.audio('stop',spec);state.media.channels[name]=nil end
    end
    state.voice_for_history=nil;state.text_buffer=''
    vm.entry_script=script.name;vm:exit_context(6)
    state.ui_waiting=false;state.reading_wait=false;state.reading_context=nil
    return 0
  end)
  define(0x321717d1,function(vm,args)
    assert(#args==1 and type(args[1])=='string')
    local bytes=options.cp932 and options.cp932(args[1])or args[1]
    assert(#bytes<=31,'voice selection label exceeds native field')
    state.voice_selection_labels=state.voice_selection_labels or{}
    assert(#state.voice_selection_labels<16,'voice selection table capacity exceeded')
    state.voice_selection_labels[#state.voice_selection_labels+1]=args[1]
    return 0
  end)
  define(0x2cd009af,function(vm,args)
    assert(#args<=3,'float array dimension count')
    local dims={}
    if #args==0 then dims={0,0,0}
    else
      for i,n in ipairs(args)do
        assert(type(n)=='number'and n%1==0 and n>=0,'invalid float array dimension')
        dims[i]=n
      end
    end
    return{kind='array',element_type=1,dims=dims,values={}}
  end)
  define(0x7d8b2c41,function(vm,args)
    assert(#args==1 and type(args[1])=='number'and args[1]%1==0,'font quality signature')
    local value=((args[1]+2147483648)%4294967296)-2147483648
    if not options.service then vm:fail('font quality host provider missing')end
    assert(options.service('font_quality',tostring(value))=='OK','font quality request failed')
    state.settings.font_quality=value;return 0
  end)
  define(0xdca7da63,function(vm,args)
    assert(#args<=1 and(args[1]==nil or type(args[1])=='string'),'source media check signature')
    -- Original builds search removable drives for the running executable.
    -- The portable host has already mounted the selected game directory:
    -- validate that mounted source instead of probing unrelated device paths.
    local names=assert(options.resource_names,'game source catalogue unavailable')
    if not names['start.mjo']then vm:fail('selected game source is unavailable: start.mjo missing')end
    state.source_media_check={requested_subdirectory=args[1]or'',mounted=true}
    return 0
  end)
  define(0x42f6fddf,function(vm,args)
    assert(#args==0)
    local now=(options.wall_now or options.now)()
    local elapsed=now-(state.last_poll_wall or now)
    elapsed=((elapsed+2147483648)%4294967296)-2147483648
    elapsed=math.max(0,math.min(1000,elapsed))
    state.time_control.offset=state.time_control.offset-elapsed
    return 0
  end)
  define(0x8cfc4573,function(vm,args)
    assert(#args==0);return state.console.page_message or''
  end)
  define('$exit',function(vm,args,wants)
    assert(#args<=1)
    if args[1]and args[1]~=0 then
      -- 0x44199E requests a voice/text stop even when negative suppresses
      -- the script-context exit.
      for name,spec in pairs(state.media.channels)do
        if name:sub(1,5)=='voice'then options.audio('stop',spec);state.media.channels[name]=nil end
      end
    end
    if not args[1]or args[1]>=0 then
      vm:exit_context();state.reading_wait=false;state.reading_context=nil
      state.ui_waiting=false;state.ui_areas={}
    end
    return 0
  end)
  state.progress=options.progress or{pictures={},read={}}
  state.progress.lines=state.progress.lines or{}
  local function line_changed(vm,frame)
    local t=vm.threads[vm.thread_id]
    if t and t.parent then return end
    local script=frame.script
    local count=script.line_count or 0
    if count==0 then
      if #vm.frames<=1 then state.current_line_read=false end
      return
    end
    local key=(script.source_sha256 or script.name)..':'..frame.line
    state.current_line_read=state.progress.lines[key]or false
    state.progress.lines[key]=true;state.persist_dirty=true
  end
  define(0xf6416e87,function(vm,args)assert(#args==0);return state.current_line_read and 1 or 0 end)
  state.progress.scripts=state.progress.scripts or{}
  local function script_key(name)
    local base=name:gsub('\\','/'):match('[^/]+$')or''
    return base:gsub('%.[^%.]*$',''):lower()..'.mjo'
  end
  for position in pairs(state.progress.read)do
    local name=position:match('^(.-):%-?%d+$')
    if name then state.progress.scripts[script_key(name)]=true end
  end
  define('$is_read',function(vm,args)
    -- 0x441AF0 checks the read-record list, created at script load by
    -- 0x440EB4 -> 0x43A370, even before its first text instruction.
    assert(#args==1 and type(args[1])=='string')
    return state.progress.scripts[script_key(args[1])]and 1 or 0
  end)
  define(0x99a5de25,function(vm,args)
    -- Original 0x450CA0 looks up the same normalized ID as 0x451330.
    assert(#args==1 and type(args[1])=='string')
    local key=args[1]:match('^[^.]*'):lower()
    return state.progress.pictures[key]and 1 or 0
  end)
  define(0xeb5cc468,function(vm,args)
    -- Original handler 0x451330 records normalized picture IDs in a bounded
    -- global viewed-picture table. It does not create or display the picture.
    assert(#args==1 and type(args[1])=='string')
    local key=args[1]:match('^[^.]*'):lower()
    if not state.progress.pictures[key] then
      local count=0;for _ in pairs(state.progress.pictures)do count=count+1 end
      if count>=10000 then return -1 end
      state.progress.pictures[key]=true;state.persist_dirty=true
    end
    return 0
  end)
  define('$wait',function(vm,args)
    assert(#args>=1 and #args<=2)
    local reader=state.reading_context
    if reader and reader.thread==vm.thread_id then
      state.reading_wait=#vm.frames<=reader.depth and state.console.visible
    end
    -- Original 0x442589 stores now-10 for wait(-2), requesting a scheduler
    -- yield without waiting for a new display frame. wait(0) also has an
    -- already-reached deadline. Positive waits still use real time.
    if args[1]==-2 or args[1]==0 then vm.waiting={kind='yield'}
    elseif args[1]<0 then vm.waiting={kind='context',fast_interrupt=true}
    else vm.waiting={kind='time',until_ms=options.now()+args[1],fast_interrupt=true}end
    return 0
  end)
  define('$timer_progress',function(vm,args)
    assert(#args==3)
    if args[2]<=0 then return args[3]end
    local elapsed=math.min(math.abs(options.now()-args[1]),args[2])
    local value=elapsed*args[3]/args[2]
    return value<0 and math.ceil(value)or math.floor(value)
  end)
  define('$timer_sync',function(vm,args,wants)
    assert(#args==2)
    local start=args[1]
    if math.abs(options.now()-start)>=args[2]*10 then start=options.now()end
    local until_ms=start+args[2]
    vm.waiting={kind='time',until_ms=until_ms,wants_result=wants,resume_value=until_ms}
    return nil,true
  end)
  define('$invalidate_disable',function(vm,args)
    assert(#args==0);state.invalidate_depth=state.invalidate_depth+1
    state.invalidate=state.invalidate_depth==0;return 0
  end)
  define('$invalidate_enable',function(vm,args)
    assert(#args==0);state.invalidate_depth=state.invalidate_depth-1
    state.invalidate=state.invalidate_depth==0;state.dirty=true;return 0
  end)
  define('$invalidate_rect',function(vm,args)
    assert(#args==1 or #args==5,'invalidate_rect signature')
    if not state.pages[args[1]] then vm:fail('invalidate page missing') end
    state.dirty=true;return 0
  end)
  define('$draw_wait',function(vm,args)
    assert(#args<=1)
    -- Original 0x4418F1/0x471220 returns immediately while invalidation is
    -- suspended. Otherwise real playback waits for the finished frame.
    if state.invalidate_depth~=0 then return 0 end
    local real=options.wait_for_present
    if type(real)=='function'then real=real()end
    if real then
      state.present_requested=state.present_requested+1
      vm.waiting={kind='present',generation=state.present_requested}
    else vm.waiting={kind='time',until_ms=options.now()+1}end
    state.dirty=true;return 0
  end)
  define('$thread_begin',function(vm,args)
    assert(#args>=1)
    local parameters=vm:argument_slice(args,2)
    return vm:spawn(args[1],parameters,false)
  end)
  define('$thread_begin_child',function(vm,args)
    assert(#args>=1)
    local parameters=vm:argument_slice(args,2)
    return vm:spawn(args[1],parameters,true)
  end)
  define('$thread_end',function(vm,args)
    assert(#args<=1);vm:terminate(args[1] or vm.thread_id);return 0
  end)
  define('$thread_is_exist',function(vm,args)
    assert(#args==1);local t=vm.threads[args[1]]
    return t and not t.finished and 1 or 0
  end)
  define(0xc438ba98,function(vm,args)
    assert(#args==0)
    local thread=vm.threads[vm.thread_id]
    state.modal_stack=state.modal_stack or {}
    state.modal_stack[#state.modal_stack+1]={thread=vm.thread_id,areas=thread.areas,
      defaults=thread.area_defaults,save=state.settings.save,save_menu=state.settings.save_menu}
    thread.areas={};thread.area_defaults={}
    state.settings.save=false;state.settings.save_menu=false
    state.ui_waiting=false;return 0
  end)
  define(0x3378635d,function(vm,args)
    -- Original 0x450DD0 pairs with 0x450B40 and restores suspended areas.
    assert(#args==0)
    local saved=state.modal_stack and table.remove(state.modal_stack)
    if not saved then vm:fail('modal context stack underflow')end
    local thread=vm.threads[saved.thread]
    if not thread then vm:fail('modal owner thread missing')end
    thread.areas,thread.area_defaults=saved.areas,saved.defaults
    state.settings.save,state.settings.save_menu=saved.save,saved.save_menu
    state.ui_areas=thread.areas;state.ui_waiting=false;return 0
  end)
  local window_menus=options.modules('runtime/majiro_window_menus.lua').new(state,dispatch_event)
  local function poll(vm,event)
    if event and event.window_command and event.window_command~=0 then
      state.window_menu_result=window_menus.request(vm,event.window_command)
    end
    state.last_poll_wall=(options.wall_now or options.now)()
    if dialogs.poll(vm)then return not dialogs.active()end
    window_menus.poll(vm)
    input.sample(event or {})
    if state.media.update_pending then state.media.update_pending(vm)end
    sprites.update()
    vm:each_thread(function()
      -- Host-budget exhaustion is not a source input yield. Preserve pending
      -- clicks until this thread reaches its own scheduler boundary.
      local input_boundary=vm.waiting~=nil
      -- Original scheduler 0x439277 -> 0x439286 activates a forward
      -- left-selection handler in the main context while fast mode is on.
      -- This also releases a PAUSE entered before CTRL/the skip button.
      if input_boundary and vm.thread_id==1 and providers['$is_fast_mode'](vm,{})~=0 then
        vm:signal('left',true)
      end
      if input_boundary and state.input.buttons%2==1 and vm:signal('left') then state.input.buttons=state.input.buttons-1 end
      if input_boundary and math.floor(state.input.buttons/2)%2==1 and vm:signal('right') then
        state.input.buttons=state.input.buttons-2
      end
      local wait=vm.waiting
      if not wait then return end
      -- Original 0x4424F8 cancels the main context's existing $wait as
      -- soon as source fast mode is active; background timers keep running.
      if wait.fast_interrupt and vm.thread_id==1 and providers['$is_fast_mode'](vm,{})~=0 then
        vm.waiting=nil
        if wait.wants_result then vm:push(wait.resume_value or 0)end
        return
      end
      if wait.kind=='movie'and(not state.media.movie or
         options.movie('status',state.media.movie)==0)then vm.waiting=nil;return end
      if wait.kind=='time' and options.now()>=wait.until_ms then
        vm.waiting=nil
        if wait.wants_result then vm:push(wait.resume_value or 0)end
        return
      end
      if wait.kind=='audio'then
        if wait.channel then
          local spec=state.media.channels[wait.channel]
          if not spec or spec.generation~=wait.generation or not options.audio('playing',spec)then
            vm.waiting=nil;return
          end
          wait.until_ms=not spec.loop and spec.paused_at==nil and
            math.max(wall_now()+10,spec.started+spec.duration_ms)or nil
        elseif wait.until_ms and wall_now()>=wait.until_ms then vm.waiting=nil;return end
      end
      if wait.kind=='present' and state.present_completed>=wait.generation then
        vm.waiting=nil;return
      end
      if wait.kind=='input' and state.input.buttons~=0 then
        vm.waiting=nil
        if wait.wants_result then vm:push(input.read()) else input.read() end
      end
    end)
    return not vm.waiting
  end
  local aliases=options.modules('runtime/majiro_syscall_aliases.lua')
  options.modules('runtime/majiro_tiles_extra.lua').install(define,state,options)
  for hash,name in pairs(aliases)do
    if not providers[hash]and providers[name]then providers[hash]=providers[name]end
  end
  for hash,rule in pairs(options.modules('runtime/majiro_native_constants.lua'))do
    if not providers[hash]then
      local argc=rule.argc
      define(hash,function(vm,args)assert(#args==argc,'native constant signature');return 0 end)
    end
  end
  local function invoke(vm,hash,args,wants_result)
    local name=state.names[hash]
    local provider=providers[hash] or (name and providers[name])
    if not provider then
      vm:fail(string.format('unimplemented syscall %08x %s argc=%d',hash,name or '?',#args))
    end
    name=name or string.format('%08x',hash)
    state.calls[name]=(state.calls[name] or 0)+1
    local ok,value,deferred=pcall(provider,vm,args,wants_result)
    if not ok then
      if vm.status=='blocked' then error(value,0) end
      vm:fail('syscall '..name..': '..tostring(value))
    end
    return value,deferred,(name:sub(-1)=='%'or name=='$rand!')and 1 or nil
  end
  return {invoke=invoke,state=state,providers=providers,sprites=sprites,emit=emit,
    dispatch_event=dispatch_event,window_menus=window_menus,saves=saves,dialogs=dialogs,
    wall_deadline=wall_deadline,
    resume_action=function(vm,action)
      if action.name=='console_redraw'then
        console_actions.redraw(vm,action)
        return
      end
      if action.name=='chapter_menu_return'then
        local outgoing={}
        for id,sprite in pairs(state.sprites)do
          if id>=action.first_sprite and sprite.source_script=='menu.mjo'then
            outgoing[#outgoing+1]=id
          end
        end
        for _,id in ipairs(outgoing)do providers['$sprite_release'](vm,{id})end
        local thread=assert(vm.threads[action.thread],'chapter menu owner missing')
        thread.areas,thread.area_defaults,thread.area_focus=action.areas,action.defaults,action.focus
        state.settings.save,state.settings.save_menu=action.save,action.save_menu
        state.ui_areas=thread.areas;state.active_ui_script=action.script
        state.chapter_menu_active=nil;state.ui_waiting=false
        return
      end
      assert(action.name=='input_return','unknown host continuation')
      if action.reading then state.reading_wait=true end
    end,
    presented=function(generation)
      state.present_completed=math.max(state.present_completed,generation or 0)
      local page=state.fast_page
      if page and not page.started and state.present_completed>=page.generation then
        page.started=wall_now()
      end
    end,
    strings=strings,
    line_changed=line_changed,
    persistent_changed=function()state.persist_dirty=true end,
    script_loaded=function(name)
      local key=script_key(name)
      if not state.progress.scripts[key]then
        state.progress.scripts[key]=true;state.persist_dirty=true
      end
    end,
    text=text,screen=screen,
    input=input,
    poll=poll,
    after_init=function(vm,done,reason)
      if reason~=nil then
        sprites.reset()
        release_screen_snapshot(vm)
        state.ui_waiting=false;state.ui_areas={};state.active_ui_script=nil
        state.reading_wait=false;state.reading_context=nil;state.fast_page=nil
        state.modal_stack={}
        state.chapter_menu_active=nil
        state.input.buttons=0;state.input.held_buttons=0;state.input.pressed={};state.input.down={}
        providers['$reset_fast_mode'](vm,{})
      end
      -- Host result 5 selects restart; 0x45AC70 then clears ESI before
      -- invoking HOT_RESET. Passing 5 would restore the previous scene's
      -- picture/filter state over the new title.
      emit(vm,'HOT_RESET',{0,'',0},done)
    end}
end
return M
