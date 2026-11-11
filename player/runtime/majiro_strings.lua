-- Character-based operations over UTF-8 display strings, with CP932 hashing.
local M={}
local fullwidth_lower={}
for i=0,25 do
  local byte=0xa1+i
  fullwidth_lower[string.char(0xef,0xbc,byte)]=string.char(0xef,0xbd,0x81+i)
end
function M.resource_key(text)
  -- Match the catalogue/model's case handling without width conversion.
  -- The shipped CG script spells ＣＧモード数字 with uppercase fullwidth
  -- letters, while the actual archive/catalogue name uses lowercase.
  return (text:lower():gsub('[\239][\188][\161-\186]',fullwidth_lower):gsub('\\','/'))
end
local function characters(text)
  local out={}
  for char in text:gmatch('[%z\1-\127\194-\244][\128-\191]*')do out[#out+1]=char end
  return out
end
local multibyte_lower={}
local multibyte_upper={}
do
  local upper=characters('ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ')
  local lower=characters('αβγδεζηθικλμνξοπρστυφχψωабвгдеёжзийклмнопрстуфхцчшщъыьэюя')
  for i,value in ipairs(upper)do
    multibyte_lower[value]=lower[i];multibyte_upper[lower[i]]=value
  end
end
function M.install(define,state,options)
  local function crc(text)
    local bytes=options.cp932 and options.cp932(text)or text
    local value=0xffffffff
    local function xor(a,b)
      local out,p=0,1
      for _=1,32 do
        if a%2~=b%2 then out=out+p end
        a,b,p=math.floor(a/2),math.floor(b/2),p*2
      end
      return out
    end
    for i=1,#bytes do
      value=xor(value,bytes:byte(i))
      for _=1,8 do value=value%2==1 and xor(math.floor(value/2),0xedb88320)or math.floor(value/2)end
    end
    return xor(value,0xffffffff)
  end
  local function fold(text)
    text=text:lower():gsub('[\239][\188][\161-\186]',fullwidth_lower)
    if options.string_case=='cp932' then
      text=text:gsub('[%z\1-\127\194-\244][\128-\191]*',multibyte_lower)
    end
    return text
  end
  local function uppercase(text)
    text=text:upper():gsub('[\239][\189][\129-\154]',function(char)
      return string.char(0xef,0xbc,char:byte(3)+0x20)
    end)
    if options.string_case=='cp932'then
      text=text:gsub('[%z\1-\127\194-\244][\128-\191]*',multibyte_upper)
    end
    return text
  end
  local function byte_characters(text)
    local out,index={},1
    while index<=#text do
      local value=text:byte(index)
      if(value>=0x81 and value<=0x9f)or(value>=0xe0 and value<=0xfc)then
        value=value*256+(text:byte(index+1)or 0);index=index+1
      end
      out[#out+1]=value;index=index+1
    end
    return out
  end
  local function compare(a,b,sensitive)
    a,b=a:match('^[^%z]*'),b:match('^[^%z]*')
    if not sensitive then a,b=fold(a),fold(b)end
    if a==b then return 0 end
    if options.cp932 then
      local left_ok,left=pcall(options.cp932,a)
      local right_ok,right=pcall(options.cp932,b)
      if not left_ok or not right_ok then
        -- Display translations can contain Unicode absent from CP932.
        -- Preserve distinct strings and their deterministic UTF-8 ordering;
        -- replacing with '?' would make unrelated translated lines equal.
        -- Resource names and hashes continue through the strict encoder.
        return a<b and -1 or 1
      end
      a,b=left,right
    end
    if options.string_case=='cp932'and options.cp932 then
      -- The older CRT compares multibyte character codes. The later
      -- engine helper compares the byte sequence, which orders halfwidth
      -- kana differently relative to a two-byte Japanese character.
      a,b=byte_characters(a),byte_characters(b)
      for i=1,math.max(#a,#b)do
        local left,right=a[i]or 0,b[i]or 0
        if left~=right then return left<right and -1 or 1 end
      end
      return 0
    end
    return a==b and 0 or (a<b and -1 or 1)
  end
  local function slice(text,start,count)
    local chars=characters(text)
    start=math.max(0,start)
    local out={}
    for i=start+1,math.min(#chars,start+math.max(0,count))do out[#out+1]=chars[i]end
    return table.concat(out)
  end
  define('$strlen',function(vm,args)assert(#args==1);return #characters(args[1])end)
  define('$strleft$',function(vm,args)
    assert(#args==2);if args[2]<0 then return args[1] end
    return slice(args[1],0,args[2])
  end)
  define('$strright$',function(vm,args)
    assert(#args==2);if args[2]<0 then return args[1]end
    local chars=characters(args[1]);return slice(args[1],math.max(0,#chars-args[2]),args[2])
  end)
  define('$strmid$',function(vm,args)
    assert(#args==3);if args[2]<0 then return ''end
    return slice(args[1],args[2],args[3]<0 and #characters(args[1])or args[3])
  end)
  define('$strstr',function(vm,args)
    assert(#args==2)
    if options.empty_search_absent and args[1]==''then return -1 end
    local found=fold(args[1]):find(fold(args[2]),1,true)
    return found and #characters(args[1]:sub(1,found-1)) or -1
  end)
  -- The syscall wrappers compare argument two to argument one. Bytecode
  -- string operators use the ordinary left/right ordering.
  define('$strcmp',function(vm,args)assert(#args==2);return compare(args[2],args[1],true)end)
  define('$stricmp',function(vm,args)assert(#args==2);return compare(args[2],args[1])end)
  define('$strupr$',function(vm,args)assert(#args==1);return uppercase(args[1])end)
  define('$strlwr$',function(vm,args)assert(#args==1);return fold(args[1])end)
  local function split(text)
    local result={}
    for value in (text..','):gmatch('(.-),')do
      -- Original 0x430AA8 strips bytes <= 0x20 from each comma field,
      -- then removes paired quotes. CGMODE.CFG uses spaces after commas.
      value=value:gsub('^[%z\1-\32]+',''):gsub('[%z\1-\32]+$','')
      if #value>=2 and value:sub(1,1)=='"'and value:sub(-1)=='"'then value=value:sub(2,-2)end
      result[#result+1]=value
    end
    return result
  end
  define('$num_strsplit',function(vm,args)assert(#args==1);return #split(args[1])end)
  define('$strsplit$',function(vm,args)
    assert(#args==2);return split(args[1])[args[2]+1] or ''
  end)
  define('$atoi',function(vm,args)
    assert(#args==1);local part=args[1]:match('^%s*([%+%-]?%d+)')
    if not part then return 0 end
    local negative=part:sub(1,1)=='-'
    local result=0;local limit=negative and 2147483648 or 2147483647
    for digit in part:gmatch('%d')do
      result=result*10+tonumber(digit)
      if options.integer_parse=='wrap32' then result=result%4294967296
      elseif result>limit then result=limit;break end
    end
    if negative then result=-result end
    return ((result+2147483648)%4294967296)-2147483648
  end)
  define('$crc32str',function(vm,args)assert(#args==1);return crc(args[1])end)
  local function variable(vm,name)
    if not name:find('@',2,true)then name=name..'@GLOBAL'end
    local scope=({['#']=0,['@']=1,['%']=2})[name:sub(1,1)]
    if scope==nil then vm:fail('dynamic variable scope unsupported: '..name)end
    return vm.globals[scope],crc(name)
  end
  define('$get_variable',function(vm,args)
    assert(#args==1);local bank,key=variable(vm,args[1]);return bank[key]or 0
  end)
  define('$get_variable_nowarn',function(vm,args)
    assert(#args==1);local bank,key=variable(vm,args[1]);return bank[key]or 0
  end)
  local function string_variable(vm,args,warn)
    assert(#args==1 and type(args[1])=='string','string variable name required')
    local bank,key=variable(vm,args[1]);local value=bank[key]
    if value==nil then
      if warn then
        state.missing_variables=state.missing_variables or{}
        state.missing_variables[args[1]]=(state.missing_variables[args[1]]or 0)+1
      end
      return''
    end
    if type(value)~='string'then vm:fail('dynamic variable is not a string: '..args[1])end
    return value
  end
  define('$get_variable$',function(vm,args)return string_variable(vm,args,true)end)
  define('$get_variable_nowarn$',function(vm,args)return string_variable(vm,args,false)end)
  define('$set_variable',function(vm,args)
    assert(#args==2);local bank,key=variable(vm,args[1]);bank[key]=args[2]
    if args[1]:sub(1,1)=='#'then state.persist_dirty=true end
    return 0
  end)
  define('$itoa$',function(vm,args)assert(#args==1);return string.format('%.0f',args[1])end)
  define('$chr$',function(vm,args)
    local pieces={}
    for index,value in ipairs(args)do
      assert(type(value)=='number'and value%1==0,'chr$ requires integers')
      value=value%4294967296
      local bytes={}
      for at=1,4 do
        local byte=value%256
        if byte==0 then break end
        bytes[#bytes+1]=string.char(byte);value=math.floor(value/256)
      end
      pieces[index]=table.concat(bytes)
    end
    local raw=table.concat(pieces)
    assert(#raw<2048,'chr$ exceeds original string buffer')
    if options.decode_bytes then return options.decode_bytes(raw)end
    if raw:find('[\128-\255]')then vm:fail('CP932 byte decoder unavailable for chr$')end
    return raw
  end)
  define('$strmatch',function(vm,args)
    assert(#args==2)
    -- Original 0x43085F compares CP932 bytes symmetrically. Either operand
    -- can contain ?, and * accepts the remaining suffix immediately.
    -- This is not a glob: the pattern suffix following * is never checked.
    local a,b=fold(args[1]),fold(args[2])
    if options.cp932 then a,b=options.cp932(a),options.cp932(b)end
    for i=1,math.max(#a,#b)+1 do
      local left,right=a:byte(i)or 0,b:byte(i)or 0
      if left==42 or right==42 then return -1 end
      if left==0 or right==0 then return left==right and -1 or 0 end
      if left~=63 and right~=63 and left~=right then return 0 end
    end
    return -1
  end)
  return {characters=characters,compare=compare,slice=slice,hash=crc,fold=fold,uppercase=uppercase}
end
return M
