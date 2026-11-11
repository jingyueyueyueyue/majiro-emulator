-- Apply the patch's font substitution only at display boundaries.
-- Filenames, function hashes and VM string operations retain source characters.
local M={}
local character='[%z\1-\127\194-\244][\128-\191]*'
function M.new(data,cp932)
  data=data or {characters={},tunnel={}}
  return {
    display=function(text)
      if data.labels and data.labels[text]then return data.labels[text]end
      return (text:gsub(character,data.characters))
    end,
    cp932=function(text)
      local bytes={}
      for char in text:gmatch(character)do
        local pair=data.tunnel[char]
        bytes[#bytes+1]=pair and string.char(pair[1],pair[2])or cp932(char)
      end
      return table.concat(bytes)
    end,
  }
end
return M
