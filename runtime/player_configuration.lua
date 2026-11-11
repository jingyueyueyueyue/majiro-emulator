local M={}
local function formal(path)
  local text=HOST.game_text('player-data/scripts/'..path..'.lua')
  return text and assert(loadstring(text,path))()
end
local function tokens(text)
  local result,remainders={},{}
  for line in(text..'\n'):gmatch('(.-)\r?\n')do
    local quoted=false;local stop=#line
    for i=1,#line do
      if line:sub(i,i)=='"'then quoted=not quoted
      elseif not quoted and line:sub(i,i+1)=='//'then stop=i-1;break end
    end
    line=line:sub(1,stop):match('^%s*(.-)%s*$')
    if line~=''then
      local starts={1};quoted=false
      for i=1,#line do
        if line:sub(i,i)=='"'then quoted=not quoted
        elseif not quoted and line:sub(i,i)==','then starts[#starts+1]=i+1 end
      end
      assert(not quoted,'unterminated configuration quote')
      local end_cursor=#result+#starts+1
      for index,start in ipairs(starts)do
        local value=line:sub(start,(starts[index+1]or(#line+2))-2):match('^%s*(.-)%s*$')
        if value:sub(1,1)=='"'and value:sub(-1)=='"'then value=value:sub(2,-2)end
        remainders[#result+1]={text=line:sub(start):match('^%s*(.-)%s*$'),next=end_cursor}
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
  local profiles=assert(loadstring(HOST.asset_text('runtime/majiro_engine_profiles.lua'),
    'engine profiles'))()
  local platform_profiles=assert(loadstring(HOST.asset_text('runtime/majiro_platform_profiles.lua'),
    'platform profiles'))()
  for _,hash in ipairs(HOST.engine_hashes())do
    local profile=profiles[hash]
    if profile then
      assert(not data.numeric_profile or data.numeric_profile==profile.numeric,
        'conflicting original engine numeric profiles')
      assert(not data.minmax_arguments or data.minmax_arguments==profile.minmax_arguments,
        'conflicting original engine min/max profiles')
      data.numeric_profile=profile.numeric
      data.minmax_arguments=profile.minmax_arguments
      assert(not data.string_case or data.string_case==profile.string_case,
        'conflicting original engine string profiles')
      data.string_case=profile.string_case
      data.integer_parse=profile.integer_parse
      data.empty_search_absent=profile.empty_search_absent
      data.case_string_relations=profile.case_string_relations
      data.ultra_disable_returns_previous=profile.ultra_disable_returns_previous
      if data.text_controls then
        for key,value in pairs(data.text_controls)do
          assert(profile.text_controls and profile.text_controls[key]==value,
            'conflicting original engine text controls')
        end
      end
      data.text_controls=profile.text_controls
      if data.voice_controls then
        for key,value in pairs(data.voice_controls)do
          assert(profile.voice_controls and profile.voice_controls[key]==value,
            'conflicting original engine voice controls')
        end
      end
      data.voice_controls=profile.voice_controls
      if data.screen_controls then
        for key,value in pairs(data.screen_controls)do
          assert(profile.screen_controls and profile.screen_controls[key]==value,
            'conflicting original engine screen controls')
        end
      end
      data.screen_controls=profile.screen_controls
      local platform_profile=assert(platform_profiles[hash],'platform engine profile is missing')
      if data.platform_controls then
        for key,value in pairs(data.platform_controls)do
          assert(platform_profile[key]==value,'conflicting original engine platform controls')
        end
      end
      data.platform_controls=platform_profile
      if data.graphics_controls then
        for key,value in pairs(data.graphics_controls)do
          assert(profile.graphics_controls and profile.graphics_controls[key]==value,
            'conflicting original engine graphics controls')
        end
      end
      data.graphics_controls=profile.graphics_controls
      if data.dialog_controls then
        for key,value in pairs(data.dialog_controls)do
          assert(profile.dialog_controls and profile.dialog_controls[key]==value,
            'conflicting original engine dialog controls')
        end
      end
      data.dialog_controls=profile.dialog_controls
    end
  end
  assert(data.numeric_profile,
    'original engine is not identified; recover and verify its numeric profile first')
  local names=HOST.member_names()
  for _,name in ipairs(names)do
    local key=name:lower()
    data.resource_names[key]=true
    local extension=key:match('%.([^%.]+)$')
    if extension=='cfg'then
      data.config_files[name],data.config_lines[name]=tokens(HOST.resource_text(name))
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
  local translated=formal('catalogue')
  if translated then
    data.translation=true
    data.config_files=assert(formal('config_files'))
    data.config_lines=assert(formal('config_lines'))
    data.environment=assert(formal('environment'))
    data.resource_names=assert(formal('resource_names'))
    for name,value in pairs(assert(formal('image_metadata')))do data.image_metadata[name]=value end
  end
  data.width=assert(tonumber(data.environment.CLIENT_WIDTH),'game CLIENT_WIDTH missing')
  data.height=assert(tonumber(data.environment.CLIENT_HEIGHT),'game CLIENT_HEIGHT missing')
  return data
end
return M
