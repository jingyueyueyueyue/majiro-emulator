local M={}
local function trim(value)
  local first,last=1,#value
  local function space(byte)return byte==32 or(byte>=9 and byte<=13)end
  while first<=last do
    if space(value:byte(first))then first=first+1
    elseif value:sub(first,first+2)=='\227\128\128'then first=first+3
    else break end
  end
  while last>=first do
    if space(value:byte(last))then last=last-1
    elseif value:sub(last-2,last)=='\227\128\128'then last=last-3
    else break end
  end
  return value:sub(first,last)
end
local function tokens(text,canonical_whitespace)
  local result,remainders={},{}
  local strip=canonical_whitespace and trim or function(value)
    return value:match('^[%z\1-\32]*(.-)[%z\1-\32]*$')
  end
  for line in(text..'\n'):gmatch('(.-)\r?\n')do
    -- Original file_read/file_read$ read physical lines, remove commentary
    -- and find the next comma without quote state. Quotes are only removed
    -- when both ends of the individual field are quotes. An opening quote
    -- on one physical line therefore never makes file_open fail.
    line=strip(line:match('^[^;]*'):gsub('//.*$',''))
    if line~=''then
      local starts={1}
      for i=1,#line do
        if line:sub(i,i)==','then starts[#starts+1]=i+1 end
      end
      local end_cursor=#result+#starts+1
      for index,start in ipairs(starts)do
        local value=strip(line:sub(start,(starts[index+1]or(#line+2))-2))
        if #value>=2 and value:sub(1,1)=='"'and value:sub(-1)=='"'then value=value:sub(2,-2)end
        remainders[#result+1]={text=strip(line:sub(start)),next=end_cursor}
        result[#result+1]=value
      end
    end
  end
  return result,remainders
end
M.tokens=tokens
function M.make()
  local data={config_files={},config_lines={},environment={},resource_names={},image_metadata={},
    movies=HOST.movies(),identity=HOST.identity()}
  local engine=assert(loadstring(HOST.asset_text('runtime/majiro_engine.lua'),'Majiro engine recognition'))()
  local requirements=assert(HOST.engine_requirements(),'engine format requirements unavailable')
  local profile=engine.detect(requirements)
  data.numeric_profile=profile.numeric
  for _,key in ipairs{'minmax_arguments','string_case','string_search_case_sensitive','integer_parse','empty_search_absent',
    'case_string_relations','array_3d_legacy_stride','ultra_disable_returns_previous','ultra_fast_mode_available','text_controls',
    'voice_controls','screen_controls','graphics_controls','dialog_controls'}do data[key]=profile[key]end
  data.platform_controls=profile.platform_controls;data.external_controls=profile.external_controls
  data.event_controls=profile.event_controls
  data.audio_controls=profile.audio_controls
  data.engine_requirements=requirements;data.engine_generation=profile.generation
  local names=HOST.member_names()
  for _,name in ipairs(names)do
    local key=name:lower()
    data.resource_names[key]=true
    local extension=key:match('%.([^%.]+)$')
    if extension=='cfg'then
      data.config_files[name],data.config_lines[name]=tokens(HOST.resource_text(name),true)
    elseif extension=='env'then
      local text=HOST.resource_text(name)
      for line in(text..'\n'):gmatch('(.-)\r?\n')do
        local key,value=line:match('^%s*([^%s/]+)%s+(.+)$')
        if key then data.environment[key]=value:gsub('\t//.*$',''):gsub('|',' ')end
      end
    elseif extension=='rct'or extension=='rc8'or extension=='png'then
      data.image_metadata[key]=HOST.image_metadata(name)
    end
  end
  data.width=assert(tonumber(data.environment.CLIENT_WIDTH),'game CLIENT_WIDTH missing')
  data.height=assert(tonumber(data.environment.CLIENT_HEIGHT),'game CLIENT_HEIGHT missing')
  return data
end
return M
