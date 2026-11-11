-- Majiro execution core, Lua 5.1. Unsupported semantics stop at their source.
-- No coroutine stack is saved; all execution frames are explicit.
local M = {}
local VM = {}; VM.__index = VM
local U32 = 4294967296
local function unsigned(n) return n % U32 end
local function signed(n)
  n = unsigned(n)
  if n >= 2147483648 then return n - U32 end
  return n
end
local function bitop(a,b,op)
  a,b=unsigned(a),unsigned(b)
  local result,power=0,1
  for _=1,32 do
    local x,y=a%2,b%2
    if (op=='and' and x==1 and y==1) or
       (op=='or' and (x==1 or y==1)) or (op=='xor' and x~=y) then
      result=result+power
    end
    a,b,power=math.floor(a/2),math.floor(b/2),power*2
  end
  return signed(result)
end
local function truth(x) return x~=0 and x~=false and x~=nil and x~='' end
local function clear_click_handlers(frame)
  for i=#(frame.handlers or {}),1,-1 do
    if frame.handlers[i].kind~='destructor' then table.remove(frame.handlers,i) end
  end
end
local function set_handler(frame,kind,offset,stack_size)
  frame.handlers=frame.handlers or {}
  for i=#frame.handlers,1,-1 do
    if frame.handlers[i].kind==kind then table.remove(frame.handlers,i) end
  end
  frame.handlers[#frame.handlers+1]={kind=kind,offset=offset,stack_size=stack_size}
end
local function bool(x) return x and 1 or 0 end
local function trunc(x) return x<0 and math.ceil(x) or math.floor(x) end
local function f32(x)
  -- Explicit round-to-nearest, ties-to-even for binary32.
  if x==0 or x~=x or x==math.huge or x==-math.huge then return x end
  local sign=x<0 and -1 or 1; x=math.abs(x)
  local _,exponent=math.frexp(x)
  local scale=math.max(exponent-24,-149)
  local units=math.ldexp(x,-scale); local floor=math.floor(units)
  if units-floor>0.5 or (units-floor==0.5 and floor%2==1) then floor=floor+1 end
  local value=math.ldexp(floor,scale)
  if value>=math.ldexp(1,128) then value=math.huge end
  return sign*value
end
local function value_type(value)
  if type(value)=='string'then return 2 end
  if type(value)=='table'and value.kind=='array'then return 3+value.element_type end
  return type(value)=='number'and value%1~=0 and 1 or 0
end
local function float_bits(value)
  value=f32(value)
  if value~=value then return 2143289344 end
  local sign=(value<0 or(value==0 and 1/value==-math.huge))and 2147483648 or 0
  value=math.abs(value)
  if value==math.huge then return sign+2139095040 end
  if value==0 then return sign end
  if value<math.ldexp(1,-126)then return sign+math.ldexp(value,149)end
  local fraction,exponent=math.frexp(value)
  return sign+(exponent+126)*8388608+(fraction*2-1)*8388608
end
local function bits_float(bits)
  bits=unsigned(bits)
  local sign=bits>=2147483648 and -1 or 1
  bits=bits%2147483648
  local exponent=math.floor(bits/8388608)
  local fraction=bits%8388608
  if exponent==255 then return fraction==0 and sign*math.huge or 0/0 end
  if exponent==0 then return sign*math.ldexp(fraction,-149)end
  return sign*math.ldexp(1+fraction/8388608,exponent-127)
end
local function raw_number(value,source,target)
  if source==target then return value end
  if source==1 and target==0 then return signed(float_bits(value))end
  if source==0 and target==1 then return bits_float(value)end
  return value
end
local function default(t)
  if t==2 then return '' end
  if t>=3 then return {kind='array',element_type=t-3,dims={1},values={}} end
  return 0
end

function M.new(options)
  assert(options and options.loader and options.opcodes and options.syscall)
  local numeric_profile=options.numeric_profile or 'sse32'
  assert(numeric_profile=='sse32'or numeric_profile=='x87','unknown numeric semantics profile')
  local vm=setmetatable({
    loader=options.loader,opcodes=options.opcodes,syscall=options.syscall,
    after_init=options.after_init,
    restart_on_return=options.restart_on_return,
    persistent_changed=options.persistent_changed,
    script_loaded=options.script_loaded,
    string_ops=options.string_ops,
    text_ops=options.text_ops,
    line_changed=options.line_changed,
    before_instruction=options.before_instruction,
    resume_action=options.resume_action,
    wall_deadline=options.wall_deadline,
    numeric_profile=numeric_profile,
    array_3d_legacy_stride=options.array_3d_legacy_stride==true,
    case_string_relations=options.case_string_relations~=false,
    scripts={},functions={},libraries={},frames={},stack={},stack_types={},duplicate_functions={},load_order={},
    globals={[0]=options.persistent_globals or{},[1]={},[2]={}},hooks={},steps=0,status='ready',
    trace={},trace_limit=48,opcode_counts={},syscall_counts={},thread_id=1,next_thread=2,
    threads={},thread_order={1},schedule_cursor=0,context_generation=0,
  },VM)
  vm.threads[1]={id=1,frames=vm.frames,stack=vm.stack,stack_types=vm.stack_types,globals=vm.globals[2]}
  return vm
end
function VM:save_thread()
  local t=self.threads[self.thread_id]
  t.frames,t.stack,t.globals,t.waiting=self.frames,self.stack,self.globals[2],self.waiting
  t.stack_types=self.stack_types
end
function VM:select_thread(id)
  self:save_thread()
  local t=assert(self.threads[id],'thread not found')
  self.thread_id=id
  self.frames,self.stack,self.globals[2],self.waiting=t.frames,t.stack,t.globals,t.waiting
  t.stack_types=t.stack_types or {};self.stack_types=t.stack_types
end
function VM:each_thread(callback)
  local original=self.thread_id
  for _,id in ipairs(self.thread_order)do
    local t=self.threads[id]
    if not t.finished then self:select_thread(id);callback(t);self:save_thread() end
  end
  self:select_thread(original)
end
function VM:resolve(hash)
  hash=unsigned(hash)
  local frame=self:frame()
  if frame then
    for _,fn in ipairs(frame.script.functions)do
      if fn[1]==hash then return {script=frame.script,offset=fn[2]} end
    end
  end
  return self.functions[hash]
end
function VM:spawn(hash,args,child)
  local fn=self:resolve(hash)
  if not fn then self:fail(string.format('thread function missing %08x',unsigned(hash))) end
  local parent=self.thread_id
  local id=self.next_thread;self.next_thread=id+1
  self.threads[id]={id=id,frames={},stack={},stack_types={},globals={},owner=child and parent or nil}
  self.thread_order[#self.thread_order+1]=id
  self:select_thread(id);self:enter(fn.script,fn.offset,args,false);self:save_thread()
  self:select_thread(parent)
  return id
end
function VM:inject_event(id,calls)
  -- Native do_event_single pushes callback frames on the target context,
  -- then restores the sender. A parent's wait belongs to its suspended
  -- frame, so callbacks run before that wait resumes.
  self:save_thread()
  local target=self.threads[id]
  if not target or target.finished or target.terminate_requested then return false end
  if #calls==0 then return true end
  for _,call in ipairs(calls)do
    local script=self:load(call.script)
    if not script.positions[call.offset]then self:fail('event callback source position missing')end
  end
  local sender=self.thread_id
  self:select_thread(id)
  local waiting=self.waiting
  self.waiting=nil
  self:resume{kind='sequence',calls=calls,
    done={kind='event_return',waiting=waiting,depth=#self.frames}}
  self:save_thread()
  self:select_thread(sender)
  return true
end
function VM:terminate(id)
  local t=self.threads[id]
  if not t or t.finished then return end
  t.terminate_requested=true
  if id==self.thread_id then self.waiting=nil else t.waiting=nil end
  for _,child in pairs(self.threads)do
    if child.owner==id then self:terminate(child.id)end
  end
end
function VM:unwind()
  local thread=self.threads[self.thread_id]
  if thread.unwind_depth and #self.frames>=thread.unwind_depth then
    self:step()
    if #self.frames<thread.unwind_depth then thread.unwind_depth=nil end
    return
  end
  thread.unwind_depth=nil
  local frame=self:frame()
  if not frame then self.threads[self.thread_id].finished=true;return end
  frame.on_return=nil;frame.wants_result=false
  if frame.cancel_cleanup then self:step();return end
  self:truncate_stack(frame.stack_base)
  local depth=#self.frames
  self:push(0);frame.cancel_cleanup=true;self:finish()
  if #self.frames>=depth then thread.unwind_depth=depth end
end
function VM:next_deadline()
  self:save_thread()
  local next_time
  for _,t in pairs(self.threads)do
    if not t.finished and t.waiting and(t.waiting.kind=='time'or t.waiting.kind=='movie'or t.waiting.kind=='audio')then
      local deadline=t.waiting.until_ms
      if self.wall_deadline and t.waiting.kind=='time'then deadline=self.wall_deadline(deadline)end
      if deadline then next_time=math.min(next_time or math.huge,deadline)end
    end
  end
  return next_time
end
function VM:frame() return self.frames[#self.frames] end
function VM:float(value)return f32(value)end
function VM:fail(reason)
  local frame=self:frame()
  self.status='blocked'
  self.problem={
    reason=tostring(reason),script=frame and frame.script.name or '',
    offset=self.current_offset or -1,line=frame and frame.line or 0,steps=self.steps,
  }
  error(string.format('%s@%08x line %d: %s',self.problem.script,
    math.max(0,self.problem.offset),self.problem.line,self.problem.reason),0)
end
function VM:push(value,kind)
  if value==nil then self:fail('nil cannot be placed on Majiro stack') end
  local index=#self.stack+1
  self.stack[index]=value;self.stack_types[index]=kind or value_type(value)
end
function VM:pop_typed()
  local frame=self:frame()
  if #self.stack<=(frame and frame.stack_base or 0) then self:fail('operand stack underflow') end
  local index=#self.stack
  local value,kind=self.stack[index],self.stack_types[index]
  self.stack[index]=nil;self.stack_types[index]=nil
  return value,kind or value_type(value)
end
function VM:pop()
  local value=self:pop_typed()
  return value
end
function VM:truncate_stack(size)
  while #self.stack>size do
    self.stack_types[#self.stack]=nil;table.remove(self.stack)
  end
end
function VM:arguments(count)
  local args={_types={}}
  -- Majiro's first declared argument is nearest the top of the caller stack.
  for i=1,count do args[i],args._types[i]=self:pop_typed() end
  return args
end
function VM:argument_slice(args,first,last)
  local values={_types={}}
  for i=first,math.min(last or #args,#args)do
    local target=i-first+1
    values[target]=args[i]
    values._types[target]=args._types and args._types[i]or value_type(args[i])
  end
  return values
end
function VM:load(name)
  -- Original script loading uses _splitpath's filename and replaces its
  -- extension with .mjo. Source scripts can legitimately name a .mjs file.
  name=name:match('^[^%z]*'):gsub('\\','/'):match('([^/]*)$'):gsub('^[A-Za-z]:','')
  name=name:gsub('%.[^.]*$','')..'.mjo'
  if self.scripts[name] then return self.scripts[name] end
  for loaded,script in pairs(self.scripts) do if loaded:lower()==name:lower() then return script end end
  local script=self.loader(name)
  if not script then self:fail('script not found: '..name) end
  -- The host resolves CP932/fullwidth case aliases to the archive's name.
  -- Reuse that script object before registering callbacks/functions again.
  if self.scripts[script.name]then return self.scripts[script.name]end
  script.positions={}
  for index,row in ipairs(script.code) do script.positions[row[1]]=index end
  self.scripts[script.name]=script
  self.load_order[#self.load_order+1]=script.name
  if self.script_loaded then self.script_loaded(script.name)end
  for _,fn in ipairs(script.functions) do
    -- The original searches the current script first (0x43A470). Libraries
    -- may intentionally repeat private helper hashes, e.g. both transit files.
    local previous=self.functions[fn[1]]
    if previous and previous.script~=script then
      if fn[1]~=487755568 and fn[1]~=2981858012 then
        self.duplicate_functions[#self.duplicate_functions+1]={hash=fn[1],
          previous=previous.script.name,additional=script.name}
      end
    elseif not previous then
      -- Native lookup includes main/init hashes when the current file does
      -- not define them. Every fallback lookup selects the first table row,
      -- including duplicate hashes within the same library.
      self.functions[fn[1]]={script=script,offset=fn[2]}
    end
  end
  return script
end
function VM:enter(script,offset,args,wants_result,on_return)
  local index=script.positions[offset]
  if not index then self:fail('invalid function offset '..tostring(offset)) end
  -- Event hooks may reuse the same argument list for several calls. Each
  -- Majiro frame owns its scalar slots; arrays retain their referenced value.
  local parameters,parameter_types={},{}
  local parent=self:frame()
  local argument_tail={}
  if parent then
    -- Original negative offsets address physical caller slots beyond argc.
    -- Keep explicit references to that layout instead of inventing optional
    -- arguments or weakening argcheck. Some shipped vararg helpers read here.
    for i=#self.stack,parent.stack_base+1,-1 do
      argument_tail[#argument_tail+1]={bank=self.stack,key=i}
    end
    local locals={}
    for key in pairs(parent.locals)do if type(key)=='number'then locals[#locals+1]=key end end
    table.sort(locals,function(a,b)return a>b end)
    for _,key in ipairs(locals)do argument_tail[#argument_tail+1]={bank=parent.locals,key=key}end
    argument_tail[#argument_tail+1]={bank=parent.system,key=2802105789}
    for i=1,#parent.args do argument_tail[#argument_tail+1]={bank=parent.args,key=i}end
    for _,slot in ipairs(parent.argument_tail or{})do argument_tail[#argument_tail+1]=slot end
  end
  for i,value in ipairs(args or {})do parameters[i]=value end
  for i,value in ipairs(parameters)do parameter_types[i]=args._types and args._types[i]or value_type(value)end
  self.frames[#self.frames+1]={script=script,index=index,args=parameters,locals={},
    arg_types=parameter_types,
    argument_tail=argument_tail,
    system={[2802105789]=#(args or {})},
    stack_base=#self.stack,wants_result=wants_result,on_return=on_return,line=0}
end
function VM:resume(action,result)
  if not action then return end
  if type(action)=='function'then action(self,result);return end
  if action.kind=='push'then
    if action.wants then self:push(result or 0)end
  elseif action.kind=='enter'then
    self:enter(self:load(action.script),action.offset,action.args,action.wants,action.done)
  elseif action.kind=='after_init'then
    local script=self:load(action.script)
    local next_step={kind='enter',script=script.name,offset=script.main}
    if self.after_init then self.after_init(self,next_step)else self:resume(next_step)end
  elseif action.kind=='sequence'then
    action.index=(action.index or 0)+1
    local call=action.calls[action.index]
    if call then
      self:enter(self:load(call.script),call.offset,call.args,false,action)
    else self:resume(action.done,result or 0)end
  elseif action.kind=='event_return'then
    assert(#self.frames==action.depth,'event callback changed suspended frame ownership')
    self.waiting=action.waiting
  elseif action.kind=='host'then
    assert(self.resume_action,'host continuation provider missing')
    self.resume_action(self,action)
    self:resume(action.done,result)
  else self:fail('unknown continuation '..tostring(action.kind))end
end
function VM:begin(name)
  assert(#self.frames==0,'VM already started')
  local script=self:load(name)
  self.entry_script=script.name
  local init
  for _,fn in ipairs(script.functions) do if fn[1]==2981858012 then init=fn[2] end end
  self.status='running'
  if init then
    self:enter(script,init,{},false,{kind='after_init',script=script.name})
  else self:enter(script,script.main,{},false) end
end
function VM:load_library(name,wants_result)
  local script=self:load(name)
  if self.libraries[script.name] then
    if wants_result then self:push(0) end
    return
  end
  self.libraries[script.name]=true
  self:enter(script,script.main,{},wants_result)
end
function VM:call(hash,args,wants_result,on_return)
  hash=unsigned(hash)
  local fn=self:resolve(hash)
  if not fn then self:fail(string.format('unresolved function %08x',hash)) end
  self:enter(fn.script,fn.offset,args,wants_result,on_return)
end
function VM:finish()
  local frame=self:frame()
  if #self.stack<frame.stack_base+1 then
    self:fail('return requires a value above frame base')
  end
  local result,result_type=self:pop_typed()
  if frame.return_value~=nil then result,result_type=frame.return_value,frame.return_type end
  for i=#(frame.handlers or {}),1,-1 do
    if frame.handlers[i].kind=='destructor' then
      frame.return_value=result
      frame.return_type=result_type
      local offset=frame.handlers[i].offset
      -- Original 0x43D2B8 consumes the cleanup slot before entering it.
      table.remove(frame.handlers,i)
      self:jump(offset)
      return
    end
  end
  self:truncate_stack(frame.stack_base)
  table.remove(self.frames)
  if frame.wants_result then self:push(result,result_type) end
  if frame.on_return then self:resume(frame.on_return,result) end
  if #self.frames==0 then
    local thread=self.threads[self.thread_id]
    if thread.at_end then
      local target=thread.at_end;thread.at_end=nil
      thread.terminate_requested=nil;thread.unwind_depth=nil
      thread.ending_callback=true
      self:enter(self:load(target.script),target.offset,{},false)
      return
    end
    thread.ending_callback=nil;thread.finished=true
    if self.thread_id==1 and not self.exiting_context then
      if self.restart_on_return then self:exit_context(5)
      else self.status='finished'end
    end
    for _,t in pairs(self.threads)do if t.owner==self.thread_id then self:terminate(t.id)end end
  end
end
function VM:exec_at(script,offset,args,preserve_globals)
  self:save_thread()
  self.frames,self.stack,self.stack_types,self.waiting={},{},{},nil
  local thread=self.threads[self.thread_id]
  thread.frames,thread.stack,thread.waiting=self.frames,self.stack,nil
  thread.stack_types=self.stack_types
  if not preserve_globals then thread.globals={}end
  thread.areas={};thread.area_defaults={};thread.fontout=nil
  thread.terminate_requested=nil;thread.unwind_depth=nil;thread.finished=nil
  self.globals[2]=thread.globals
  self.context_generation=self.context_generation+1
  self:enter(script,offset,args,false)
end
function VM:exec(script,args)
  self:exec_at(script,script.main,args,false)
end
function VM:exec_function(hash,args)
  -- The native context is cleared before resolving this hash. Its PC is
  -- therefore zero: only loaded-script order supplies the fallback target.
  local fn=self.functions[unsigned(hash)]
  if not fn then self:fail(string.format('unresolved function %08x',unsigned(hash)))end
  self:exec_at(fn.script,fn.offset,args,true)
end
function VM:exit_context(reason)
  self.exiting_context=true
  self.reset_reason=reason or 5
  for _,id in ipairs(self.thread_order)do self:terminate(id)end
end
function VM:restore_context()
  self.frames,self.stack,self.stack_types,self.waiting={},{},{},nil
  self.thread_id=1;self.next_thread=2;self.schedule_cursor=0;self.thread_order={1}
  self.threads={[1]={id=1,frames=self.frames,stack=self.stack,stack_types=self.stack_types,globals={}}}
  self.globals[2]=self.threads[1].globals
  -- Original 0x44E7A0 -> 0x44F2D0 discards volatile banks before restart.
  -- Persistent variables (bank 0) retain progress and player preferences.
  self.globals[1]={}
  self.exiting_context=nil;self.status='running'
  self.context_generation=self.context_generation+1
  local script=self:load(assert(self.entry_script,'entry script missing'))
  local next_step={kind='enter',script=script.name,offset=script.main}
  if self.after_init then self.after_init(self,next_step,self.reset_reason or 5)
  else self:resume(next_step)end
  self.reset_reason=nil
end
function VM:signal(kind,forward_only)
  for depth=#self.frames,1,-1 do
    local frame=self.frames[depth]
    for i=#(frame.handlers or {}),1,-1 do
      local hook=frame.handlers[i]
      local next_row=frame.script.code[frame.index]
      if (hook.kind==kind or hook.kind=='any')and
         (not forward_only or(next_row and hook.offset>next_row[1]))then
        self.waiting=nil
        self.threads[self.thread_id].pending_input={depth=depth,offset=hook.offset,
          stack_size=hook.stack_size or frame.stack_base}
        -- Original scheduler clears all three mouse slots on selection.
        clear_click_handlers(frame)
        return true
      end
    end
  end
  return false
end
function VM:unwind_input()
  local thread=self.threads[self.thread_id]
  local pending=assert(thread.pending_input)
  if #self.frames>pending.depth then self:unwind();return end
  assert(#self.frames==pending.depth,'input handler frame disappeared')
  self:truncate_stack(pending.stack_size)
  thread.pending_input=nil;thread.unwind_depth=nil;self.waiting=nil
  self:jump(pending.offset)
end
function VM:export_state()
  assert(not self.modal_dialog and not self.external_checker,
    'cannot checkpoint while an external owner request is pending')
  self:save_thread()
  local scripts={}
  for _,name in ipairs(self.load_order)do
    scripts[#scripts+1]={name=name,sha256=self.scripts[name].source_sha256}
  end
  local function export_threads(source)
  local threads={}
  for id,thread in pairs(source)do
    if not thread.finished or id==self.thread_id or id==1 then
    local saved={}
    for k,v in pairs(thread)do if k~='frames'then saved[k]=v end end
    saved.frames={}
    for i,frame in ipairs(thread.frames)do
      assert(type(frame.on_return)~='function','non-serializable VM continuation')
      local copy={}
      for k,v in pairs(frame)do if k~='script'and k~='index'then copy[k]=v end end
      local row=assert(frame.script.code[frame.index],'checkpoint outside bytecode')
      copy.script=frame.script.name;copy.offset=row[1]
      saved.frames[i]=copy
    end
    threads[id]=saved
    end
  end
  return threads
  end
  local threads=export_threads(self.threads)
  local thread_order={};local cursor=0
  for position,id in ipairs(self.thread_order)do
    if threads[id]then
      thread_order[#thread_order+1]=id
      if position<=self.schedule_cursor then cursor=#thread_order end
    end
  end
  return {schema=1,scripts=scripts,threads=threads,thread_id=self.thread_id,
    thread_order=thread_order,next_thread=self.next_thread,schedule_cursor=cursor,
    globals=self.globals,libraries=self.libraries,status=self.status,steps=self.steps,
    entry_script=self.entry_script,exiting_context=self.exiting_context,reset_reason=self.reset_reason,
    numeric_profile=self.numeric_profile,array_3d_legacy_stride=self.array_3d_legacy_stride}
end
function VM:import_state(data)
  assert(data.schema==1 and data.threads[data.thread_id],'invalid VM checkpoint')
  for _,thread in pairs(data.threads)do
    local wait=thread.waiting
    assert(not wait or(wait.kind~='dialog'and wait.kind~='external_checker'),
      'checkpoint contains an unavailable external owner request')
  end
  assert(not data.numeric_profile or data.numeric_profile==self.numeric_profile,
    'checkpoint engine numeric profile changed')
  assert(data.array_3d_legacy_stride==nil or
    data.array_3d_legacy_stride==self.array_3d_legacy_stride,'checkpoint array indexing profile changed')
  self.scripts={};self.functions={};self.duplicate_functions={};self.load_order={}
  for _,record in ipairs(data.scripts)do
    local script=self:load(record.name)
    assert(script.source_sha256==record.sha256,'checkpoint script changed: '..record.name)
  end
  local function import_threads(threads)
  for _,thread in pairs(threads)do
    for _,frame in ipairs(thread.frames)do
      local script=assert(self.scripts[frame.script])
      frame.index=assert(script.positions[frame.offset],'checkpoint source position missing')
      frame.script=script;frame.offset=nil
    end
  end
  end
  import_threads(data.threads)
  self.threads=data.threads;self.thread_order=data.thread_order
  self.thread_id,self.next_thread,self.schedule_cursor=data.thread_id,data.next_thread,data.schedule_cursor
  self.globals,self.libraries=data.globals,data.libraries
  self.status,self.steps=data.status,data.steps
  self.entry_script=data.entry_script or self.entry_script;self.exiting_context=data.exiting_context
  self.reset_reason=data.reset_reason
  self.context_generation=self.context_generation+1
  self.problem=nil;self.trace={};self.current_offset=nil;self.last_syscall=nil
  local thread=self.threads[self.thread_id]
  self.frames,self.stack,self.waiting=thread.frames,thread.stack,thread.waiting
  thread.stack_types=thread.stack_types or {};self.stack_types=thread.stack_types
  self.globals[2]=thread.globals
end
function VM:variable(flags,hash,offset)
  local scope=math.floor(flags/32)%8
  local vartype=math.floor(flags/256)%8
  if vartype>5 then self:fail('unsupported variable type '..vartype) end
  if scope==3 then
    local frame=self:frame()
    if offset==-1 and hash==2802105789 then return frame.system,hash,vartype end
    if offset<=-2 then
      local index=-offset-1
      if index>#frame.args then
        local inherited=(frame.argument_tail or{})[index-#frame.args]
        if inherited then return inherited.bank,inherited.key,vartype end
      end
      return frame.args,index,vartype
    end
    if offset<0 then self:fail('invalid local offset '..offset) end
    return frame.locals,offset,vartype
  end
  if scope>2 then self:fail('invalid variable scope '..scope) end
  return self.globals[scope],hash,vartype
end
function VM:slot(flags,hash,offset,element)
  local bank,key,vartype=self:variable(flags,hash,offset)
  if bank[key]==nil then
    -- Parameters and allocated locals must exist. Global defaults are typed.
    if math.floor(flags/32)%8==3 then self:fail('unallocated local/argument '..offset) end
    bank[key]=default(element and vartype+3 or vartype)
  end
  if element then
    local array=bank[key]
    if type(array)~='table' or array.kind~='array' then self:fail('array variable required') end
    local dimensions=math.floor(flags/2048)%4
    if dimensions==0 or #array.dims~=dimensions then self:fail('array dimensionality mismatch') end
    local indices={}
    for i=dimensions,1,-1 do indices[i]=self:pop() end
    local flat=0
    for i=1,dimensions do
      local index=indices[i]
      if type(index)~='number' or index%1~=0 then self:fail('nonintegral array index')end
      if array.dims[i]<=0 then self:fail('array dimension is empty')end
      -- Original 0x43A590 wraps every coordinate by its dimension, adding
      -- the dimension for negative remainders. -1 addresses the last item.
      index=index%array.dims[i]
      if self.array_3d_legacy_stride and dimensions==3 then
        flat=flat+index*(i==1 and array.dims[2]or i==2 and array.dims[3]or 1)
      else flat=flat*array.dims[i]+index end
    end
    bank,key,vartype=array.values,flat,array.element_type
    if bank[key]==nil then bank[key]=default(vartype) end
  end
  return bank,key,vartype
end
function VM:jump(offset)
  local index=self:frame().script.positions[offset]
  if not index then self:fail('jump target missing: '..tostring(offset)) end
  self:frame().index=index
end
function VM:binary(op,a,b,t)
  local result
  local comparison=({cle=true,clt=true,cge=true,cgt=true,ceq=true,cne=true})[op]
  if t=='r'and comparison then
    -- Newer SSE builds compare after converting both operands to binary32;
    -- older x87 builds retain integer precision and treat unordered flags
    -- as true for <=, < and ==. These are observable engine differences.
    if self.numeric_profile=='sse32'then a,b=f32(a),f32(b)end
    if a~=a or b~=b then
      if self.numeric_profile=='x87'then
        return op=='clt'and -1 or(op=='cle'or op=='ceq')and 1 or 0
      end
      return op=='cne'and 1 or 0
    end
  end
  if t=='s' and ({cle=true,clt=true,cge=true,cgt=true,ceq=true,cne=true})[op] then
    local compare=self.string_ops and self.string_ops.compare
    if compare then a,b=compare(a,b),0 else a,b=a:lower(),b:lower() end
  end
  if op=='add' then result=t=='s' and (a..b) or a+b
  elseif op=='sub' then result=a-b
  elseif op=='mul' then
    if t=='i' then
      -- Avoid loss of low bits in Lua doubles on 32x32 multiplication.
      local ua,ub=unsigned(a),unsigned(b)
      result=(ua%65536)*(ub%65536)+
        (((math.floor(ua/65536)*(ub%65536)+(ua%65536)*math.floor(ub/65536))%65536)*65536)
    else result=a*b end
  elseif op=='div' or op=='rem' then
    if t=='i'and a==-2147483648 and b==-1 then self:fail('signed division overflow')end
    if b==0 then
      if op=='rem'then return 0 end
      if t=='i'then return signed(a*10000)end
    end
    -- Original real division substitutes 0.00001 for a zero divisor.
    -- The x87 comparison also takes this path for a NaN divisor.
    if t=='r'and(b==0 or(self.numeric_profile=='x87'and b~=b))then b=0.00001 end
    local quotient=a/b
    if op=='rem' then result=a-trunc(quotient)*b
    else result=t=='i' and trunc(quotient) or quotient end
  elseif op=='cle' then return bool(a<=b)
  elseif op=='clt' then return (t=='r'and -1 or 1)*bool(a<b)
  elseif op=='cge' then return (t~='s'and -1 or 1)*bool(a>=b)
  elseif op=='cgt' then return bool(a>b)
  elseif op=='ceq' or op=='cne' then
    if type(a)=='table' or type(b)=='table' then self:fail('array comparison semantics unverified') end
    return bool((a==b)==(op=='ceq'))
  elseif op=='andl' then return bool(truth(a) and truth(b))
  elseif op=='orl' then return bool(truth(a) or truth(b))
  elseif op=='and' or op=='or' or op=='xor' then return bitop(a,b,op)
  elseif op=='shl' then
    local shift=unsigned(b)%32
    result=(unsigned(a)%2^(32-shift))*2^shift
  elseif op=='shr' then return math.floor(signed(a)/2^(unsigned(b)%32))
  else self:fail('unsupported operator '..op) end
  if t=='i' then return signed(result) end
  if t=='r' then return f32(result) end
  return result
end
function VM:integer(value)
  -- cvttss2si returns INT_MIN for non-finite/out-of-range input. Older
  -- _ftol2 truncates to signed 64 bits, then retains the low 32 bits.
  if self.numeric_profile=='x87'then
    if value~=value or value>=9223372036854775808 or value< -9223372036854775808 then return 0 end
  elseif value~=value or value>=2147483648 or value< -2147483648 then return -2147483648 end
  return signed(trunc(value))
end
function VM:step()
  local frame=self:frame()
  if not frame then self.status='finished';return false end
  local row=frame.script.code[frame.index]
  if not row then self:fail('execution beyond script') end
  local spec=self.opcodes[row[2]]
  if not spec then self:fail('opcode description missing') end
  if self.before_instruction and self.before_instruction(self,spec[1],row)then return false end
  self.current_offset=row[1]
  frame.index=frame.index+1
  local name=spec[1]
  self.steps=self.steps+1
  self.opcode_counts[name]=(self.opcode_counts[name] or 0)+1
  self.trace[#self.trace+1]={script=frame.script.name,offset=row[1],opcode=name}
  if #self.trace>self.trace_limit then table.remove(self.trace,1) end
  if name=='line' then
    frame.line=row[3]>=32768 and row[3]-65536 or row[3]
    if self.line_changed then self.line_changed(self,frame)end
  elseif name=='argcheck' then
    if #frame.args<#row[3] then self:fail('not enough function arguments') end
    for i,t in ipairs(row[3]) do
      local actual=frame.arg_types and frame.arg_types[i]or value_type(frame.args[i])
      if actual~=t then
        self:fail(string.format('argument type mismatch at %d: expected %d, got %d',i,t,actual))
      end
    end
  elseif name=='alloca' then
    for i,t in ipairs(row[3]) do frame.locals[i-1]=default(t) end
  elseif name=='ldc.i' or name=='ldc.r' or name=='ldstr' then
    self:push(row[3],name=='ldc.r'and 1 or name=='ldstr'and 2 or 0)
  elseif name=='pop' then self:pop()
  elseif name=='ret' then self:finish()
  elseif name=='call' or name=='callp' then
    self:call(row[3],self:arguments(row[5]),name=='call')
  elseif name=='syscall' or name=='syscallp' then
    local args=self:arguments(row[4])
    local key=string.format('%08x/%d/%s',row[3],row[4],name)
    self.syscall_counts[key]=(self.syscall_counts[key] or 0)+1
    self.last_syscall={hash=row[3],args=args,opcode=name}
    local value,deferred,kind=self.syscall(self,row[3],args,name=='syscall')
    if name=='syscall' and not deferred then self:push(value,kind) end
  elseif name=='br' then self:jump(row[3])
  elseif name=='brtrue' or name=='brfalse' then
    local value,kind=self:pop_typed()
    local condition=truth(value)
    if kind==1 and value~=value and self.numeric_profile=='x87'then condition=false end
    if condition==(name=='brtrue') then self:jump(row[3]) end
  elseif name=='switch' then
    local index=self:pop()
    if index%1~=0 then self:fail('nonintegral switch index') end
    if #row[3]==0 then self:fail('empty switch table')end
    -- The final table entry is the original default, including negative
    -- selectors. It is not a fall-through after the jump table.
    if index<0 or index>=#row[3]then index=#row[3]-1 end
    self:jump(row[3][index+1])
  elseif name=='br.case' then
    local value,kind=self:pop_typed()
    self.globals[2][0x11f91fd3]=value;self.threads[self.thread_id].case_type=kind
    self:jump(row[3])
  elseif name=='bne.case' or name=='bge.case' or name=='ble.case' or
         name=='blt.case' or name=='bgt.case' then
    local value=self.stack[#self.stack];local a=self.globals[2][0x11f91fd3]
    if a==nil then self:fail('case selector missing') end
    if value==nil then self:fail('case comparison operand missing')end
    local kind=self.stack_types[#self.stack]or value_type(value)
    local operation=({['bne.case']='cne',['bge.case']='cge',['ble.case']='cle',
      ['blt.case']='clt',['bgt.case']='cgt'})[name]
    local selector_type=self.threads[self.thread_id].case_type or value_type(a)
    if kind<=1 and selector_type<=1 then a=raw_number(a,selector_type,kind)end
    if kind==2 and operation~='cne'and not self.case_string_relations then
      self:fail('original engine does not define string relational case instructions')
    end
    local yes
    if kind==1 and(a~=a or value~=value)then
      yes=self.numeric_profile=='sse32'or name=='bge.case'or name=='bgt.case'
    else yes=self:binary(operation,a,value,({'i','r','s'})[kind+1])~=0 end
    if yes then self:jump(row[3]) end
  elseif name=='ld' or name=='ldelem' then
    local flags=row[3];local bank,key,t=self:slot(flags,row[4],row[5],name=='ldelem')
    local value=bank[key];local modifier=flags%8
    if modifier~=0 then
      if modifier>4 then self:fail('invalid reserved load modifier '..modifier)end
      if t>1 then self:fail('unsupported nonnumeric load modifier')end
      local updated=value+((modifier==1 or modifier==3)and 1 or -1)
      updated=t==1 and f32(updated)or signed(updated)
      bank[key]=updated
      if math.floor(flags/32)%8==0 and self.persistent_changed then self.persistent_changed()end
      if modifier<=2 then value=updated end
    end
    local invert=math.floor(flags/8)%4
    if invert==1 then value=t==1 and f32(-value) or signed(-value)
    elseif invert==2 and t==0 then value=bool(not truth(value))
    elseif invert==3 and t==0 then value=signed(4294967295-unsigned(value)) end
    local kind=t
    if math.floor(flags/32)%8==3 and row[5]<=-2 and frame.arg_types then
      kind=frame.arg_types[-row[5]-1]or kind
    end
    if t==0 and(invert==2 or invert==3)then kind=0 end
    self:push(value,kind)
  elseif name:match('^stelemp?%.') or name:match('^stp?%.') then
    local element=name:sub(1,6)=='stelem'
    local bank,key,t=self:slot(row[3],row[4],row[5],element)
    local value,source_type=self:pop_typed()
    local source_value=value
    local tail=name:match('^stp?%.(.*)') or name:match('^stelemp?%.(.*)')
    local operation
    if tail and not ({i=true,r=true,s=true,iarr=true,rarr=true,sarr=true})[tail] then
      operation=tail:match('^([%a]+)')
    end
    local expression
    if element then
      local operand=t<=1 and raw_number(value,source_type,t)or value
      if operation=='add'and t==1 then
        value=operand -- Original floating array-add opcode performs assignment.
      elseif operation=='mul'and t==1 then
        value=f32(bank[key]*f32(raw_number(source_value,source_type,0)))
      elseif operation=='div'and t==1 and operand==0 then
        value=f32(bank[key]*10000)
      elseif operation then value=self:binary(operation,bank[key],operand,({'i','r','s'})[t+1])
      else value=operand end
      -- Most array compound handlers leave the RHS slot untouched. Integer
      -- multiplication is the exception: it publishes its computed value.
      if operation=='mul'and t==0 then expression=raw_number(value,t,source_type)
      elseif operation=='add'and t==2 then expression=value
      else expression=source_value end
    elseif operation then
      local operand=t<=1 and raw_number(value,source_type,t)or value
      if operation=='rem'and operand==0 then value=bank[key];expression=0
      else
        value=self:binary(operation,bank[key],operand,({'i','r','s'})[t+1])
        expression=t<=1 and raw_number(value,t,source_type)or value
      end
    else
      if t==0 then value=self:integer(value) elseif t==1 then value=f32(value)end
      expression=value;source_type=t
    end
    bank[key]=value
    if math.floor(row[3]/32)%8==0 and self.persistent_changed then self.persistent_changed()end
    local discard=name:match('^stp%.') or name:match('^stelemp%.')
    if not discard then self:push(expression,source_type) end
  elseif name=='notl' then self:push(bool(not truth(self:pop())))
  elseif name=='not' then self:push(signed(4294967295-unsigned(self:pop())))
  elseif name=='neg.i' then self:push(signed(-self:pop()))
  elseif name=='neg.r' then self:push(f32(-self:pop()),1)
  elseif name=='conv.i' then self:push(self:integer(self:pop()))
  elseif name=='conv.r' then self:push(f32(self:pop()),1)
  elseif name:match('^ceq%.[irs]arr$')or name:match('^cne%.[irs]arr$')then
    -- Original 0x43BB90 / 0x43CE30 compare the array object addresses,
    -- release both operands and push an integer; contents are not compared.
    local b,a=self:pop(),self:pop()
    if type(a)~='table'or type(b)~='table'or a.kind~='array'or b.kind~='array'then
      self:fail('array comparison requires array operands')
    end
    self:push(bool((a==b)==(name:sub(1,3)=='ceq')))
  elseif name:match('^nop%.') then -- documented no-op opcodes only
  elseif name=='bsel.4' then
    -- Original 0x439E23 stores one destination in frame+0x20. It is
    -- separate from mouse hooks and from the return/destructor target.
    frame.branch4=row[3]
  elseif name=='bsel.jmp.4' then
    -- Original 0x4407E0 scans inward to outward, changes the first
    -- forward destination, and consumes that slot. An ancestor's PC is
    -- updated in place: its callee continues and returns normally.
    for depth=#self.frames,1,-1 do
      local target=self.frames[depth]
      local next_row=target.script.code[target.index]
      if target.branch4 and next_row and target.branch4>next_row[1]then
        target.index=assert(target.script.positions[target.branch4],'branch4 target missing')
        target.branch4=nil
        break
      end
    end
  elseif name:match('^bsel') then
    frame.handlers=frame.handlers or {}
    if name=='bsel.clr' then
      -- Original 0x43C810 zeros three mouse slots; repeated clears are valid.
      -- The independent return-cleanup slot must survive this instruction.
      clear_click_handlers(frame)
    else
      local kind=({['bsel.1']='left',['bsel.2']='right',['bsel.3']='middle',
        ['bsel.x']='any',['bsel.5']='destructor'})[name]
      if not kind then self:fail('unimplemented event opcode '..name) end
      if kind=='any' then
        for _,button in ipairs{'left','middle','right'}do
          set_handler(frame,button,row[3],#self.stack)
        end
      else set_handler(frame,kind,row[3],#self.stack)end
    end
  elseif name=='text' or name=='proc' or name=='ctrl' then
    if not self.text_ops then self:fail('text provider missing')end
    self.text_ops.execute(self,name,row[3])
  else
    local operation,t=name:match('^([%a]+)%.([irs])$')
    if not operation and ({rem=true,shr=true,shl=true,xor=true,andl=true,orl=true,
      ['and']=true,['or']=true})[name] then operation,t=name,'i' end
    if not operation then self:fail('unimplemented opcode '..name) end
    local b,a=self:pop(),self:pop()
    local comparison=({cle=true,clt=true,cge=true,cgt=true,ceq=true,cne=true})[operation]
    self:push(self:binary(operation,a,b,t),t=='r'and not comparison and 1 or nil)
  end
  return true
end
function VM:run(budget,deadline,clock)
  if self.status=='blocked' or self.status=='finished' then return self.status end
  self.host_budget_exhausted=false
  local consumed=0
  while consumed<budget do
    -- A native dialog suspends the complete interpreter while the owner
    -- host keeps rendering and servicing its asynchronous UI response.
    if self.modal_dialog or self.external_checker then return 'waiting'end
    -- Source threads may repeatedly yield against the same cached tick.
    -- Bound host CPU time as well as instruction count, preserving all PCs.
    if consumed>0 and deadline and clock()>=deadline then
      self.host_budget_exhausted=true;break
    end
    self:save_thread()
    local id
    for shift=1,#self.thread_order do
      local position=(self.schedule_cursor+shift-1)%#self.thread_order+1
      local candidate=self.thread_order[position]
      local t=self.threads[candidate]
      if t.waiting and t.waiting.kind=='yield'then
        t.waiting=nil
        if candidate==self.thread_id then self.waiting=nil end
      end
      if not t.finished and (#t.frames>0) and (not t.waiting or t.terminate_requested) then
        id=candidate;self.schedule_cursor=position;break
      end
    end
    if not id then
      if self.exiting_context then
        self:restore_context()
        if self.status=='finished'then return self.status end
      else self:select_thread(1);return 'waiting'end
    else
    self:select_thread(id)
    local generation=self.context_generation
    for quantum=1,math.min(256,budget-consumed)do
      if quantum>1 and quantum%32==1 and deadline and clock()>=deadline then break end
      if self.threads[id].terminate_requested then self:unwind()
      elseif self.threads[id].pending_input then self:unwind_input()
      else self:step()end
      consumed=consumed+1
      if generation~=self.context_generation or self.waiting or self.threads[id].finished or self.status=='finished' then break end
    end
    if self.status=='finished' then break end
    end
  end
  if self.status~='blocked' then self:select_thread(1) end
  return self.status
end
M.signed=signed;M.unsigned=unsigned;M.f32=f32
return M
