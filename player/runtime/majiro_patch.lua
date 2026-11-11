-- Raw patch bytes remain source data. Only verified data repairs alter decoded rows.
local M={}
local function charbytes(value)
  assert(type(value)=='string'and #value==4 and not value:find('[^0-9a-f]'),'invalid patch tunnel pair')
  return string.char(tonumber(value:sub(1,2),16),tonumber(value:sub(3,4),16))
end
function M.new(metadata,decode)
  assert(type(metadata)=='table'and metadata.schema==1 and type(metadata.scripts)=='table',
    'unsupported raw translation patch metadata')
  local tunnel={}
  for hex,value in pairs(metadata.decode_tunnel or{})do tunnel[charbytes(hex)]=value end
  local function decoded(bytes)
    local output,plain={},{}
    local function flush()
      if #plain>0 then output[#output+1]=decode(table.concat(plain));plain={}end
    end
    local at=1
    while at<=#bytes do
      local pair=bytes:sub(at,at+1);local translated=tunnel[pair]
      if translated then flush();output[#output+1]=translated;at=at+2
      else
        local first=bytes:byte(at)
        local length=((first>=0x81 and first<=0x9f)or(first>=0xe0 and first<=0xfc))and 2 or 1
        assert(at+length-1<=#bytes,'truncated raw patch multibyte sequence')
        plain[#plain+1]=bytes:sub(at,at+length-1);at=at+length
      end
    end
    flush();return table.concat(output)
  end
  local function apply(script)
    if not script.patch_source then return script end
    assert(not script.raw_patch_applied,'raw patch adapter applied twice')
    local record=assert(metadata.scripts[script.name:lower()],'raw patch script metadata absent')
    assert(record.sha256==script.source_sha256,'raw patch script hash changed')
    local repairs={}
    for _,entry in ipairs(record.preludes or{})do
      assert(not repairs[entry.offset],'duplicate raw patch stack repair')
      repairs[entry.offset]=entry
    end
    local output={};local virtual=script.code_size
    local consumed,replaced,expected_replacements=0,0,0
    for _,entries in pairs(record.text_replacements or{})do expected_replacements=expected_replacements+#entries end
    for _,row in ipairs(script.code)do
      local source_offset=row[1]
      local copy={};for i,value in ipairs(row)do copy[i]=value end
      for _,entry in ipairs((record.text_replacements or{})[source_offset]or{})do
        assert(row[2]==entry.opcode and row[entry.operand+3]==entry.expected,'raw patch text anchor changed')
        copy[entry.operand+3]=entry.value;replaced=replaced+1
      end
      local repair=repairs[source_offset]
      if repair then
        assert(row[2]==repair.opcode,'raw patch stack repair anchor changed')
        local offset=source_offset
        for _,value in ipairs(repair.push_values)do
          output[#output+1]={offset,0x800,value};offset=virtual;virtual=virtual+1
        end
        copy[1]=offset;consumed=consumed+1
      end
      output[#output+1]=copy
    end
    assert(consumed==#(record.preludes or{}),'raw patch stack repair offset absent')
    assert(replaced==expected_replacements,'raw patch text offset absent')
    script.code=output;script.raw_patch_applied=true;return script
  end
  return{decode=decoded,apply=apply,display=metadata.display or{}}
end
return M
