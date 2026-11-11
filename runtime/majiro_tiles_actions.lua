-- Original tile-game action family. Canonical offsets identify shared engine
-- fields across adapters; values are ordinary serializable VM-owned numbers.
local M={}
local function trunc(value)return value<0 and math.ceil(value)or math.floor(value)end
function M.install(define,state,options)
  state.tiles=state.tiles or{}
  state.tiles.memory=state.tiles.memory or{}
  local function memory()return state.tiles.memory end
  local function get(address)return memory()[address]or 0 end
  local function put(address,value)memory()[address]=value;return value end
  local function index(base,i)return get(base+i*4)end
  local function set(base,i,value)return put(base+i*4,value)end
  local function integer(value)
    assert(type(value)=='number'and value%1==0,'tile integer argument required')
    return((value+2147483648)%4294967296)-2147483648
  end
  local function seat(args)
    assert(#args==1,'tile player argument required')
    local value=integer(args[1]);assert(value>=0 and value<4,'tile player outside range')
    return value
  end
  local function random(divisor)
    assert(divisor>0,'tile random divisor')
    state.random_seed=((state.random_seed or 1)*214013+2531011)%4294967296
    return math.floor(state.random_seed/65536)%32768%divisor
  end
  local limits={[0]=15,[1]=10,[2]=136,[3]=108,[4]=136}
  local function wall_limit()return limits[get(0x5d1578)]or 0 end
  local function decrement(encoded,basic)
    local kind=trunc(encoded/10)
    set(0x5b4520,kind,index(0x5b4520,kind)-1)
    if basic then set(0x5b4480,kind,index(0x5b4480,kind)-1)end
  end
  local function draw_plain()
    local cursor=get(0x5d1580);local found=cursor
    while found<wall_limit()and index(0x5d15a0,found)~=0 do found=found+1 end
    assert(found<wall_limit(),'tile wall has no unmarked remaining tile')
    local encoded=index(0x5d1800,found)
    set(0x5d1800,found,index(0x5d1800,cursor))
    set(0x5d15a0,found,index(0x5d15a0,cursor))
    put(0x5d1580,cursor+1);decrement(encoded,true)
    return encoded
  end
  local function draw_kind(kind)
    local cursor=get(0x5d1580);local found
    for i=wall_limit()-1,cursor,-1 do
      if trunc(index(0x5d1800,i)/10)==kind and index(0x5d15a0,i)==1 then found=i;break end
    end
    if found==nil then
      for i=wall_limit()-1,cursor,-1 do
        if trunc(index(0x5d1800,i)/10)==kind then found=i;break end
      end
    end
    if found==nil then return -1 end
    local encoded=index(0x5d1800,found)
    decrement(encoded,index(0x5d15a0,found)==0)
    set(0x5d1800,found,index(0x5d1800,cursor))
    set(0x5d15a0,found,index(0x5d15a0,cursor))
    put(0x5d1580,cursor+1);return encoded
  end
  local function hand(seat_id)
    local base=seat_id==3 and 0x5d08e0 or 0x5d1280+seat_id*56
    local locks=seat_id==3 and 0x5d08a0 or 0x5d11c0+seat_id*56
    local counts={}
    for tile=0,39 do counts[tile]=0 end
    local total=0
    for at=0,13 do
      local encoded=index(base,at);local value=trunc(encoded/10)
      if index(locks,at)==0 and encoded>=0 and encoded<400 then
        assert(value<40,'invalid tile type');counts[value]=counts[value]+1;total=total+1
      end
    end
    return counts,total
  end
  local function valid(tile)return tile>=1 and tile<=37 and tile%10~=0 end
  local function melds(counts,remaining)
    if remaining==0 then return true end
    local first
    for tile=1,37 do if counts[tile]and counts[tile]>0 then first=tile;break end end
    if not first then return false end
    if counts[first]>=3 then
      counts[first]=counts[first]-3
      local good=melds(counts,remaining-3);counts[first]=counts[first]+3
      if good then return true end
    end
    if first<30 and first%10<=7 and(counts[first+1]or 0)>0 and(counts[first+2]or 0)>0 then
      for tile=first,first+2 do counts[tile]=counts[tile]-1 end
      local good=melds(counts,remaining-3)
      for tile=first,first+2 do counts[tile]=counts[tile]+1 end
      if good then return true end
    end
    return false
  end
  local function winning(counts,total)
    if total%3~=2 then return false end
    if total==14 then
      local pairs=0
      for tile=1,37 do if counts[tile]==2 then pairs=pairs+1 end end
      if pairs==7 then return true end
      local terminals={1,9,11,19,21,29,31,32,33,34,35,36,37};local pair=false;local all=true
      for _,tile in ipairs(terminals)do
        if(counts[tile]or 0)==0 then all=false end
        if(counts[tile]or 0)>=2 then pair=true end
      end
      if all and pair then return true end
    end
    for tile=1,37 do
      if(counts[tile]or 0)>=2 then
        counts[tile]=counts[tile]-2
        local good=melds(counts,total-2);counts[tile]=counts[tile]+2
        if good then return true end
      end
    end
    return false
  end
  local function waits(seat_id)
    local counts,total=hand(seat_id);local result={}
    if total%3==2 then
      -- Original evaluator temporarily excludes slot 13 before searching
      -- the next winning draw when inspecting a full fourteen-tile hand.
      local base=seat_id==3 and 0x5d08e0 or 0x5d1280+seat_id*56
      local last=trunc(index(base,13)/10)
      if last>=0 and last<40 then counts[last]=counts[last]-1;total=total-1 end
    end
    for tile=1,37 do if valid(tile)and(counts[tile]or 0)<4 then
      counts[tile]=(counts[tile]or 0)+1
      if winning(counts,total+1)then result[#result+1]=tile end
      counts[tile]=counts[tile]-1
    end end
    state.tiles.waits=state.tiles.waits or{};state.tiles.waits[seat_id]=result
    return result
  end
  -- Tile kinds span 1..37, unlike seat IDs.
  define(0x1cd5bca8,function(vm,args)
    assert(#args==1);local kind=integer(args[1]);assert(valid(kind),'invalid requested tile kind')
    return draw_kind(kind)
  end)
  define(0x62442ec6,function(vm,args)assert(#args==0);return draw_plain()end)
  define(0x0d36e2a7,function(vm,args)
    local player=seat(args);local available={}
    for kind=0,39 do available[kind]=index(0x5b4520,kind)end
    for at=0,13 do if index(0x5d11c0+player*56,at)==0 then
      local kind=index(0x5d1020+player*56,at)
      available[kind]=(available[kind]or 0)-1
      if available[kind]<0 then return 0 end
    end end
    for at=0,13 do
      if index(0x5d11c0+player*56,at)==0 then
        set(0x5d0f60+player*56,at,draw_kind(index(0x5d1020+player*56,at)))
      end
      set(0x5d1280+player*56,at,index(0x5d0f60+player*56,at))
    end
    return 1
  end)
  define(0x1f5a14d4,function(vm,args)
    assert(#args==0)
    local players=get(0x5d1578);local dealer=get(0x5d079c);local order=get(0x5d07a4)
    if players==3 and order==3 then order=2 end
    if players==2 then order=order~=0 and 2 or 0;dealer=dealer*2 end
    if players==2 or players==3 or players==4 then
      local count=players==3 and 3 or 4
      local values={}
      for value=0,count-1 do
        local target=(order+dealer+value)%count
        values[target]=value
      end
      set(0x5d1330,0,values[0])
      if players>=3 then set(0x5d1330,1,values[1])end
      if players==4 then set(0x5d1330,2,values[2])end
      put(0x5d07b0,values[players==4 and 3 or 2])
    end
    return 0
  end)
  define(0x23d5d11d,function(vm,args)return#waits(seat(args))end)
  define(0x5c9761c5,function(vm,args)assert(#args==0);return#waits(3)>0 and 1 or 0 end)
  define(0x45e32ea6,function(vm,args)
    local player=seat(args);local count=0
    while count<40 and index(0x5d1360+player*160,count)~=-1 do count=count+1 end
    local threshold=count<4 and 100 or count<7 and 70 or count<14 and 55 or count<18 and 40 or 30
    return random(100)<threshold and random(13)or 13
  end)
  define(0x2c18d5a9,function(vm,args)
    assert(#args==0)
    local counts,total=hand(3);local no_open=true
    for at=0,13 do if index(0x5d08a0,at)>0 then no_open=false end;set(0x5d1540,at,0)end
    local found=false
    if get(0x5d07b8)==0 then
      for at=0,13 do
        if index(0x5d08a0,at)==0 then
          local kind=trunc(index(0x5d08e0,at)/10)
          if valid(kind)then
            counts[kind]=(counts[kind]or 0)-1
            local can=false
            for tile=1,37 do if valid(tile)and(counts[tile]or 0)<4 then
              counts[tile]=(counts[tile]or 0)+1
              if winning(counts,total)then can=true end
              counts[tile]=counts[tile]-1
            end end
            counts[kind]=counts[kind]+1
            if can then set(0x5d1540,at,1);found=true end
          end
        end
      end
    end
    return found and no_open and 1 or 0
  end)
  local function draw_character()
    local players=get(0x5d1578);local order={0,1,2}
    if players==4 then
      local selection=random(3)
      if selection==1 then order={1,2,0}elseif selection==2 then order={2,0,1}end
    end
    local owner,bias,invert=-1,0,false
    for at=1,players-1 do
      local player=order[at];local character=index(0x5d0a10,player)
      if character==0x18 then owner,bias=player,6
      elseif character==0x29 then owner,bias=player,4
      elseif character==0x49 or character==0x52 then
        owner,bias,invert=player,4,character==0x52;break
      elseif character==0x21 then owner,bias=player,9
      elseif character==0x5a or character==0x41 or character==0x60 then
        owner,bias=player,character==0x60 and 10 or 9;break
      end
    end
    local chosen=draw_plain()
    if owner==-1 then return chosen end
    local function desirability(encoded)
      local wanted=trunc(encoded/10)+1;local value=0
      for at=0,13 do if index(0x5d1020+owner*56,at)==wanted then value=value+1 end end
      return value
    end
    local best=desirability(chosen)
    for attempt=1,bias do
      local cursor=get(0x5d1580);local candidate=draw_plain()
      put(0x5d1580,cursor)
      local score=desirability(candidate)
      local replace=invert and score<best or not invert and score>best
      local returned=replace and chosen or candidate
      local kind=trunc(returned/10)
      set(0x5b4480,kind,index(0x5b4480,kind)+1)
      set(0x5b4520,kind,index(0x5b4520,kind)+1)
      local target=cursor+random(wall_limit()-cursor)
      set(0x5d1800,cursor,index(0x5d1800,target))
      set(0x5d1800,target,returned)
      set(0x5d15a0,cursor,index(0x5d15a0,target));set(0x5d15a0,target,0)
      if replace then chosen,best=candidate,score end
    end
    return chosen
  end
  define(0x2e99e98c,function(vm,args)
    assert(#args==0);local at=0
    while at<=5 and index(0x4c7ecc,at)~=-1 do at=at+1 end
    if at<=5 then set(0x4c7ecc,at,draw_character());set(0x4c7efc,at,draw_character())end
    return 0
  end)
  define(0x50cd2dca,function(vm,args)
    assert(#args==0);local players=get(0x5d1578);assert(players>=2 and players<=4,'tile player mode')
    local ready={};local total=0;local active=false
    for player=0,players-2 do
      ready[player]=index(0x5d0d90,player)~=0
      if ready[player]then total=total+1 end
    end
    active=#waits(3)>0;if active then total=total+1 end
    local losses=players==2 and{0,15,0,0,0}or players==3 and{0,15,30,0,0}or{0,10,15,30,0}
    local gains=players==2 and{0,15,0,0,0}or players==3 and{0,30,15,0,0}or{0,30,15,10,136}
    for player=0,players-2 do
      if not ready[player]then set(0x5d1340,player,index(0x5d1340,player)-losses[total+1])
      elseif get(0x5d091c)==0 then set(0x5d1340,player,index(0x5d1340,player)+gains[total+1])end
    end
    if not active then put(0x5d0918,get(0x5d0918)-losses[total+1])
    elseif get(0x5d091c)==0 then put(0x5d0918,get(0x5d0918)+gains[total+1])end
    local dealer_ready=active and get(0x5d07b0)==0
    for player=0,players-2 do
      if ready[player]and index(0x5d1330,player)==0 then dealer_ready=true end
    end
    return dealer_ready and 1 or 0
  end)
  state.tiles_actions={get=get,put=put,index=index,set=set,random=random,
    draw_plain=draw_plain,draw_kind=draw_kind,draw_character=draw_character,
    waits=waits,winning=winning,hand=hand}
  return state.tiles_actions
end
return M
