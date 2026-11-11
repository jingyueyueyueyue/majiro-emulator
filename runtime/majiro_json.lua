-- Bounded JSON transport for provider settings and plain translation data.
local M={}
local array={}
function M.array(value)return setmetatable(value or{},array)end
local escapes={['"']='\\"',['\\']='\\\\',['\b']='\\b',['\f']='\\f',['\n']='\\n',['\r']='\\r',['\t']='\\t'}
local function quote(value)
  return'"'..value:gsub('[%z\1-\31\\"]',function(char)
    return escapes[char]or string.format('\\u%04x',char:byte())
  end)..'"'
end
function M.encode(value)
  local bytes=0
  local function encode(item,depth)
    assert(depth<32,'JSON nesting limit')
    local kind=type(item)
    if kind=='string'then return quote(item)
    elseif kind=='boolean'then return item and'true'or'false'
    elseif kind=='nil'then return'null'
    elseif kind=='number'then
      assert(item==item and item~=math.huge and item~=-math.huge,'JSON nonfinite number')
      return string.format('%.17g',item)
    end
    assert(kind=='table','unsupported JSON value')
    local result={}
    if getmetatable(item)==array or #item>0 then
      for _,entry in ipairs(item)do result[#result+1]=encode(entry,depth+1)end
      return'['..table.concat(result,',')..']'
    end
    local keys={};for key in pairs(item)do assert(type(key)=='string','JSON object key');keys[#keys+1]=key end
    table.sort(keys)
    for _,key in ipairs(keys)do result[#result+1]=quote(key)..':'..encode(item[key],depth+1)end
    return'{'..table.concat(result,',')..'}'
  end
  local result=encode(value,0);assert(#result<=1024*1024,'JSON byte limit');return result
end
local function utf8(code)
  if code<128 then return string.char(code)
  elseif code<2048 then return string.char(192+math.floor(code/64),128+code%64)
  elseif code<65536 then return string.char(224+math.floor(code/4096),128+math.floor(code/64)%64,128+code%64)
  else return string.char(240+math.floor(code/262144),128+math.floor(code/4096)%64,128+math.floor(code/64)%64,128+code%64)end
end
function M.decode(source)
  assert(type(source)=='string'and #source<=1024*1024,'JSON input byte limit')
  local at,count=1,0
  local function whitespace()while source:sub(at,at):match('[ \t\r\n]')do at=at+1 end end
  local parse
  local function text()
    assert(source:sub(at,at)=='"','JSON string expected');at=at+1
    local result,start={},at
    while at<=#source do
      local char=source:sub(at,at)
      if char=='"'then result[#result+1]=source:sub(start,at-1);at=at+1;return table.concat(result)
      elseif char=='\\'then
        result[#result+1]=source:sub(start,at-1);at=at+1
        local escape=source:sub(at,at);at=at+1
        local decoded={['"']='"',['\\']='\\',['/']='/',b='\b',f='\f',n='\n',r='\r',t='\t'}
        if escape=='u'then
          local hex=source:sub(at,at+3);assert(#hex==4 and not hex:find('[^0-9a-fA-F]'),'JSON unicode escape')
          local code=tonumber(hex,16);at=at+4
          if code>=0xd800 and code<=0xdbff then
            assert(source:sub(at,at+1)=='\\u','JSON surrogate pair missing');at=at+2
            hex=source:sub(at,at+3);assert(#hex==4 and not hex:find('[^0-9a-fA-F]'),'JSON low surrogate escape')
            local low=tonumber(hex,16);assert(low>=0xdc00 and low<=0xdfff,'JSON low surrogate missing')
            code=65536+(code-0xd800)*1024+low-0xdc00;at=at+4
          else assert(code<0xdc00 or code>0xdfff,'JSON unpaired low surrogate')end
          result[#result+1]=utf8(code)
        else result[#result+1]=assert(decoded[escape],'JSON invalid escape')end
        start=at
      else assert(char:byte()>=32,'JSON raw control character');at=at+1 end
    end
    error('JSON unterminated string')
  end
  parse=function(depth)
    assert(depth<32,'JSON nesting limit');count=count+1;assert(count<=100000,'JSON item limit');whitespace()
    local char=source:sub(at,at)
    if char=='"'then return text()
    elseif char=='{'or char=='['then
      local object=char=='{';local result=object and{}or M.array();local seen={}
      at=at+1;whitespace();local ending=object and'}'or']'
      if source:sub(at,at)==ending then at=at+1;return result end
      while true do
        local key
        if object then
          whitespace();key=text();assert(not seen[key],'JSON duplicate key');seen[key]=true;whitespace()
          assert(source:sub(at,at)==':','JSON colon expected');at=at+1
        else key=#result+1 end
        local value=parse(depth+1)
        assert(value~=nil,'JSON null is unsupported in translation transport')
        result[key]=value;whitespace();char=source:sub(at,at);at=at+1
        if char==ending then return result end
        assert(char==',','JSON comma expected')
      end
    elseif source:sub(at,at+3)=='true'then at=at+4;return true
    elseif source:sub(at,at+4)=='false'then at=at+5;return false
    elseif source:sub(at,at+3)=='null'then at=at+4;return nil
    else
      local token=source:sub(at):match('^%-?%d+%.?%d*[eE]?[+-]?%d*')
      assert(token and not token:match('^%-?0%d')and not token:match('%.$'),'JSON number expected')
      local number=tonumber(token);assert(number and number==number and number~=math.huge and number~=-math.huge,'JSON invalid number')
      at=at+#token;return number
    end
  end
  local result=parse(0);whitespace();assert(at==#source+1,'JSON trailing data');return result
end
return M
