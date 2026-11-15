-- Native frame/stack layout is projected into explicit Lua frames. All source
-- positions and type allocations are checked before the live VM is changed.
local M={}
function M.plan(native,manifest,baseline,live_vm,options,decode,value,reset_objects,system_native,readmarks_native)
  local codec=options.modules('runtime/majiro_codec.lua')
  local packet=codec.clone(baseline)
  local header=(native.header_bytes_hex:gsub('..',function(pair)return string.char(tonumber(pair,16))end))
  local count=native.count_fields_offset
  local early=native.format=='MajiroSavV1.102'
  assert((early and count==0x90 and #header==0x9fd0)
    or(native.format=='MajiroSavV1.103'and count==0x90 and #header==0xa3e0)
    or((native.format=='MajiroSavV1.104'or native.format=='MajiroSavV1.105')
      and count==0x210 and #header==0xa560),'native story header layout differs')
  local function word(at)
    local a,b,c,d=header:byte(at+1,at+4)
    assert(d,'native header word truncated')
    return a+b*256+c*65536+d*16777216
  end
  local function text(at,bytes)
    local raw=header:sub(at+1,at+bytes)
    local ending=assert(raw:find('\0',1,true),'native media name is unterminated')
    return decode(raw:sub(1,ending-1))
  end
  local function script_name(hex)
    return decode((hex:gsub('..',function(pair)return string.char(tonumber(pair,16))end)))
  end
  local isolated=options.modules('runtime/majiro_vm.lua').new{
    loader=live_vm.loader,opcodes=live_vm.opcodes,syscall=live_vm.syscall,
    numeric_profile=live_vm.numeric_profile,string_ops=live_vm.string_ops,text_ops=live_vm.text_ops,
    array_3d_legacy_stride=live_vm.array_3d_legacy_stride,
    case_string_relations=live_vm.case_string_relations}
  local sources,loaded={},{}
  local function load(name)
    local script=isolated:load(name)
    local binding=assert(manifest.scripts[script.name:lower()],'native source binding missing: '..name)
    assert(binding.sha256==script.source_sha256 and binding.version_identity==manifest.source_identity,
      'native saved script source changed: '..name)
    if not loaded[script.name]then
      loaded[script.name]=true;sources[#sources+1]={name=script.name,sha256=script.source_sha256}
    end
    return script
  end
  -- Preserve the source libraries and event registrations initialized by this
  -- exact game. HOT_RESET is the original scene reconstruction boundary.
  for _,record in ipairs(baseline.vm.scripts)do load(record.name)end
  -- Windows serializes process-local page/sprite pointers as integers. A
  -- verified source manifest binds their global assignment sites to resources
  -- rebuilt by this process; copying those old pointers would poison HOT_RESET.
  local replacements={}
  for _,binding in ipairs(manifest.resource_globals or{})do
    local hash=assert(tonumber(binding.hash,16),'invalid native resource global')
    local owner=load(binding.script)
    local row=owner.code[assert(owner.positions[binding.offset],'native resource assignment moved')]
    assert(row[2]==0x1b0 and math.floor(row[3]/32)%8==0 and row[4]==hash,
      'native resource binding is not the verified persistent assignment')
    local current=live_vm.globals[0][hash]
    local resources=({page=baseline.state.pages,sprite=baseline.state.sprites})[binding.kind]
    assert(resources and resources[current]and current==binding.current_value,
      'native resource binding no longer owns a live resource')
    assert(type(binding.native_value)=='number'and binding.native_value%1==0,
      'native resource pointer invalid')
    local found=false
    for _,saved in ipairs(system_native and system_native.variables or{})do
      if tonumber(saved.hash,16)==hash then
        assert(saved.native_type==0 and saved.value==binding.native_value,
          'native resource pointer differs from verified system save')
        found=true;break
      end
    end
    assert(found,'native resource global absent from system save')
    assert(replacements[binding.native_value]==nil or replacements[binding.native_value]==current,
      'native resource pointer has conflicting bindings')
    replacements[binding.native_value]=current
  end
  local function imported(row)
    local result,tag=value(row)
    if tag==0 then result=replacements[result]or result
    elseif tag==3 then
      for key,item in pairs(result.values)do result.values[key]=replacements[item]or item end
    end
    return result,tag
  end
  local raw=native.groups[3].common_value_view.variables
  local slots,tags={},{}
  for i,row in ipairs(raw)do slots[i],tags[i]=imported(row)end
  local globals={[0]=codec.clone(live_vm.globals[0]),[1]={},[2]={}}
  for group=1,2 do
    for _,row in ipairs(native.groups[group].common_value_view.variables)do
      local hash=assert(tonumber(row.hash,16),'invalid native variable hash')
      assert(globals[group][hash]==nil,'duplicate native variable')
      globals[group][hash]=imported(row)
    end
  end
  local frames,stack,stack_types={},{},{}
  assert(#native.frames==native.frame_depth+1 and #native.frames<=1001,'native frame count differs')
  local function layout(script,pc)
    local entry=script.main<=pc and script.main or -1
    for _,fn in ipairs(script.functions)do if fn[2]<=pc and fn[2]>entry then entry=fn[2]end end
    assert(entry>=0 and script.positions[entry],'native frame has no function boundary')
    local allocated,declared={},{}
    for i=script.positions[entry],#script.code do
      local row=script.code[i]
      if row[2]==0x836 then declared=row[3]
      elseif row[2]==0x829 then allocated=row[3];break
      elseif row[2]~=0x83a and row[2]~=0x191 then break end
    end
    return allocated,declared
  end
  for index,native_frame in ipairs(native.frames)do
    local f=native_frame.fields
    assert(#f==10,'native frame fields missing')
    local script=load(script_name(native_frame.script_bytes_hex))
    local pc=f[1];local position=assert(script.positions[pc],'native instruction position changed')
    local base,top=f[2],f[3]
    assert(f[4]==0 or f[4]==1,'invalid native return-discard flag')
    assert(base%1==0 and top%1==0 and base>=0 and top>=base and top<=#slots,'native frame stack range invalid')
    local locals,declared=layout(script,pc)
    assert(base+#locals<=top,'native local allocation exceeds saved frame')
    local args,arg_types={},{}
    local argc=base>0 and slots[base]or 0
    assert(type(argc)=='number'and argc%1==0 and argc>=0 and argc<=base-1 or base==0,
      'native frame argument marker invalid')
    if base==0 then argc=0 end
    if index>1 then
      assert(base==native.frames[index-1].fields[3]+argc+1,'native caller argument boundary differs')
    end
    for i=1,argc do args[i],arg_types[i]=slots[base-i],tags[base-i]end
    assert(#args>=#declared,'native frame argument count differs from source')
    for i,tag in ipairs(declared)do assert(arg_types[i]==tag,'native frame argument type differs from source')end
    local frame={script=script.name,offset=pc,args=args,arg_types=arg_types,locals={},
      system={[2802105789]=argc},stack_base=#stack,wants_result=index>1 and native.frames[index-1].fields[4]==0,
      line=f[5]>=2147483648 and f[5]-4294967296 or f[5],argument_tail={}}
    for i,tag in ipairs(locals)do
      assert(tags[base+i]==tag,'native local type differs from source')
      frame.locals[i-1]=slots[base+i]
    end
    for i=base+#locals+1,top do stack[#stack+1]=slots[i];stack_types[#stack]=tags[i]end
    for slot=6,10 do
      local target=f[slot]
      if target~=0 then
        assert(script.positions[target],'native event target changed')
        if slot==9 then frame.branch4=target
        else
          frame.handlers=frame.handlers or{}
          frame.handlers[#frame.handlers+1]={kind=({[6]='left',[7]='right',[8]='middle',[10]='destructor'})[slot],
            offset=target,stack_size=#stack}
        end
      end
    end
    local parent=frames[index-1]
    if parent then
      for i=frame.stack_base,parent.stack_base+1,-1 do
        frame.argument_tail[#frame.argument_tail+1]={bank=stack,key=i}
      end
      local keys={};for key in pairs(parent.locals)do keys[#keys+1]=key end
      table.sort(keys,function(a,b)return a>b end)
      for _,key in ipairs(keys)do frame.argument_tail[#frame.argument_tail+1]={bank=parent.locals,key=key}end
      frame.argument_tail[#frame.argument_tail+1]={bank=parent.system,key=2802105789}
      for i=1,#parent.args do frame.argument_tail[#frame.argument_tail+1]={bank=parent.args,key=i}end
      for _,slot in ipairs(parent.argument_tail)do frame.argument_tail[#frame.argument_tail+1]=slot end
    end
    frames[index]=frame
  end
  assert(native.frames[#native.frames].fields[3]==#slots,'native top stack differs from serialized count')
  -- Original save_point captures the PC at the syscall. Its argument(s) are
  -- still in the native stack; consume them and resume after that instruction.
  local top=frames[#frames];local script=load(top.script);local row=script.code[script.positions[top.offset]]
  if(row[2]==0x834 or row[2]==0x835)and row[3]==0xf8004c93 then
    local argc=row[4]
    assert(#stack-top.stack_base>=argc,'native save_point arguments missing')
    for _=1,argc do stack_types[#stack]=nil;table.remove(stack)end
    if row[2]==0x834 then stack[#stack+1]=0;stack_types[#stack]=0 end
    top.offset=assert(script.code[script.positions[top.offset]+1],'native save_point ends outside bytecode')[1]
  end
  packet.vm={schema=1,scripts=sources,threads={[1]={id=1,frames=frames,stack=stack,stack_types=stack_types,
    globals=globals[2]}},thread_id=1,thread_order={1},next_thread=2,schedule_cursor=0,
    globals=globals,libraries=codec.clone(baseline.vm.libraries),status='running',steps=0,
    entry_script=frames[1].script,numeric_profile=live_vm.numeric_profile,
    array_3d_legacy_stride=live_vm.array_3d_legacy_stride}
  packet.identity=options.source_identity
  packet.state.input.buttons=0;packet.state.input.held_buttons=0
  packet.state.input.pressed={};packet.state.input.down={}
  packet.state.reading_wait=false;packet.state.reading_context=nil;packet.state.ui_waiting=false
  packet.state.ui_areas={};packet.state.active_ui_script=nil;packet.state.chapter_menu_active=nil
  -- Native visual reset hides retained initialized sprites; it does not free
  -- the game's console/frontbuffer allocations before HOT_RESET rebuilds.
  for _,sprite in pairs(packet.state.sprites)do
    sprite.x,sprite.y=100000,100000
    sprite.move=nil;sprite.fade=nil;sprite.animation=nil;sprite.alpha_sequence=nil
    sprite.transform_sequences=nil;sprite.tweens={};sprite.hidden=0
  end
  packet.state.media=codec.clone(options.native_initial_state.media)
  if system_native then
    assert(system_native.format=='MajiroSysV1.100'and
      system_native.source_sha256==manifest.system_save_sha256,'native system save identity differs')
    reset_objects()
    for _,row in ipairs(system_native.variables)do
      globals[0][assert(tonumber(row.hash,16),'invalid native system hash')]=imported(row)
    end
    packet.state.progress.native_pictures=packet.state.progress.native_pictures or{}
    for _,hash in ipairs(system_native.picture_basename_hashes or{})do
      packet.state.progress.native_pictures[assert(tonumber(hash,16),'invalid native picture hash')]=true
    end
  end
  if readmarks_native then
    assert(readmarks_native.format=='Majiro native readmarks'and
      readmarks_native.source_sha256==manifest.readmarks_sha256,'native readmark identity differs')
    local progress=packet.state.progress
    progress.lines=progress.lines or{};progress.read=progress.read or{};progress.scripts=progress.scripts or{}
    for _,record in ipairs(readmarks_native.records)do
      local script=load(script_name(record.script_bytes_hex))
      assert(script.line_count==record.line_count,'native readmark source line count differs')
      local marked={};for _,line in ipairs(record.marked_line_ids)do marked[line]=true end
      local line=0
      for _,row in ipairs(script.code)do
        if row[2]==0x83a then line=row[3]>=32768 and row[3]-65536 or row[3]end
        if marked[line]then
          progress.lines[script.source_sha256..':'..line]=true
          if row[2]==0x840 then progress.read[script.name..':'..row[1]]=true end
        end
      end
      progress.scripts[script.name:lower()]=true
    end
  end
  -- Header media fields are fixed by native V1.103/V1.105 capture evidence.
  local audio=count+28
  local requests={}
  local first,second=text(audio,256),text(audio+256,256)
  if first~=''then requests[#requests+1]={channel='music',first=first,second=second,
    gain=word(audio+(early and 0x200 or 0x600)),loop=word(audio+(early and 0x208 or 0x608))}end
  for i=0,early and -1 or 3 do
    local begin=audio+0x200+i*0x100
    first,second=text(begin,128),text(begin+128,128)
    if first~=''then requests[#requests+1]={channel=i==0 and'sound'or'sound'..(i+1),
      first=first,second=second,gain=word(audio+0x688+i*4),loop=1}end
  end
  packet.state.native_import={schema=1,save_sha256=manifest.save_sha256,
    native_exe_sha256=manifest.native_exe_sha256,source_format=native.format,
    serialized_header=native.header_bytes_hex,
    scope='verified source frames, typed native stack and original HOT_RESET reconstruction'}
  packet.state.savepoint={label=decode((native.label_bytes_hex:gsub('..',function(pair)
    return string.char(tonumber(pair,16))end))),script=top.script,offset=top.offset,thread=1,
    source_sha256=script.source_sha256}
  return{schema=1,packet=packet,commands=packet.graphics,identity_verified=true,
    native_import_scope=packet.state.native_import.scope,persistent_globals=globals[0],
    native_rebuild={text_first_render=word(audio+(early and 0x2e0 or 0x6f0)),audio=requests}}
end
return M
