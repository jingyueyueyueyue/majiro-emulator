-- Validate the complete selected text layer before configuration, saves or VM.
-- Legacy adapters remain supported as data with exact source hashes, not code.
local M={}
local function digest(value)
  return type(value)=='string'and #value==64 and not value:find('[^0-9a-f]')
end
local function utf8(text)
  local at=1
  while at<=#text do
    local a,b,c,d=text:byte(at,at+3)
    local count=a<128 and 1 or a>=194 and a<=223 and 2 or a>=224 and a<=239 and 3
      or a>=240 and a<=244 and 4 or 0
    assert(count>0 and at+count-1<=#text,'invalid UTF-8 patch data')
    if count>=2 then assert(b>=128 and b<=191,'invalid UTF-8 patch continuation')end
    if count>=3 then
      assert(c>=128 and c<=191 and not(a==224 and b<160)and not(a==237 and b>=160),
        'noncanonical UTF-8 patch data')
    end
    if count==4 then
      assert(d>=128 and d<=191 and not(a==240 and b<144)and not(a==244 and b>=144),
        'invalid UTF-8 patch scalar')
    end
    at=at+count
  end
  return text
end
M.utf8=utf8
function M.data(text,name)
  utf8(text)
  -- Only the table-literal subset emitted by the patch builders is accepted.
  -- Do not execute Lua, even with an empty environment: expressions can allocate
  -- unbounded memory before an instruction hook has a chance to run.
  local at,nodes=1,0
  local function skip()
    while true do
      local _,last=text:find('^%s*',at);at=(last or(at-1))+1
      if text:sub(at,at+1)~='--'then return end
      at=(text:find('\n',at+2,true)or #text)+1
    end
  end
  local function take(token)
    skip();assert(text:sub(at,at+#token-1)==token,'invalid literal patch data: '..name)
    at=at+#token
  end
  local function string_value()
    local quote=text:sub(at,at);at=at+1
    local output={}
    while at<=#text do
      local stop=text:find('[\\\'"\r\n]',at)
      assert(stop,'unterminated patch string')
      output[#output+1]=text:sub(at,stop-1)
      local char=text:sub(stop,stop);at=stop+1
      if char==quote then return utf8(table.concat(output))end
      assert(char~='\r'and char~='\n','literal newline in patch string')
      if char=='\\'then
        local escaped=text:sub(at,at)
        local simple={a='\7',b='\8',f='\12',n='\n',r='\r',t='\t',v='\11',
          ['\\']='\\',['"']='"',["'"]="'"}
        if escaped:match('%d')then
          local digits=assert(text:match('^%d%d?%d?',at))
          local byte=tonumber(digits);assert(byte<=255,'invalid patch byte escape')
          output[#output+1]=string.char(byte);at=at+#digits
        else output[#output+1]=assert(simple[escaped],'unsupported patch string escape');at=at+1 end
      else output[#output+1]=char end
    end
    error('unterminated patch string',0)
  end
  local value
  value=function(depth)
    nodes=nodes+1;assert(nodes<=2000000 and depth<=32,'patch data size limit')
    skip();local char=text:sub(at,at)
    if char=='"'or char=="'"then return string_value()end
    if char=='{'then
      at=at+1;local result,used,index={},{},1
      while true do
        skip();if text:sub(at,at)=='}'then at=at+1;return result end
        local key,child
        if text:sub(at,at)=='['then
          at=at+1;key=value(depth+1);take(']');take('=');child=value(depth+1)
        else
          local identifier=text:match('^([%a_][%w_]*)%s*=',at)
          if identifier then key=identifier;at=at+#identifier;take('=');child=value(depth+1)
          else key=index;index=index+1;child=value(depth+1)end
        end
        assert((type(key)=='string'or type(key)=='number')and not used[key],'duplicate or invalid patch key')
        used[key]=true;result[key]=child;skip()
        char=text:sub(at,at)
        if char==','or char==';'then at=at+1
        else assert(char=='}','patch table separator missing')end
      end
    end
    for token,boolean in pairs{['true']=true,['false']=false}do
      if text:sub(at,at+#token-1)==token then at=at+#token;return boolean end
    end
    local number=text:match('^[-+]?%d+%.?%d*[eE][-+]?%d+',at)or text:match('^[-+]?%d+%.?%d*',at)
    assert(number,'patch data is not a literal: '..name);at=at+#number
    local result=tonumber(number);assert(result and result==result and result~=math.huge and result~=-math.huge,
      'invalid patch number');return result
  end
  take('return');local data=value(0);skip()
  assert(at>#text,'trailing executable patch data')
  local seen,nodes={},0
  local function validate(value,depth)
    nodes=nodes+1;assert(nodes<=2000000 and depth<=32,'patch data size limit')
    if type(value)=='string'then utf8(value)
    elseif type(value)=='table'then
      assert(not seen[value]and getmetatable(value)==nil,'cyclic or metatable patch data')
      seen[value]=true
      for key,child in pairs(value)do validate(key,depth+1);validate(child,depth+1)end
    else assert(type(value)=='number'or type(value)=='boolean','non-data patch value')end
  end
  assert(type(data)=='table','patch data must be a table');validate(data,0)
  return data
end
function M.new(mode,config,host,modules)
  assert(mode=='original'or mode=='local'or mode=='online','invalid text resource mode')
  local api={metadata_digest='',required={},seen={}}
  local function hash(raw)
    return assert(host.storage('sha256\n'..raw):match('^OK ([0-9a-f]+)$'),'text hash unavailable')
  end
  local function load(path)
    local raw=assert(host.game_text(path),'declared patch metadata missing: '..path)
    local sha=hash(raw)
    if config.schema==2 then
      assert((config.metadata_hashes or{})[path]==sha,'patch metadata digest missing or changed: '..path)
    end
    api.metadata_digest=api.metadata_digest..'\n'..path..'\n'..sha
    return M.data(raw,path)
  end
  if mode=='local'then
    assert(not config.legacy,'local text requires an explicit manifest')
    if config.raw_metadata and config.raw_metadata~=''then
      api.raw_data=load(config.raw_metadata)
      api.raw=modules('runtime/majiro_patch.lua').new(api.raw_data,host.decode_bytes)
      for name,sha in pairs(api.raw_data.files)do
        assert(digest(sha),'invalid raw resource hash')
        api.required[name]={hash=sha,kind='pinned_raw_patch'}
      end
    end
    local adapter=config.text_adapter or{}
    if adapter.path and adapter.path~=''then
      assert(not api.raw and adapter.kind=='sequential_text_v1','conflicting or unsupported text adapters')
      api.text_data=load(adapter.path)
      api.text=modules('runtime/majiro_text_patch.lua').new(api.text_data,host.cp932)
      for name,record in pairs(api.text_data.scripts)do
        assert(digest(record.sha256),'invalid original MJO hash')
        api.required[name]={hash=record.sha256,kind='source_mjo'}
      end
    end
    for name,sha in pairs(config.resource_hashes or{})do
      assert(not api.required[name]and digest(sha),'conflicting text resource hashes')
      api.required[name]={hash=sha,kind='encoded_text'}
    end
    assert(next(api.required),'local text layer has no verified resource inventory')
    local present={}
    for _,row in ipairs(host.patch_resource_names())do
      local directory,name=row:match('^([^\t]+)\t([^\t]+)$')
      assert(directory and name,'invalid overlay resource name')
      local entry=assert(api.required[name],'undeclared file in text layer: '..name)
      assert(not present[name],'ambiguous shadowed overlay: '..name)
      local declared=false
      for _,layer in ipairs(config.script_layers or{})do
        if layer.directory==directory and layer.kind==entry.kind then declared=true end
      end
      assert(declared,'overlay kind does not match verified text inventory: '..name)
      present[name]=true
    end
    -- Pinned metadata also contains binary tables. Verify them without decoding.
    for name,entry in pairs(api.required)do
      if entry.kind~='encoded_text'and not present[name]then
        local found=false
        for _,layer in ipairs(config.script_layers or{})do
          if layer.kind==entry.kind then
            local raw=host.game_text(layer.directory..'/'..name)
            if raw then assert(hash(raw)==entry.hash,'overlay content changed: '..name);found=true end
          end
        end
        assert(found,'required overlay resource missing: '..name)
      end
    end
  end
  function api.verify(name,sha,kind,path)
    assert(digest(sha),'invalid text digest')
    if mode~='local'then
      assert(kind=='original','localized input cannot enter original/online mode')
      return
    end
    local entry=api.required[name]
    if entry then
      local valid=entry.kind==kind or(entry.kind=='encoded_text'and
        (kind=='cp932'or kind=='cp936'or kind=='utf-8'))
      assert(valid and sha==entry.hash,'text provenance/hash mismatch: '..name)
      api.seen[name]=true
    else assert(kind=='original','unverified local text resource: '..path)end
  end
  function api.apply(script)
    if api.raw then script=api.raw.apply(script)end
    if api.text then script=api.text.apply(script)end
    return script
  end
  function api.preflight()
    local has_start=false
    for _,name in ipairs(host.text_resource_names())do
      has_start=has_start or name=='start.mjo'
      if name:match('%.mjo$')then api.apply(host.script(name))
      elseif name:match('%.txt$')and not api.required[name]then host.pin_text_resource(name)
      else utf8(host.resource_text(name))end
    end
    assert(has_start,'Original start.mjo is unavailable. Restore the original game resources before launching.')
    for name,entry in pairs(api.required)do
      if name:match('%.mjo$')or name:match('%.cfg$')or name:match('%.env$')or name:match('%.txt$')then
        assert(api.seen[name],'declared text resource is not in the effective layer: '..name)
      end
    end
    host.seal_text_resources()
  end
  function api.identity(source)
    return hash('verified-text-v2\n'..source..'\nvm:'..(config.vm_encoding or'cp932')..api.metadata_digest)
  end
  return api
end
return M
