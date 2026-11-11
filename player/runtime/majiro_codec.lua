-- Bounded data-only snapshots. Shared array/table identities are preserved.
-- No Lua bytecode or executable expressions are written to or read from saves.
local M={}
local LIMIT=32*1024*1024
local HEX,UNHEX={},{}
for byte=0,255 do
  local character=string.char(byte);local encoded=string.format('%02x',byte)
  HEX[character]=encoded;UNHEX[encoded]=character
end
function M.clone(value,memo,depth)
  local kind=type(value)
  if kind=='function'then return nil end
  if kind~='table'then return value end
  memo=memo or {};depth=(depth or 0)+1
  assert(depth<=256,'snapshot nesting limit')
  if memo[value]then return memo[value]end
  local out={};memo[value]=out
  for k,v in pairs(value)do
    if type(k)~='function'and type(v)~='function'then out[M.clone(k,memo,depth)]=M.clone(v,memo,depth)end
  end
  return out
end
function M.encode(root)
  local out={'MJS1\n'};local seen={};local count,bytes=0,5
  local function append(text)
    bytes=bytes+#text;assert(bytes<=LIMIT,'snapshot byte limit')
    out[#out+1]=text
  end
  local function write(value,depth)
    assert(depth<=256,'snapshot nesting limit')
    local kind=type(value)
    if value==nil then append('Z\n')
    elseif kind=='boolean'then append(value and'B1\n'or'B0\n')
    elseif kind=='number'then
      assert(value==value and value~=math.huge and value~=-math.huge,'non-finite snapshot number')
      append('N'..string.format('%.17g',value)..'\n')
    elseif kind=='string'then
      append('S'..#value..'\n')
      append((value:gsub('.',HEX)))
    elseif kind=='table'then
      if seen[value]then append('R'..seen[value]..'\n');return end
      count=count+1;assert(count<=200000,'snapshot object limit');seen[value]=count
      local keys={}
      for k in pairs(value)do
        assert(type(k)=='string'or type(k)=='number'or type(k)=='boolean','snapshot key type')
        keys[#keys+1]=k
      end
      table.sort(keys,function(a,b)
        local ta,tb=type(a),type(b)
        if ta~=tb then return ta<tb end
        if ta=='boolean'then return a==false and b==true end
        return a<b
      end)
      append('T'..count..','..#keys..'\n')
      for _,k in ipairs(keys)do write(k,depth+1);write(value[k],depth+1)end
    else error('unsupported snapshot type: '..kind)end
  end
  write(root,0);return table.concat(out)
end
function M.decode(data)
  assert(type(data)=='string'and #data<=LIMIT and data:sub(1,5)=='MJS1\n','invalid snapshot header')
  local position,objects,count=6,{},0
  local function line()
    local ending=data:find('\n',position,true)
    assert(ending and ending-position<100,'invalid snapshot token')
    local value=data:sub(position,ending-1);position=ending+1;return value
  end
  local function read(depth)
    assert(depth<=256,'snapshot nesting limit')
    local token=line();local kind,body=token:sub(1,1),token:sub(2)
    if kind=='Z'then assert(body=='');return nil
    elseif kind=='B'then assert(body=='0'or body=='1');return body=='1'
    elseif kind=='N'then
      local n=tonumber(body)
      assert(n and n==n and n~=math.huge and n~=-math.huge,'invalid snapshot number');return n
    elseif kind=='S'then
      local length=tonumber(body)
      assert(length and length>=0 and length%1==0 and length*2<=#data-position+1,'invalid snapshot string')
      local hex=data:sub(position,position+length*2-1);position=position+length*2
      assert(not hex:find('[^0-9a-f]'),'invalid snapshot string encoding')
      return(hex:gsub('..',UNHEX))
    elseif kind=='R'then
      local id=tonumber(body);assert(id and objects[id],'invalid snapshot reference');return objects[id]
    elseif kind=='T'then
      local id,length=body:match('^(%d+),(%d+)$');id,length=tonumber(id),tonumber(length)
      count=count+1
      assert(id==count and count<=200000 and length<=200000,'invalid snapshot object')
      local result={};objects[id]=result
      for _=1,length do
        local key=read(depth+1)
        assert(type(key)=='string'or type(key)=='number'or type(key)=='boolean','invalid snapshot key')
        assert(result[key]==nil,'duplicate snapshot key')
        result[key]=read(depth+1)
      end
      return result
    end
    error('unknown snapshot token')
  end
  local root=read(0);assert(position==#data+1,'trailing snapshot data');return root
end
return M
