-- Shared tile state operations whose native entry points are unnamed.
local M={}
local function trunc(value)return value<0 and math.ceil(value)or math.floor(value)end
function M.install(define,state,options)
  local function api(vm)
    if not state.tiles_actions then vm:fail('tile action engine is not installed')end
    return state.tiles_actions
  end
  local function seat(value)
    assert(type(value)=='number'and value%1==0 and value>=0 and value<=3,'tile player outside range')
    return value
  end
  define(0x6ddeb30e,function(vm,args)
    assert(#args==1);local player=seat(args[1]);local a=api(vm)
    local waits=a.waits(player);local discarded=player==3 and 0x5d07c0 or(0x5d1360+player*160)
    local wanted={}
    for _,kind in ipairs(waits)do wanted[kind]=true end
    for i=0,39 do
      local encoded=a.index(discarded,i)
      if encoded==-1 then break end
      if wanted[trunc(encoded/10)]then return 1 end
    end
    return 0
  end)
  define(0x850eecf4,function(vm,args)
    assert(#args==2);local player=seat(args[1]);local encoded=args[2]
    assert(type(encoded)=='number'and encoded%1==0,'encoded tile argument')
    local a=api(vm);local get,put,index,set=a.get,a.put,a.index,a.set
    local values,groups,marks
    if player==3 then values,groups,marks=0x5d08e0,0x5d08a0,0x5d0860
    else values,groups,marks=0x5d1280+player*56,0x5d11c0+player*56,0x5d1100+player*56 end
    put(0x5d0d64,1)
    local quad_count=get(0x5d0798)+1;put(0x5d0798,quad_count)
    if quad_count==4 then
      local closed=0
      for i=0,12,3 do if index(groups,i)==-1 or index(groups,i)==5 then closed=closed+1 end end
      if closed==3 then put(0x5d0798,-1)end
    end
    local kind=trunc(encoded/10)
    local concealed=index(values,13)~=600
    if concealed then
      for i=0,13 do
        if trunc(index(values,i)/10)==kind and index(groups,i)~=0 then concealed=false;break end
      end
    else set(values,13,encoded)end
    local count=0
    while count<14 and index(groups,count)~=0 do count=count+1 end
    assert(count<=11,'tile hand has no group capacity')
    local upgraded=false
    for i=0,count-1 do if trunc(index(values,i)/10)==kind then upgraded=true;break end end
    for i=0,13 do if trunc(index(values,i)/10)==kind then set(values,i,0)end end
    local function sort_suffix(first)
      local sorted={}
      for i=first,13 do sorted[#sorted+1]=index(values,i)end
      table.sort(sorted)
      for i,value in ipairs(sorted)do set(values,first+i-1,value)end
    end
    if upgraded then
      local target
      for i=0,12,3 do if trunc(index(values,i)/10)==0 then target=i;break end end
      assert(target and target<=11,'native upgraded group position invalid')
      for j=0,2 do set(values,target+j,kind*10+j);set(groups,target+j,5)end
    else
      sort_suffix(count)
      for j=0,2 do set(values,count+j,kind*10+j);set(groups,count+j,concealed and-1 or 5)end
    end
    for i=0,13 do if index(values,i)==0 then set(values,i,600)end end
    if not upgraded then
      if not concealed then
        local mode=get(0x5d1578);local offset=get(0x5d0a30)
        local at=mode==2 and(count+1)or mode==3 and(count+(offset==0 and 2 or 0))
          or mode==4 and(count+2-offset)or nil
        if at then assert(at>=0 and at<14,'tile source marker outside hand');set(marks,at,1)end
      end
      count=count+3
    end
    sort_suffix(count)
    return concealed and 1 or 0
  end)
end
return M
