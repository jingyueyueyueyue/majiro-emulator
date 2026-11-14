-- Content-addressed, atomic save objects; identical prefixes are shared by slots.
local M={}
function M.new(codec,host,identity,compatible_identities,policy)
  assert(type(identity)=='string'and #identity==64,'save source identity missing')
  local store={codec=codec,host=host,identity=identity}
  policy=policy or{}
  local index_name='index.mjs'
  local compatible={}
  for _,value in ipairs(compatible_identities or{})do compatible[value]=true end
  function store:accepts_identity(value)
    return value==identity or compatible[value]==true
  end
  local function digest(data)
    local value=host('sha256\n'..data):match('^OK ([0-9a-f]+)$')
    assert(value and #value==64,'save digest failed');return value
  end
  local function exists(name)return host('save_exists '..name)=='OK 1'end
  local function read(name)
    local value=host('save_read '..name)
    if value=='OK absent'then return nil end
    assert(value:sub(1,8)=='OK data\n','save read failed');return value:sub(9)
  end
  local function write(name,data)
    assert(#data<=60000,'save chunk exceeds transport bound')
    assert(host('save_write '..name..'\n'..data)=='OK','save publication failed')
  end
  function store:put(data,maximum)
    maximum=maximum or 56000
    local parts={}
    for at=1,#data,maximum do
      local chunk=data:sub(at,at+maximum-1);local key=digest(chunk)
      if not exists(key..'.mjb')or read(key..'.mjb')~=chunk then write(key..'.mjb',chunk)end
      parts[#parts+1]=key
    end
    return {bytes=#data,parts=parts}
  end
  function store:get(object)
    assert(type(object)=='table'and type(object.parts)=='table'and object.bytes<=32*1024*1024,
      'invalid save object')
    local chunks={};local size=0
    for _,key in ipairs(object.parts)do
      assert(type(key)=='string'and #key==64 and not key:find('[^0-9a-f]'),'invalid save object key')
      local data=assert(read(key..'.mjb'),'save object missing')
      assert(digest(data)==key,'save object integrity failure')
      chunks[#chunks+1]=data;size=size+#data
      assert(size<=object.bytes,'save object size mismatch')
    end
    assert(size==object.bytes,'truncated save object')
    return table.concat(chunks)
  end
  function store:object_key(object)
    local key=digest(tostring(object.bytes))
    for _,part in ipairs(object.parts)do key=digest(key..part)end
    return key
  end
  function store:load_index()
    if self.loaded_index then return codec.clone(self.loaded_index)end
    local partition='index_'..identity..'.mjs'
    if exists(partition)then index_name=partition end
    local root=read(index_name)
    if not root then
      self.loaded_index={schema=1,identity=identity,slots={}}
      return codec.clone(self.loaded_index)
    end
    local descriptor=codec.decode(root)
    local index=codec.decode(self:get(descriptor))
    if index.schema==1 and not self:accepts_identity(index.identity)and policy.partition_foreign then
      -- Keep the original index and every referenced object intact. A changed
      -- script set gets its own index instead of preventing the game from
      -- starting or importing incompatible globals and instruction positions.
      self.foreign_identity=index.identity
      index_name='index_'..identity..'.mjs'
      root=read(index_name)
      if not root then
        self.loaded_index={schema=1,identity=identity,slots={}}
        return codec.clone(self.loaded_index)
      end
      descriptor=codec.decode(root);index=codec.decode(self:get(descriptor))
    end
    assert(index.schema==1 and self:accepts_identity(index.identity),
      'save data belongs to a different script set')
    -- Known proofreading revisions retain raw source hashes and offsets.
    -- Normalize the next index publication without rewriting slot objects.
    index.identity=identity
    self.index_object=descriptor
    self.loaded_index=codec.clone(index)
    return index
  end
  function store:index_name()return index_name end
  function store:commit(index)
    assert(index.identity==identity,'save source identity mismatch')
    local object=self:put(codec.encode(index))
    write(index_name,codec.encode(object))
    self.loaded_index=codec.clone(index)
    local old=self.index_object;self.index_object=object
    if old and not policy.partition_foreign then
      local used={}
      for _,key in ipairs(object.parts)do used[key]=true end
      for _,slot in pairs(index.slots)do
        for _,key in ipairs(slot.snapshot.parts)do used[key]=true end
        for _,part in ipairs(slot.graphics or{})do
          for _,key in ipairs(part.parts)do used[key]=true end
        end
      end
      for _,key in ipairs(old.parts)do if not used[key]then host('save_delete '..key..'.mjb')end end
    end
  end
  function store:collect(old,index)
    if not old or policy.partition_foreign then return end
    local used={}
    for _,key in ipairs(self.index_object and self.index_object.parts or{})do used[key]=true end
    for _,slot in pairs(index.slots)do
      for _,key in ipairs(slot.snapshot.parts)do used[key]=true end
      for _,object in ipairs(slot.graphics or{})do
        for _,key in ipairs(object.parts)do used[key]=true end
      end
      if slot.thumbnail then used[slot.thumbnail]=true end
    end
    for _,object in ipairs(old.graphics or{})do
      for _,key in ipairs(object.parts)do if not used[key]then host('save_delete '..key..'.mjb')end end
    end
    for _,key in ipairs(old.snapshot.parts)do if not used[key]then host('save_delete '..key..'.mjb')end end
    if old.thumbnail and not used[old.thumbnail]then host('save_delete '..old.thumbnail)end
  end
  store.exists=exists;store.digest=digest
  store.path=function(name)return 'savedata-dev/port-data/'..name end
  return store
end
return M
