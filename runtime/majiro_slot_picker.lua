-- Native picker protocol. Saved text is display metadata, never editable code.
local M={}
function M.new(state,key,json)
  local function ordinary(slot)return slot>=1 and slot<=1100 and slot%1==0 end
  local function request(loading,current_slot)
    local entries,available,slot_keys=json.array(),{},{}
    local function add(slot,record,kind)
      if not record then return end
      assert(type(record)=='table','invalid native slot metadata')
      local name=record.thumbnail or''
      assert(name==''or name:match('^thumb_[%w_%-]+%.png$'),'invalid native thumbnail filename')
      entries[#entries+1]={id=slot,time=record.time or'',info=record.info or'',
        thumbnail=name,kind=kind}
      available[slot]=true;slot_keys[slot]=key(slot)
    end
    for name,record in pairs(state.save_catalog or{})do
      local slot=tonumber(name:match('^slot_(%d+)$'))
      if slot and ordinary(slot)then add(slot,record,'normal')end
    end
    if loading then
      add(10001,state.save_catalog[key(10001)],'quick')
      for slot=10003,10010 do add(slot,state.save_catalog[key(slot)],'history')end
    end
    table.sort(entries,function(a,b)return a.id<b.id end)
    return {loading=loading,current_slot=current_slot,available=available,slot_keys=slot_keys,
      message=json.encode{schema=1,loading=loading,current_slot=current_slot,
        normal_min=101,normal_max=1100,page_size=100,entries=entries}}
  end
  local function answer(request,payload)
    if payload==''then return -1 end
    local number=payload:match('^slot=(%d+)$')
    assert(number and #number<=5,'invalid native slot response')
    local slot=tonumber(number)
    if request.loading then
      assert(request.available[slot],'native load slot was not offered')
      assert(request.slot_keys[slot]==key(slot),'native load slot changed while dialog open')
      assert(state.save_catalog[key(slot)],'selected load slot has no saved data')
    else
      assert(slot>=101 and slot<=1100,'native save slot outside original page range')
    end
    return slot
  end
  return{request=request,answer=answer}
end
return M
