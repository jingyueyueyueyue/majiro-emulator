-- Upgrade only recognized same-game identities; old indexes remain immutable.
local M={}
function M.new(store,host,json,resources,validate)
  local codec=store.codec
  local accepted={[store.identity]=true}
  local response=host('save_legacy_identity')
  local previous=response:match('^OK ([0-9a-f]+)$')
  assert(previous and #previous==64,'legacy source identity missing')
  if resources.text then previous=store.digest(previous..resources.text.identity)end
  accepted[previous]=true
  local api={}
  function api.accepts(value)return accepted[value]==true end
  local function read(root,name)
    local result=host('save_legacy_read '..root..' '..name)
    if result=='OK absent'then return nil end
    assert(result:sub(1,8)=='OK data\n','legacy save read failed')
    return result:sub(9)
  end
  local function object(root,descriptor)
    assert(type(descriptor)=='table'and type(descriptor.parts)=='table'
      and type(descriptor.bytes)=='number'and descriptor.bytes>=0
      and descriptor.bytes<=32*1024*1024,'invalid legacy save object')
    local pieces,size={},0
    for _,part in ipairs(descriptor.parts)do
      assert(type(part)=='string'and #part==64 and not part:find('[^0-9a-f]'),
        'invalid legacy object key')
      local data=assert(read(root,part..'.mjb'),'legacy object missing')
      assert(store.digest(data)==part,'legacy object integrity failure')
      size=size+#data;assert(size<=descriptor.bytes,'legacy object exceeds size')
      pieces[#pieces+1]=data
    end
    assert(size==descriptor.bytes,'legacy object is truncated')
    return table.concat(pieces)
  end
  function api.recover()
    local value=host('save_legacy_catalog')
    assert(value:sub(1,8)=='OK data\n','legacy catalog unavailable')
    local sources=json.decode(value:sub(9))
    local index=store:load_index()
    local changed=false
    local recovered,rejected,conflicts=0,0,0
    local had_slots=next(index.slots)~=nil
    local profile_recovered=false
    index.recovered=index.recovered or{}
    for _,source in ipairs(sources)do
      if not(source.root==0 and source.name==store:index_name())then
        local ok,problem=pcall(function()
          local encoded=assert(read(source.root,source.name),'legacy index missing')
          local receipt=store.digest(source.root..'/'..source.name..'\n'..encoded)
          if index.recovered[receipt]then return end
          local old=codec.decode(object(source.root,codec.decode(encoded)))
          if not api.accepts(old.identity)then return end
          assert(old.schema==1 and type(old.slots)=='table','invalid legacy index')
          local candidate=codec.clone(index)
          local imported=0
          local all_valid=true
          for slot,record in pairs(old.slots)do
            if candidate.slots[slot]then
              conflicts=conflicts+1
            else
              local valid,why=pcall(function()
                local packet=codec.decode(object(source.root,record.snapshot))
                assert(packet.identity==old.identity and packet.schema==1,'legacy checkpoint identity differs')
                local copy=codec.clone(record)
                local graphics,commands={},{}
                for _,entry in ipairs(record.graphics or{})do
                  local data=object(source.root,entry)
                  local batch=data:sub(1,5)=='MJS1\n'and codec.decode(data)or data
                  assert(type(batch)=='string'and batch:sub(1,6)=='batch\n','legacy graphics format')
                  local offset=7
                  while offset<=#batch do
                    local ending=assert(batch:find('\n',offset,true),'legacy graphics length missing')
                    local length=assert(tonumber(batch:sub(offset,ending-1)),'legacy graphics length invalid')
                    assert(length>0 and length%1==0 and ending+length<=#batch,'legacy graphics truncated')
                    commands[#commands+1]=batch:sub(ending+1,ending+length)
                    offset=ending+length+1
                  end
                  graphics[#graphics+1]=data
                end
                validate(packet,commands)
                if record.thumbnail then
                  assert(host('save_legacy_copy '..source.root..' '..record.thumbnail)=='OK',
                    'legacy thumbnail copy failed')
                end
                for i,data in ipairs(graphics)do graphics[i]=store:put(data)end
                packet.identity=store.identity
                copy.snapshot=store:put(codec.encode(packet));copy.graphics=graphics
                candidate.slots[slot]=copy;imported=imported+1
              end)
              if not valid then
                all_valid=false;rejected=rejected+1
                if M.log then M.log('legacy slot '..tostring(slot)..': '..tostring(why))end
              end
            end
          end
          if not had_slots and not profile_recovered and all_valid and old.profile then
            candidate.profile=codec.clone(old.profile);profile_recovered=true
          end
          -- A receipt is published in the same atomic index as recovered slots.
          -- Deleted recovered slots therefore stay deleted on later launches.
          if all_valid then candidate.recovered[receipt]=true end
          index=candidate
          if imported>0 or all_valid or profile_recovered then changed=true end
          recovered=recovered+imported
        end)
        if not ok then
          rejected=rejected+1
          if M.log then M.log('legacy index '..tostring(source.name)..': '..tostring(problem))end
        end
      end
    end
    if changed then store:commit(index)end
    return {recovered=recovered,rejected=rejected,conflicts=conflicts}
  end
  return api
end
return M
