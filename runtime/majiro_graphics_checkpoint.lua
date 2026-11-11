-- Backward pixel dependencies remove obsolete scenes from save replay.
local M={}
function M.compact(commands,live)
  local rows,sizes,masks={},{},{}
  for id,page in pairs(live)do sizes[id]={page.width,page.height}end
  for i,command in ipairs(commands)do
    local op=command:match('^(%w+)')
    local numbers={}
    for number in command:sub(#op+1):gmatch('[-+]?%d+%.?%d*')do
      numbers[#numbers+1]=tonumber(number)
      if #numbers>=14 then break end
    end
    rows[i]={op=op,n=numbers}
    if op=='create'then sizes[numbers[1]]={numbers[2],numbers[3]}end
    if op=='alpha'or op=='antidata'then
      local owner,mask=numbers[1],numbers[2]
      masks[owner]=masks[owner]or{}
      if mask~=0 then masks[owner][mask]=true end
    end
  end
  local pixels,allocate,used,keep={},{},{},{}
  local function need(id)
    if not id or id==0 then return end
    pixels[id]=true;allocate[id]=true;used[id]=true
    for mask in pairs(masks[id]or{})do pixels[mask]=true;allocate[mask]=true;used[mask]=true end
  end
  for id in pairs(live)do need(id)end
  for i=#rows,1,-1 do
    local row=rows[i];local op,n=row.op,row.n
    local id=n[1]
    if op=='create'or op=='load'or op=='load8'then
      if allocate[id]then keep[i]=true;allocate[id]=nil;pixels[id]=nil end
    elseif op=='alpha'then
      local mask=n[2]
      if allocate[mask]or allocate[id]then
        keep[i]=true;allocate[id]=true;used[id]=true;used[mask]=true
        allocate[mask]=nil;pixels[mask]=nil
        if n[3]~=0 then pixels[id]=true end
      end
    elseif op=='antidata'then
      if allocate[id]then keep[i]=true;need(n[2])end
    elseif op=='swap'then
      if pixels[n[1]]or pixels[n[6]]then keep[i]=true;need(n[1]);need(n[6])end
    elseif op~='release'then
      local dest,source=id,nil
      if op=='copy'or op=='extcopy'or op=='modify'or op=='revmulcopy'or
        op=='mulcopy'or op=='mosaic'or op=='thumbrect'or op=='tablecopy'then dest,source=n[6],n[1]
      elseif op=='fuchidori'then dest,source=n[2],n[1]
      elseif op=='smoothframe'then dest,source=n[8],n[1]
      elseif op=='paste'then dest,source=n[1],n[2]
      elseif op=='thumbnail'then dest,source=n[2],n[1]end
      if pixels[dest]or(op=='drawmode'and allocate[id])then
        keep[i]=true;allocate[dest]=true;used[dest]=true
        local size=sizes[dest]
        local source_size=source and sizes[source]
        if op=='copy'and size and source_size and n[2]>=0 and n[3]>=0 and
           n[2]+n[4]<=source_size[1]and n[3]+n[5]<=source_size[2]and
           n[7]==0 and n[8]==0 and n[4]>=size[1]and n[5]>=size[2]then
          pixels[dest]=nil
          for mask in pairs(masks[dest]or{})do pixels[mask]=nil end
        elseif op=='fill'and size and n[2]==0 and n[3]==0 and n[4]>=size[1]and n[5]>=size[2]then
          pixels[dest]=nil
        elseif op=='thumbnail'then pixels[dest]=nil end
        if source then need(source)end
        if op=='fuchidori'then need(n[3])elseif op=='tablecopy'then need(n[9])end
      end
    end
  end
  local result={}
  for i,row in ipairs(rows)do
    if keep[i]or(row.op=='release'and used[row.n[1]])then result[#result+1]=commands[i]end
  end
  return result
end
return M
