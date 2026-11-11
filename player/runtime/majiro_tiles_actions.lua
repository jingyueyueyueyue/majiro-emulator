-- Recovered character-specific opponent recipes and shared numeric state.
-- Other tile entry points use the exact operation graph in tiles_analysis.
local M={}
local function trunc(value)return value<0 and math.ceil(value)or math.floor(value)end
function M.install(define,state,options)
  local recipe=options.modules('scripts/tile_ai.lua')
  local machine=options.modules('runtime/majiro_tile_machine.lua')
  state.tiles=state.tiles or{}
  state.tiles.memory=state.tiles.memory or{}
  local function memory()return state.tiles.memory end
  local function get(address)
    local value=memory()[address]
    if value==nil then value=recipe.constants[address]or 0 end
    return value>=2147483648 and value-4294967296 or value
  end
  local function put(address,value)memory()[address]=value;return value end
  local function index(base,i)return get(base+i*4)end
  local function set(base,i,value)return put(base+i*4,value)end
  local function random(divisor)
    assert(divisor>0,'tile random divisor')
    state.random_seed=((state.random_seed or 1)*214013+2531011)%4294967296
    return math.floor(state.random_seed/65536)%32768%divisor
  end
  local limits={[0]=15,[1]=10,[2]=136,[3]=108,[4]=136}
  local function reserve(kind)
    local cursor=get(0x5d1580)
    local limit=assert(limits[get(0x5d1578)],'tile player mode')
    for at=limit-1,cursor,-1 do
      if index(0x5d15a0,at)==0 and trunc(index(0x5d1800,at)/10)==kind then
        set(0x5b4480,kind,index(0x5b4480,kind)-1);set(0x5d15a0,at,1)
        return index(0x5d1800,at)
      end
    end
    return -1
  end
  local function run_ai()
    local result,steps=machine.run(recipe,{memory=memory(),random=random,reserve=reserve,
      difficulty=state.settings.nanido or 0,trace=options.tile_trace})
    state.tiles.last_ai_steps=steps
    return 0
  end
  state.tiles_actions={get=get,put=put,index=index,set=set,random=random,reserve=reserve,run_ai=run_ai}
  return state.tiles_actions
end
return M
