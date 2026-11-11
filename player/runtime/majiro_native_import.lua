-- Original Windows slots become portable checkpoints only after exact source
-- identities and every supported state field have been validated.
local M={}
local function integer(value,message)
  assert(type(value)=='number'and value%1==0,message);return value
end
local function unhex(value)
  assert(type(value)=='string'and #value%2==0 and not value:find('[^0-9a-f]'),'invalid native byte string')
  return(value:gsub('..',function(pair)return string.char(tonumber(pair,16))end))
end
local function signed(value)return value>=2147483648 and value-4294967296 or value end
local function digest(value)
  return type(value)=='string'and #value==64 and not value:find('[^0-9a-f]')
end
local function real(bits)
  local sign=bits>=2147483648 and-1 or 1;bits=bits%2147483648
  local exponent=math.floor(bits/8388608);local fraction=bits%8388608
  assert(exponent~=255,'nonfinite original number cannot enter portable checkpoint')
  if exponent==0 then return sign*math.ldexp(fraction,-149)end
  return sign*math.ldexp(1+fraction/8388608,exponent-127)
end
function M.new(options)
  local codec=options.modules('runtime/majiro_codec.lua')
  local objects={}
  local function decode(raw)
    assert(options.decode_bytes,'native import byte decoder unavailable')
    return options.decode_bytes(raw)
  end
  local function value(row)
    local tag=integer(row.native_type,'native value tag missing')
    if tag==0 then return integer(row.value,'native integer missing'),0 end
    if tag==1 then return real(assert(tonumber(row.value.float32_bits,16))),1 end
    local object=assert(row.value,'native object missing')
    local key=object.object_offset
    if key and objects[key]then return objects[key],tag end
    if tag==2 then
      local text=decode(unhex(object.bytes_hex))
      if key then objects[key]=text end
      return text,2
    end
    assert(tag>=3 and tag<=5 and type(object.dimensions)=='table','native array invalid')
    local array={kind='array',element_type=tag-3,dims={},values={}}
    if key then objects[key]=array end
    local count=1
    for i,dimension in ipairs(object.dimensions)do
      dimension=integer(dimension,'native array dimension invalid')
      assert(dimension>=0 and dimension<=1000000,'native array dimension out of bounds')
      array.dims[i]=dimension;count=count*dimension
    end
    assert(#array.dims==3 and count<=1000000 and #object.values==count,'native array payload mismatch')
    for i,item in ipairs(object.values)do
      if tag==3 then array.values[i-1]=item
      elseif tag==4 then array.values[i-1]=real(assert(tonumber(item.float32_bits,16)))
      else array.values[i-1]=decode(unhex(item.bytes_hex))end
    end
    return array,tag
  end
  local function identity(manifest,script)
    local record=assert(manifest.scripts[script.name:lower()],'native source script has no verified binding')
    assert(record.sha256==script.source_sha256,'native saved script identity differs: '..script.name)
    assert(record.version_identity==manifest.source_identity,'native script binding version differs')
    return record
  end
  local function validate_neutral(native)
    local header=unhex(native.header_bytes_hex)
    local c=native.count_fields_offset
    assert(native.format=='MajiroSavV1.103'or native.format=='MajiroSavV1.105','native story signature unsupported')
    assert((native.format=='MajiroSavV1.103'and c==0x90 and #header==0xa3e0)
      or(native.format=='MajiroSavV1.105'and c==0x210 and #header==0xa560),'native story profile mismatch')
    -- The only currently proven import state is an idle, fully neutral scene.
    -- No media/graphics/window/console field is synthesized or ignored.
    local begin=c+28
    local finish=native.picture_hash_offset
    assert(type(finish)=='number'and finish==#header-40000,'native header region mismatch')
    assert(header:sub(begin+1,finish):gsub('%z','')=='',
      'native scene/media header requires an unimplemented restore adapter')
    assert(header:sub(finish+1):gsub('%z','')=='',
      'native picture progress hashes require a verified resource-name mapping')
    return header
  end
  local api={}
  function api.plan(native,manifest,baseline,live_vm,system_native,readmarks_native)
    assert(type(manifest)=='table'and manifest.schema==1 and manifest.source_identity==options.source_identity,
      'native import requires an explicit matching source-version manifest')
    assert(digest(manifest.source_identity)and digest(manifest.native_exe_sha256),
      'native import original engine identity missing')
    assert(digest(manifest.save_sha256),'native import save digest missing')
    assert(native.source_sha256==manifest.save_sha256,'native import input bytes do not match verified save')
    assert(manifest.verified_same_script_version==true,'native save version has not been verified')
    validate_neutral(native)
    assert(#native.frames==1 and native.frame_depth==0,'nested native frames need parameter/local mapping')
    assert(type(baseline)=='table'and type(baseline.state)=='table','native baseline unavailable')
    local packet=codec.clone(baseline)
    packet.identity=options.source_identity;packet.schema=1
    packet.graphics={}
    -- The accepted source has no live scene. Start with provider defaults,
    -- then retain only process-wide configuration/progress. Copying the live
    -- scene would retain stale callbacks, open files, console page handles or
    -- queued media which the original slot never contained.
    assert(options.native_initial_state,'native provider defaults unavailable')
    packet.state=codec.clone(options.native_initial_state)
    for _,field in ipairs{'registry','preference_bindings','progress'}do
      packet.state[field]=codec.clone(baseline.state[field]or{})
    end
    for _,field in ipairs{'musicvolume','soundvolume','voicevolume','mojispeed','autospeed',
      'effectspeed','font_name','font_proportional','fullscreen','voice_setup_levels',
      'voice_musicdown','voice_musicdown_vol','talk_male','talk_female','nanido','game_speed'}do
      packet.state.settings[field]=codec.clone(baseline.state.settings[field])
    end
    packet.state.graphics_journal={};packet.state.reading_positions={}
    packet.state.reading_wait=false;packet.state.ui_waiting=false;packet.state.ui_areas={}
    packet.state.modal_stack={};packet.state.tiles={memory={}}
    packet.state.persist_dirty=true
    local frame=native.frames[1];local fields=frame.fields
    assert(#fields==10,'native frame fields missing')
    local name=decode(unhex(frame.script_bytes_hex))
    local isolated=options.modules('runtime/majiro_vm.lua').new{
      loader=live_vm.loader,opcodes=live_vm.opcodes,syscall=live_vm.syscall,
      numeric_profile=live_vm.numeric_profile,string_ops=live_vm.string_ops,text_ops=live_vm.text_ops,
      case_string_relations=live_vm.case_string_relations}
    local script=isolated:load(name);identity(manifest,script)
    local pc=integer(fields[1],'native PC missing')
    local position=assert(script.positions[pc],'native PC is not an actual MJO instruction boundary')
    assert(manifest.boundaries and manifest.boundaries[script.name:lower()]
      and manifest.boundaries[script.name:lower()][pc]=='idle_root',
      'native continuation has no verified frame-layout boundary')
    -- Minimal proven continuation: no argument base, no hidden result discard,
    -- no frame event pointers. Arbitrary stacks/locals will be added only with
    -- original storage-location evidence, never inferred from byte count.
    assert(fields[2]==0 and fields[4]==0,'native frame has unmapped parameter/return metadata')
    for i=6,10 do assert(fields[i]==0,'native frame event/branch handlers require restore adapter')end
    assert(#native.groups[3].common_value_view.variables==fields[3],'native stack count differs from frame')
    assert(#native.groups[3].common_value_view.variables==0,
      'native nonempty operand stack requires frame-local mapping')
    objects={}
    local globals={[0]=codec.clone(live_vm.globals[0]),[1]={},[2]={}}
    for group=1,2 do
      for _,row in ipairs(native.groups[group].common_value_view.variables)do
        local hash=assert(tonumber(row.hash,16),'native variable hash invalid')
        assert(globals[group][hash]==nil,'duplicate native variable hash')
        globals[group][hash]=value(row)
      end
    end
    local system_objects={}
    if system_native then
      objects=system_objects
      assert(system_native.format=='MajiroSysV1.100','native system signature unsupported')
      for _,row in ipairs(system_native.variables)do globals[0][assert(tonumber(row.hash,16))]=value(row)end
      assert(#(system_native.picture_basename_hashes or{})==0,
        'native picture unlock hashes require verified resource-name mapping')
    end
    local progress=packet.state.progress
    progress.pictures=progress.pictures or{};progress.read=progress.read or{}
    progress.lines=progress.lines or{};progress.scripts=progress.scripts or{}
    if readmarks_native then
      assert(readmarks_native.format=='Majiro native readmarks','native readmark format invalid')
      for _,record in ipairs(readmarks_native.records)do
        local file=decode(unhex(record.script_bytes_hex))
        local loaded=isolated:load(file);identity(manifest,loaded)
        assert(loaded.line_count==record.line_count,'native readmark script line count changed')
        local marked={}
        for _,line in ipairs(record.marked_line_ids)do marked[line]=true end
        local current_line=0
        for _,row in ipairs(loaded.code)do
          if row[2]==0x83a then current_line=row[3];if current_line>=32768 then current_line=current_line-65536 end end
          if marked[current_line]then
            progress.lines[loaded.source_sha256..':'..current_line]=true
            if row[2]==0x840 then progress.read[loaded.name..':'..row[1]]=true end
          end
        end
        progress.scripts[loaded.name:lower()]=true
      end
    end
    packet.state.progress=progress
    packet.vm={schema=1,scripts={{name=script.name,sha256=script.source_sha256}},
      threads={[1]={id=1,frames={{script=script.name,offset=pc,args={},locals={},arg_types={},
        system={[2802105789]=0},stack_base=0,wants_result=false,line=signed(fields[5])}},
        stack={},stack_types={},globals=globals[2]}},
      thread_id=1,thread_order={1},next_thread=2,schedule_cursor=0,globals=globals,libraries={},
      status='running',steps=0,entry_script=script.name,numeric_profile=live_vm.numeric_profile}
    packet.state.savepoint={label=decode(unhex(native.label_bytes_hex)),script=script.name,
      offset=pc,thread=1,source_sha256=script.source_sha256}
    packet.state.native_import={schema=1,save_sha256=manifest.save_sha256,
      native_exe_sha256=manifest.native_exe_sha256,source_identity=manifest.source_identity,
      source_format=native.format,scope='verified idle main frame; neutral scene; zero operand stack'}
    local check=options.modules('runtime/majiro_vm.lua').new{
      loader=live_vm.loader,opcodes=live_vm.opcodes,syscall=live_vm.syscall,
      numeric_profile=live_vm.numeric_profile,string_ops=live_vm.string_ops,text_ops=live_vm.text_ops,
      case_string_relations=live_vm.case_string_relations}
    check:import_state(codec.clone(packet.vm))
    return{schema=1,packet=packet,commands={},identity_verified=true,
      native_import_scope=packet.state.native_import.scope,persistent_globals=globals[0]}
  end
  return api
end
return M
