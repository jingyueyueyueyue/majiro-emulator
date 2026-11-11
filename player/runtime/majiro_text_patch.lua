-- The Windows patch rebuilds original #mjo with sequential #txt entries.
-- Keep source MJO bytes/positions and reproduce its text groups after decoding.
local M={}
local character='[%z\1-\127\194-\244][\128-\191]*'
local function unhex(value)
  assert(type(value)=='string'and #value%2==0 and not value:find('[^0-9a-f]'),'invalid patch byte map')
  return(value:gsub('..',function(pair)return string.char(tonumber(pair,16))end))
end
local function segments(value)
  local result={};local at=1
  while true do
    local ending=value:find('\\n',at,true)
    if not ending then result[#result+1]=value:sub(at);return result end
    result[#result+1]=value:sub(at,ending-1);at=ending+2
  end
end
function M.new(data,source_bytes)
  assert(type(data)=='table'and data.schema==1 and type(data.scripts)=='table'
    and type(data.identity)=='string'and #data.identity==64,'invalid text patch metadata')
  local strings,chars={},{}
  for text,hex in pairs(data.encoded_strings)do strings[text]=unhex(hex)end
  for text,hex in pairs(data.encoded_characters)do chars[text]=unhex(hex)end
  local api={identity=data.identity,font_face=data.font_face or'sans-serif',window_title=data.window_title}
  local typography=data.typography
  if typography then
    assert(type(typography.family)=='string'and typography.family~=''and not typography.family:find('[\r\n]')
      and type(typography.style)=='number'and typography.style%1==0
      and typography.style>=0 and typography.style<=3,'invalid patch typography parameters')
  end
  function api.bytes(text)
    if strings[text]then return strings[text]end
    local ok,result=pcall(source_bytes,text)
    if ok then return result end
    local output={}
    for char in text:gmatch(character)do
      local valid,raw=pcall(source_bytes,char)
      output[#output+1]=valid and raw or assert(chars[char],'unmapped translated character')
    end
    return table.concat(output)
  end
  function api.dialog(text)
    if not text then return text end
    local trimmed=text:gsub('%s+$','')
    local translated=data.dialogs[trimmed]
    return translated and translated..text:sub(#trimmed+1)or text
  end
  function api.graphics(request)
    if not typography then return request end
    local operation=request:match('^(%w+)')
    if operation~='text'and operation~='glyphs'then return request end
    local first=assert(request:find('\n',1,true),'text glyph family is missing')
    local last=assert(request:find('\n',first+1,true),'text glyph body is missing')
    return request:sub(1,first)..'__majiro_style__'..typography.style..':'..
      typography.family..request:sub(last)
  end
  function api.apply(script)
    local record=data.scripts[script.name:lower()]
    if not record then return script end
    assert(script.source_sha256==record.sha256 and script.code_size==record.code_size,
      'text patch original MJO changed: '..script.name)
    assert(not script.text_patch,'text script adapter applied twice')
    local positions={}
    for index,row in ipairs(script.code)do positions[row[1]]=index end
    -- Validate every source group before mutating any decoded row.
    for _,entry in ipairs(record.entries)do
      local index=assert(positions[entry.offset],'text patch source offset absent')
      local row=script.code[index]
      assert(row[2]==entry.opcode,'text patch source opcode changed')
      local expected=row[3]
      if entry.opcode==0x840 then
        while script.code[index+1]and script.code[index+1][1]<entry.resume_offset do
          local a,b,c,d=script.code[index+1],script.code[index+2],script.code[index+3],script.code[index+4]
          assert(a[2]==0x841 and b[2]==0x842 and b[3]=='n'and c[2]==0x83a and d[2]==0x840,
            'text patch text group changed')
          expected=expected..'\\n'..d[3];index=index+4
        end
        assert(positions[entry.resume_offset],'text patch continuation absent')
      end
      assert(expected==entry.expected,'text patch original text changed')
    end
    local virtual=script.code_size
    local tail={}
    local function append(opcode,value)
      local row={virtual,opcode,value};tail[#tail+1]=row;virtual=virtual+1;return row[1]
    end
    for _,entry in ipairs(record.entries)do
      local row=script.code[positions[entry.offset]]
      if entry.opcode==0x801 then row[3]=entry.value
      else
        row[2],row[3]=0x82c,virtual
        for index,value in ipairs(segments(entry.value))do
          if index>1 then append(0x841);append(0x842,'n');append(0x83a,1)end
          append(0x840,value)
        end
        append(0x82c,entry.resume_offset)
      end
    end
    for _,row in ipairs(tail)do script.code[#script.code+1]=row end
    script.text_patch=data.identity
    return script
  end
  return api
end
return M
