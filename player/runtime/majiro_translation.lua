-- Read-only bytecode lookahead and display-only asynchronous translation.
-- No VM step, loader, branch evaluator, input handler or provider is called.
local M={}
function M.display_bytes(text,cp932)
  local output={}
  for char in text:gmatch('[%z\1-\127\194-\244][\128-\191]*')do
    local ok,raw=pcall(cp932,char)
    local replacement=#char>1 and raw=='?'
    output[#output+1]=ok and type(raw)=='string'and not replacement and raw or'\0\0'
  end
  return table.concat(output)
end
function M.new(options)
  local json,host=assert(options.json),assert(options.host)
  local now=options.now or function()return 0 end
  local api={}
  local config={mode=options.mode or'original',context=20,ready=false}
  local generation,vm_generation,context,submitted=nil,nil,{},{}
  local ids=setmetatable({},{__mode='k'})
  local pending=setmetatable({},{__mode='k'})
  local voice_waits=setmetatable({},{__mode='k'})
  local fallback_ids={}
  local since=0
  local stats={translated=0,fallback=0,waiting=0,submitted=0}
  api.stats=stats
  local next_submit,next_config=0,0
  local function call(action,value)
    local ok,result=pcall(function()
      return host(action,type(value)=='table'and json.encode(value)or(value or''))
    end)
    return ok and type(result)=='string'and result or''
  end
  local function reset(vm)
    if vm_generation~=vm.context_generation then
      vm_generation=vm.context_generation;context={};submitted={};since=0;next_submit=0
      pending=setmetatable({},{__mode='k'})
      voice_waits=setmetatable({},{__mode='k'});fallback_ids={}
    end
  end
  local function refresh()
    if now()<next_config then return end
    next_config=now()+1000
    local ok,value=pcall(json.decode,call('config',''))
    if ok and type(value)=='table'then
      local changed=generation~=value.generation or config.mode~=value.mode
      config=value;config.context=math.max(2,math.min(200,tonumber(config.context)or 20))
      if changed then generation=value.generation;submitted={};next_submit=0 end
    else config.ready=false end
  end
  local function enabled()
    return options.mode=='online'and config.mode=='online'and config.ready==true
  end
  local function ident(script,row)
    assert(not script.patch_source and not script.text_patch and not script.raw_patch_applied
      and(not script.text_origin or script.text_origin=='original'),
      'online translation received localized script data')
    local cached=ids[script]
    if not cached then cached={};ids[script]=cached end
    local key=tostring(row[1])..'\n'..row[3]
    if not cached[key]then
      cached[key]='dialogue-v3-original:'..(script.source_sha256 or script.name)..':'..tostring(row[1])..':'..options.hash(row[3])
    end
    return cached[key]
  end
  local function text_line(script,index,row)
    row=row or script.code[index]
    local value=row[3]
    local line={id=ident(script,row),text=value,source=value}
    if options.speaker_popup then
      local quote=value:find('「',1,true)
      if quote and quote>1 then
        line.speaker=value:sub(1,quote-1);line.text=value:sub(quote)
      elseif not quote and index then
        local following=index+1
        while script.code[following]and script.code[following][2]==0x83a do following=following+1 end
        -- NAMEPOPUP names can occupy a separate text opcode before the quote.
        local next_row=script.code[following]
        if next_row and next_row[2]==0x840 and next_row[3]:sub(1,#'「')=='「'then
          line.speaker_only=true;line.speaker=value
        end
      elseif quote==1 and index then
        local before=index-1
        while script.code[before]and script.code[before][2]==0x83a do before=before-1 end
        local previous=script.code[before]
        if previous and previous[2]==0x840 and not previous[3]:find('「',1,true)then
          line.speaker=previous[3]
        end
      end
    end
    return line
  end
  local function reference()
    local result=json.array()
    for index=math.max(1,#context-config.context+1),#context do result[#result+1]=context[index]end
    return result
  end
  local function submit(lines)
    if #lines==0 or now()<next_submit or not enabled()then return end
    local payload={}
    for _,line in ipairs(lines)do
      if not line.speaker_only then payload[#payload+1]={id=line.id,text=line.text,speaker=line.speaker}end
    end
    if #payload==0 then return end
    local result=call('submit',{game=options.game,lines=json.array(payload),context=reference(),generation=generation})
    if result=='queued'or result=='cached'or result=='ok'then
      for _,line in ipairs(lines)do submitted[line.id]=true end
      if result=='queued'then stats.submitted=stats.submitted+#lines end
    end
    next_submit=now()+((result=='busy'or result=='retry')and 2000 or 200)
  end
  local function scalar(row,kind)
    return kind and(row[2]<0x800 or kind=='line'or kind=='proc'or kind=='ldc.i'or kind=='ldc.r'
      or kind=='ldstr'or kind=='ld'or kind=='ldelem'or kind=='pop'or kind=='argcheck'or kind=='alloca'
      or kind=='conv.i'or kind=='conv.r')
  end
  local function harmless(hash)
    local name=(options.names or{})[hash]
    if not name then return false end
    for _,prefix in ipairs{'$voice','$sound','$music','$extmusic','$extsound','$sprite_','$page_','$pic_','$grp_','$timer',
      '$is_','$get_','$set_','$reg_'}do
      if name:sub(1,#prefix)==prefix then return true end
    end
    return({['$wait']=true,['$rgb']=true,['$rgba']=true,['$fontout']=true,
      ['$console_font']=true,['$console_color']=true,['$console_locate']=true,
      ['$console_locate_force']=true,['$console_curpos_x']=true,['$console_curpos_y']=true,
      ['$console_setzerotime']=true,['$console_resetzerotime']=true,
      ['$fontout2']=true,['$rgb']=true,['$rgba']=true,['$strlen']=true,['$atoi']=true,
      ['$strstr']=true,['$stricmp']=true,['$strcmp']=true,['$abs']=true,
      ['$min']=true,['$max']=true,['$rand']=true,['$rand!']=true})[name]or false
  end
  function api.scan(script,index,limit,vm)
    local positions=setmetatable({},{__mode='k'})
    local function position(source,offset)
      if source.positions then return source.positions[offset]end
      if not positions[source]then
        local map={};for i,row in ipairs(source.code)do map[row[1]]=i end;positions[source]=map
      end
      return positions[source][offset]
    end
    local function resolve(source,hash)
      for _,fn in ipairs(source.functions or{})do
        if fn[1]==hash then return{script=source,offset=fn[2]}end
      end
      return vm and vm.functions and vm.functions[hash]
    end
    local summaries,active={},{}
    local budget=0;local failure
    -- Prove every reachable arm of a no-text callee returns. This inspects
    -- control flow only: no expression, branch predicate or syscall is run.
    local returns
    returns=function(source,at,depth,visiting)
      if not at or depth>32 then failure='invalid target/depth';return false end
      budget=budget+1;if budget>10000 then failure='proof budget';return false end
      local cache=summaries[source];if not cache then cache={};summaries[source]=cache end
      if cache[at]~=nil then return cache[at]end
      visiting=visiting or{}
      if visiting[at]then failure='callee loop';return false end
      visiting[at]=true
      local row=source.code[at];local spec=row and options.opcodes[row[2]];local kind=spec and spec[1]
      local result=false
      if kind=='ret'then result=true
      elseif kind=='br'or kind=='br.case'then
        result=returns(source,position(source,row[3]),depth,visiting)
      elseif kind=='brtrue'or kind=='brfalse'or(kind and kind:match('^b[nlg][et]%.case$'))then
        result=returns(source,at+1,depth,visiting)and returns(source,position(source,row[3]),depth,visiting)
      elseif kind=='switch'then
        result=true
        for _,target in ipairs(row[3])do
          if not returns(source,position(source,target),depth,visiting)then result=false;break end
        end
      elseif kind=='call'or kind=='callp'then
        local target=resolve(source,row[3])
        if target then
          local key=target.script.name..':'..target.offset
          if not active[key]then
            active[key]=true
            result=returns(target.script,position(target.script,target.offset),depth+1,{})
              and returns(source,at+1,depth,visiting)
            active[key]=nil
          end
        end
      elseif kind=='syscall'or kind=='syscallp'then
        if not harmless(row[3])then failure='syscall '..((options.names or{})[row[3]]or string.format('%08x',row[3]))end
        result=harmless(row[3])and returns(source,at+1,depth,visiting)
      elseif scalar(row or{},kind)and kind~='proc'then result=returns(source,at+1,depth,visiting)
      else failure='callee '..tostring(kind)end
      visiting[at]=nil;cache[at]=result;return result
    end
    local result,visited={},{}
    local at=index
    local reason='end'
    while at and at<=#script.code and #result<limit do
      if visited[at]then reason='loop';break end
      visited[at]=true
      if at-index>20000 then reason='budget';break end
      local row=script.code[at];local spec=options.opcodes[row[2]];local kind=spec and spec[1]
      if kind=='text'then
        if type(row[3])~='string'or #row[3]>16000 then break end
        local line=text_line(script,at,row)
        if not line.speaker_only then result[#result+1]=line end
      elseif kind=='ctrl'then
        if not({n=true,N=true,p=true,P=true,w=true})[row[3]]then reason='dynamic text';break end
      elseif kind=='br'then
        at=position(script,row[3]);reason='invalid branch';if not at then break end
        at=at-1
      elseif kind=='call'or kind=='callp'then
        local target=resolve(script,row[3])
        if not target or not returns(target.script,position(target.script,target.offset),0,{})then reason='unproven call';break end
      elseif kind=='syscall'or kind=='syscallp'then
        if not harmless(row[3])then reason='unproven syscall';break end
      elseif not scalar(row,kind)then reason=kind or'unknown';break end
      at=at+1
    end
    api.last_scan={lines=#result,reason=#result>=limit and'filled'or reason,proof_nodes=budget,
      proof_failure=failure}
    return result
  end
  function api.before_instruction(vm,kind,row)
    local name=(kind=='syscall'or kind=='syscallp')and(options.names or{})[row[3]]
    local voiced=name=='$voice'or name=='$voice_raw'or name=='$voice_ch'
      or(name and name:match('^%$voice_ch_%d+$'))
      or(kind=='ctrl'and type(row[3])=='string'and row[3]:sub(1,1)=='v')
    if not voiced then return false end
    reset(vm);refresh()
    if not enabled()then return false end
    local frame=vm:frame()
    local waiting=voice_waits[frame]
    if not waiting or waiting.offset~=row[1]then
      local next_lines
      for index=#vm.frames,1,-1 do
        local parent=vm.frames[index]
        local lines=api.scan(parent.script,parent.index,config.context,vm)
        if #lines>0 then next_lines=lines;break end
      end
      if not next_lines then return false end
      waiting={offset=row[1],lines=next_lines,began=now()};voice_waits[frame]=waiting
    end
    local first=waiting.lines[1]
    local value=call('lookup',{game=options.game,id=first.id,generation=generation})
    if value~=''and #value<=32000 then stats.waiting=0;return false end
    submit(waiting.lines)
    if now()-waiting.began>=(options.wait_ms or 0)then
      fallback_ids[first.id]=true;stats.waiting=0;return false
    end
    stats.waiting=1
    if not waiting.notified then waiting.notified=true;call('waiting','')end
    vm.waiting={kind='time',until_ms=now()+50}
    return true
  end
  function api.instruction(vm,kind,value)
    reset(vm);refresh()
    if not enabled()then return end
    if kind=='text'then
      local frame=vm:frame();local script=frame.script
      local row={vm.current_offset,nil,value}
      local index=frame.index-1
      local line=text_line(script,index,row);local id=line.id
      local list=pending[frame]or{};pending[frame]=list
      list[#list+1]=line
      if not line.speaker_only then context[#context+1]=line.text end
      if #list>200 then pending[frame]=nil end
      if #context>200 then table.remove(context,1)end
      if line.speaker_only then return end
      since=since+1
      if not submitted[id]or since>=math.floor(config.context/2)then
        local fresh=not submitted[id]
        local start=fresh and frame.index-1 or frame.index
        local lines={}
        for _,line in ipairs(api.scan(script,start,config.context,vm))do
          if not submitted[line.id]then lines[#lines+1]=line end
        end
        submit(lines);since=fresh and 1 or 0
      end
    elseif kind=='ctrl'then
      if value:sub(1,1)=='w'then pending[vm:frame()]=nil end
    end
  end
  function api.literal(vm,value)
    pending[vm:frame()]=nil
  end
  function api.display(vm,source)
    local frame=vm:frame();local lines=pending[frame]
    if not enabled()or not lines or #lines==0 then return source end
    local originals,translated={},{}
    for _,line in ipairs(lines)do
      originals[#originals+1]=(line.source or line.text):gsub('|',' ')
      local value=not line.speaker_only and call('lookup',{game=options.game,id=line.id,generation=generation})or''
      if not line.speaker_only and(value==''or #value>32000)then
        submit({line})
        lines.wait_started=lines.wait_started or now()
        if not lines.wait_notified then
          lines.wait_notified=true;call('waiting','')
        end
        if not fallback_ids[line.id]and now()-lines.wait_started<(options.wait_ms or 0)then
          stats.waiting=1
          return source,true
        end
        pending[frame]=nil;stats.waiting=0;stats.fallback=stats.fallback+1
        call('display_status',{translated=stats.translated,fallback=stats.fallback})
        return source
      end
      if not line.speaker_only then
        value=value:gsub('[%z\1-\31]',' '):gsub('|',' ')
        if line.text:sub(1,#'「')=='「'then
          value=value:gsub('“','「'):gsub('”','」')
          local opening=value:find('「',1,true)
          if opening then value=value:sub(opening)
          else value='「'..value:gsub('^[“"]',''):gsub('[”"]$','')..'」'end
        end
        translated[#translated+1]=value
      end
    end
    local original,result=table.concat(originals),table.concat(translated)
    pending[frame]=nil;stats.waiting=0
    local function displayed(value)
      stats.translated=stats.translated+1
      call('display_status',{translated=stats.translated,fallback=stats.fallback})
      return value
    end
    local quote=original:find('「',1,true)
    if quote then
      local opening=result:find('「',1,true)
      if opening then result=result:sub(opening)
      else result='「'..result:gsub('^[“"]',''):gsub('[”"]$','')..'」'end
      if source==original then return displayed(original:sub(1,quote-1)..result)end
      if source==original:sub(quote)then return displayed(result)end
    elseif source==original then return displayed(result)end
    return source
  end
  api.display_bytes=M.display_bytes
  function api.poll(vm,state)reset(vm);refresh()end
  return api
end
return M
