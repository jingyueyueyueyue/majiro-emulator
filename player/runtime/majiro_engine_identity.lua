-- Whole-file provenance and normalized implementation identities must agree.
local M={}
local function same(left,right)
  if type(left)~=type(right)then return false end
  if type(left)~='table'then return left==right end
  for key,value in pairs(left)do if not same(value,right[key])then return false end end
  for key in pairs(right)do if left[key]==nil then return false end end
  return true
end
function M.resolve(hashes,profiles,platform,external,code)
  assert(type(code)=='table'and code.schema==1 and type(code.profiles)=='table'
    and type(code.platform)=='table'and type(code.external)=='table','invalid engine code identity registry')
  local chosen,chosen_platform,chosen_external
  local matches={}
  for _,identity in ipairs(hashes)do
    local exact,implementation=profiles[identity],code.profiles[identity]
    assert(not(exact and implementation)or same(exact,implementation),
      'conflicting exact and implementation engine profiles')
    local profile=exact or implementation
    if profile then
      local controls=assert(platform[identity]or code.platform[identity],'platform engine profile is missing')
      assert(not chosen or same(chosen,profile),'conflicting recovered engine behavior profiles')
      assert(not chosen_platform or same(chosen_platform,controls),'conflicting recovered engine platform profiles')
      local owner=external[identity]or code.external[identity]
      assert(not chosen_external or not owner or same(chosen_external,owner),
        'conflicting recovered engine external profiles')
      chosen=profile;chosen_platform=controls;chosen_external=owner or chosen_external
      matches[#matches+1]=identity
    end
  end
  assert(chosen,'original engine is not identified; recover and verify its behavior profile first')
  return chosen,chosen_platform,chosen_external,matches
end
return M
