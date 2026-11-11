-- Native raster output for source-drawn UI labels and slot captions.
local M={}
function M.install(define,state,options,graphics)
  define(0xcd111b0e,function(vm,args)
    assert(#args==1 and type(args[1])=='string','font filter signature')
    local value=args[1]:match('^[^%z]*')
    if options.cp932 and#options.cp932(value)>=32 then vm:fail('font filter exceeds original 32-byte slot')end
    state.font_filter=value
    if options.font_filter then options.font_filter(value)end
    return 0
  end)
  define('$load_font',function(vm,args)
    assert(#args==1 and type(args[1])=='string','load_font signature')
    if not options.load_font then vm:fail('private font host provider missing')end
    if not options.load_font(args[1])then vm:fail('private font file unavailable: '..args[1])end
    state.loaded_fonts=state.loaded_fonts or{}
    state.loaded_fonts[args[1]]=true;return 0
  end)
  local function registry_integer(key,fallback)
    local value=state.registry[key]
    if value==nil then return fallback end
    local part=tostring(value):match('^%s*([%+%-]?%d+)')
    if not part then return 0 end
    local negative=part:sub(1,1)=='-'
    local result=0;local limit=negative and 2147483648 or 2147483647
    for digit in part:gmatch('%d')do
      result=result*10+tonumber(digit)
      if options.integer_parse=='wrap32'then result=result%4294967296
      elseif result>limit then result=limit;break end
    end
    if negative then result=-result end
    return ((result+2147483648)%4294967296)-2147483648
  end
  for _,entry in ipairs{{'bold','fontbold',0},{'anti','fontanti',1},
    {'shadow','fontshadow',1},{'fuchidori','fuchidori',1}}do
    local name,key,fallback=entry[1],entry[2],entry[3]
    define('$is_font_setup_'..name,function(vm,args)
      assert(#args==0)
      return registry_integer(key,fallback)
    end)
  end
  local default_bold=registry_integer('fontbold',0)
  local function fresh(vm)
    local thread=vm.threads[vm.thread_id]
    return {face='ＭＳ ゴシック',height=20,width=10,step=25,style=0,color=0xffffff,
      shadow=-1,page=state.frontbuffer,x=0,y=0,previous=thread.fontout}
  end
  local function current(vm)
    local thread=vm.threads[vm.thread_id]
    thread.fontout=thread.fontout or fresh(vm)
    return thread.fontout
  end
  define('$fontout_push',function(vm,args)
    assert(#args==0)
    -- The original copies its global default style into a new node. It
    -- preserves the old node by linking it, rather than cloning that node.
    vm.threads[vm.thread_id].fontout=fresh(vm);return 0
  end)
  define('$fontout_pop',function(vm,args)
    assert(#args==0)
    local thread=vm.threads[vm.thread_id]
    if thread.fontout then thread.fontout=thread.fontout.previous end
    return 0
  end)
  define('$fontout_font',function(vm,args)
    assert(#args>=1 and #args<=5 and type(args[1])=='string')
    local f=current(vm)
    local legacy=(options.text_controls or{}).legacy_font_defaults
    local function truncate(value)return value<0 and math.ceil(value)or math.floor(value)end
    f.face=args[1];f.height=args[2]or 20
    f.width=args[3]or(legacy and truncate(f.height/2)or f.width)
    local scaled=((f.height*5+2147483648)%4294967296)-2147483648
    f.step=args[4]or(legacy and truncate(scaled/4)or f.step)
    f.style=args[5]or(legacy and 0 or default_bold)
    return 0
  end)
  define('$is_log_no_voice',function(vm,args)
    assert(#args==0)
    return options.testplay and(tonumber(options.log_no_voice)or 0)or 0
  end)
  define('$fontout_locate',function(vm,args)
    assert(#args==3)
    local f=current(vm)
    if not state.pages[args[1]] then vm:fail('fontout destination missing')end
    f.page,f.x,f.y=args[1],args[2],args[3];return 0
  end)
  define('$fontout_color',function(vm,args)
    assert(#args==1 or #args==2)
    local f=current(vm);f.color=args[1];f.shadow=args[2]or -1;return 0
  end)
  define('$fontout',function(vm,args)
    assert(#args==1 or #args==3 or #args==4)
    if args[1]=='' then return 0 end
    local f=current(vm)
    if not f.page then vm:fail('fontout target not assigned')end
    local text=options.display_text and options.display_text(args[1])or args[1]
    local command=string.format('text %d %d %d %d %d %d %d %d %d\n%s\n%s',
      f.page,f.x,f.y,f.height==0 and 20 or f.height,(options.font_face or f.proportional==1)and 0 or f.width,f.color,
      args[2]or 0,args[3]or 0,args[4]or 0,options.font_face or f.face,text)
    local reply=graphics(vm,command)
    local width=tonumber(reply:match('^OK (%d+)'))
    if not width then vm:fail('fontout metrics missing')end
    f.x=f.x+width;return 0
  end)
end
return M
