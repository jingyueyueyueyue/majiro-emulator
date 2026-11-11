-- Portable execution of the recovered pure tile algorithm operation graph.
-- All addresses are checked numeric keys in private serializable memory; none
-- are process pointers and no original EXE is loaded or executed by the player.
local M={}
local U32=4294967296
local function u(v)return v%U32 end
local function s(v)v=u(v);return v>=2147483648 and v-U32 or v end
local function trunc(v)return v<0 and math.ceil(v)or math.floor(v)end
local function bit(a,b,operation)
  a,b=u(a),u(b);local result,power=0,1
  for _=1,32 do
    local x,y=a%2,b%2
    if(operation=='and'and x==1 and y==1)or(operation=='or'and(x==1 or y==1))
      or(operation=='xor'and x~=y)then result=result+power end
    a,b,power=math.floor(a/2),math.floor(b/2),power*2
  end
  return result
end
local function product(a,b,signed)
  local aa,bb=u(a),u(b)
  local a0,a1,b0,b1=aa%65536,math.floor(aa/65536),bb%65536,math.floor(bb/65536)
  local low=a0*b0
  local middle=a0*b1+a1*b0+math.floor(low/65536)
  local high=u(a1*b1+math.floor(middle/65536))
  low=(low%65536)+(middle%65536)*65536
  if signed then
    if aa>=2147483648 then high=u(high-bb)end
    if bb>=2147483648 then high=u(high-aa)end
  end
  return low,high
end
function M.install(define,state,options)
  local program=options.modules('runtime/majiro_tile_program.lua')
  local argument_counts={[0xaefc64da]=1,[0xb758ac4e]=1,[0xbb9d7a7f]=1,[0xbef4edfa]=2,
    [0xca5c98f2]=1,[0xd481e1a2]=2,[0xd583668c]=0,[0xda60f137]=1,[0xe316a8f2]=0,
    [0xe5a2ce14]=3,[0xebb5886c]=1,[0xf0fd9886]=1,[0xf5e5be93]=0,[0xfaf17b1c]=0,
    [0xfb7fb744]=1,[0xefd59130]=0,[0x0d36e2a7]=1,[0x1cd5bca8]=1,
    [0x1f5a14d4]=0,[0x23d5d11d]=1,[0x2c18d5a9]=0,[0x2e99e98c]=0,
    [0x45e32ea6]=1,[0x50cd2dca]=0,[0x5c9761c5]=0,[0x62442ec6]=0,
    [0x6ddeb30e]=1,[0x850eecf4]=2,[0x269bda78]=0}
  state.tiles=state.tiles or{}
  state.tiles.memory=state.tiles.memory or{}
  local function execute(vm,hash,args,raw)
    assert(#args==argument_counts[hash],'recovered tile syscall argument count')
    local memory=state.tiles.memory
    local temporary={}
    local registers={eax=0,ecx=0,edx=0,ebx=0,esp=0x200ff000,ebp=0,esi=0,edi=0}
    local vectors={}
    local flags={z=false,c=false,o=false,n=false}
    local result
    local budget=0
    local function valid(address,write)
      assert(type(address)=='number'and address%1==0,'tile address must be integral')
      if address>=0x20000000 and address<0x20100000 then return temporary end
      assert((address>=0x4a0000 and address<0x5d3000)
        or(not write and address>=0x45d000 and address<0x467c50),
        string.format('tile algorithm memory outside recovered regions: %08x',address))
      return memory
    end
    local function word(address)
      local target=valid(address,false)
      return u(target[address]or program.constants[address]or 0)
    end
    local function byte(address)
      local base=address-address%4
      return math.floor(word(base)/256^(address%4))%256
    end
    local function read(address,size)
      if size==4 and address%4==0 then return word(address)end
      local value=0
      for i=size-1,0,-1 do value=value*256+byte(address+i)end
      return value
    end
    local function write(address,value,size)
      local target=valid(address,true)
      if size==4 and address%4==0 then target[address]=s(value);return end
      value=u(value)
      for i=0,size-1 do
        local at=address+i;local base=at-at%4;local power=256^(at%4)
        local previous=word(base)
        target[base]=s(previous-(math.floor(previous/power)%256)*power+(value%256)*power)
        value=math.floor(value/256)
      end
    end
    local aliases={al={'eax',0,1},ah={'eax',1,1},ax={'eax',0,2},
      cl={'ecx',0,1},ch={'ecx',1,1},cx={'ecx',0,2},
      dl={'edx',0,1},dh={'edx',1,1},dx={'edx',0,2},
      bl={'ebx',0,1},bh={'ebx',1,1},bx={'ebx',0,2},
      si={'esi',0,2},di={'edi',0,2},bp={'ebp',0,2},sp={'esp',0,2}}
    local function reg(name)
      if name:sub(1,3)=='xmm'then return vectors[name]or{0,0,0,0}end
      local alias=aliases[name]
      if alias then return math.floor(registers[alias[1]]/256^alias[2])%256^alias[3]end
      return assert(registers[name],'unknown tile register '..name)
    end
    local function setreg(name,value)
      if name:sub(1,3)=='xmm'then vectors[name]=value;return end
      local alias=aliases[name]
      if alias then
        local shift,limit=256^alias[2],256^alias[3]
        local previous=registers[alias[1]]
        registers[alias[1]]=u(previous-(math.floor(previous/shift)%limit)*shift+(value%limit)*shift)
      else assert(registers[name]~=nil,'unknown tile register');registers[name]=u(value)end
    end
    local function address(op)
      return u((op[2]~=''and reg(op[2])or 0)+(op[3]~=''and reg(op[3])*op[4]or 0)+op[5])
    end
    local function get(op)
      if op[1]=='i'then return u(op[2])end
      if op[1]=='r'then return reg(op[2])end
      local at=address(op)
      if op[6]>4 then
        local value={0,0,0,0}
        for i=1,op[6]/4 do value[i]=read(at+(i-1)*4,4)end
        return value
      end
      return read(at,op[6])
    end
    local function put(op,value)
      if op[1]=='r'then setreg(op[2],value)
      else
        local at=address(op)
        if type(value)=='table'then
          for i=1,op[6]/4 do write(at+(i-1)*4,value[i]or 0,4)end
        else write(at,value,op[6])end
      end
    end
    local function push(value)registers.esp=u(registers.esp-4);write(registers.esp,value,4)end
    local function pop()local value=read(registers.esp,4);registers.esp=u(registers.esp+4);return value end
    local function arithmetic(a,b,value,subtract,size,preserve_c)
      local limit=256^(size or 4);local half=limit/2
      a,b=a%limit,b%limit;local r=value%limit
      local old=flags.c
      flags.z=r==0;flags.n=r>=half
      flags.c=subtract and a<b or(not subtract and a+b>=limit)
      local as,bs=a>=half,b>=half
      flags.o=subtract and(as~=bs and flags.n~=as)or(not subtract and as==bs and flags.n~=as)
      if preserve_c then flags.c=old end
    end
    local function condition(code)
      if code=='e'or code=='z'then return flags.z end
      if code=='ne'or code=='nz'then return not flags.z end
      if code=='l'then return flags.n~=flags.o end
      if code=='le'then return flags.z or flags.n~=flags.o end
      if code=='g'then return not flags.z and flags.n==flags.o end
      if code=='ge'then return flags.n==flags.o end
      if code=='b'or code=='c'then return flags.c end
      if code=='be'then return flags.c or flags.z end
      if code=='a'then return not flags.c and not flags.z end
      if code=='ae'or code=='nc'then return not flags.c end
      if code=='s'then return flags.n end
      if code=='ns'then return not flags.n end
      error('unknown tile condition '..code)
    end
    local bindings={}
    local function sync_in()
      if raw then return end
      for _,binding in ipairs(program.bindings)do
        local value=vm.globals[binding[2]][binding[1]]
        assert(value~=nil,'tile source variable missing: '..binding[5])
        if binding[4]==1 then
          assert(type(value)=='table'and value.kind=='array'and value.element_type==0,
            'tile source integer array required: '..binding[5])
          local count=1
          for _,dimension in ipairs(value.dims)do count=count*dimension end
          assert(count<=4096,'tile source array exceeds bound')
          bindings[#bindings+1]={binding,value,count}
          for i=0,count-1 do write(binding[3]+i*4,value.values[i]or 0,4)end
        else
          assert(type(value)=='number'and value%1==0,'tile scalar required: '..binding[5])
          write(binding[3],value,4);bindings[#bindings+1]={binding,value,0}
        end
      end
    end
    local function sync_out()
      if raw then return end
      for _,item in ipairs(bindings)do
        local binding,value,count=item[1],item[2],item[3]
        if binding[4]==1 then
          for i=0,count-1 do value.values[i]=s(read(binding[3]+i*4,4))end
        else vm.globals[binding[2]][binding[1]]=s(read(binding[3],4))end
      end
    end
    local stack_top=0x20001000+(#args-1)*16
    for index,value in ipairs(args)do
      assert(type(value)=='number'and value%1==0,'tile algorithm integer argument required')
      write(stack_top-(index-1)*16+12,value,4)
    end
    registers.ecx=#args;push(0)
    local at=assert(program.entries[hash],'unknown recovered tile entry')
    while at~=0 do
      budget=budget+1;assert(budget<=5000000,'tile algorithm instruction budget exceeded')
      local boundary=program.boundaries[at]
      if boundary then
        if boundary=='sync_in'then sync_in()
        elseif boundary=='sync_out'then sync_out()
        elseif boundary=='arguments'then registers.eax=stack_top
        elseif boundary=='result'then result=s(read(registers.esp+4,4));registers.eax=u(result)
        elseif boundary=='cookie'then
        elseif boundary=='opponent'then
          assert(state.tiles_actions,'opponent recipe engine is not installed').run_ai()
        elseif boundary=='random'then
          state.random_seed=((state.random_seed or 1)*214013+2531011)%U32
          registers.eax=math.floor(state.random_seed/65536)%32768
          registers.ecx=registers.eax
        elseif boundary=='copy'then
          local dst,src,count=read(registers.esp+4,4),read(registers.esp+8,4),read(registers.esp+12,4)
          assert(count<=65536,'tile copy exceeds bound')
          local copied={}
          for i=0,count-1 do copied[i]=read(src+i,1)end
          for i=0,count-1 do write(dst+i,copied[i],1)end
          registers.eax=dst
        elseif boundary=='fill'then
          local dst,value,count=read(registers.esp+4,4),read(registers.esp+8,4)%256,read(registers.esp+12,4)
          assert(count<=65536,'tile fill exceeds bound')
          for i=0,count-1 do write(dst+i,value,1)end
          registers.eax=dst
        elseif boundary=='diagnostic'then error('original tile algorithm reported invalid input')
        else error('unknown recovered tile boundary')end
        at=pop()
      else
        local row=assert(program.code[at],string.format('tile operation missing %08x',at))
        local following,op,a,b,c=row[1],row[2],row[3],row[4],row[5]
        at=following
        if op=='mov'or op=='movdqu'or op=='movdqa'or op=='movq'or op=='movd'then
          local value=get(b)
          if op=='movq'and a[1]=='r'then value={value[1],value[2],0,0}
          elseif op=='movd'then value=type(value)=='table'and value[1]or
            (a[1]=='r'and a[2]:sub(1,3)=='xmm'and{value,0,0,0}or value)end
          put(a,value)
        elseif op=='lea'then put(a,address(b))
        elseif op=='push'then push(get(a))
        elseif op=='pop'then put(a,pop())
        elseif op=='ret'then at=pop();if a then registers.esp=u(registers.esp+get(a))end
        elseif op=='call'then push(following);at=get(a)
        elseif op=='jmp'then at=get(a)
        elseif op:sub(1,1)=='j'then if condition(op:sub(2))then at=get(a)end
        elseif op:sub(1,4)=='cmov'then if condition(op:sub(5))then put(a,get(b))end
        elseif op:sub(1,3)=='set'then put(a,condition(op:sub(4))and 1 or 0)
        elseif op=='cmp'or op=='sub'or op=='add'or op=='sbb'or op=='inc'or op=='dec'or op=='neg'then
          local left,right=get(a),b and get(b)or 1;local value
          local subtract=op~='add'and op~='inc'
          if op=='neg'then right,left=left,0 end
          if op=='sbb'then right=right+(flags.c and 1 or 0)end
          value=subtract and left-right or left+right
          arithmetic(left,right,value,subtract,(a[1]=='r'and a[3]or a[6]),op=='inc'or op=='dec')
          if op~='cmp'then put(a,u(value))end
        elseif op=='test'or op=='and'or op=='or'or op=='xor'then
          local value=bit(get(a),get(b),op=='test'and'and'or op)
          flags.z=value==0;flags.n=value>=2147483648;flags.c=false;flags.o=false
          if op~='test'then put(a,value)end
        elseif op=='shl'or op=='shr'or op=='sar'then
          local value,count=get(a),get(b)%32
          if count>0 then
            local previous=value
            if op=='shl'then value=u(value*2^count);flags.c=math.floor(previous/2^(32-count))%2==1
            else value=math.floor((op=='sar'and s(value)or value)/2^count);flags.c=math.floor(previous/2^(count-1))%2==1 end
            value=u(value);put(a,value);flags.z=value==0;flags.n=value>=2147483648
            flags.o=count==1 and(op=='shl'and(flags.n~=flags.c)or op=='shr'and previous>=2147483648)or false
          end
        elseif op=='cdq'then registers.edx=registers.eax>=2147483648 and 4294967295 or 0
        elseif op=='imul'or op=='mul'then
          if not b then registers.eax,registers.edx=product(registers.eax,get(a),op=='imul')
          elseif c then local low=product(get(b),get(c),true);put(a,low)
          else local low=product(get(a),get(b),true);put(a,low)end
        elseif op=='idiv'then
          local divisor=s(get(a));assert(divisor~=0,'tile division by zero')
          local numerator=s(registers.edx)*U32+registers.eax
          assert(math.abs(numerator)<=9007199254740991,'tile division exceeds exact portable bound')
          local quotient=trunc(numerator/divisor);assert(quotient>=-2147483648 and quotient<=2147483647,'tile division overflow')
          registers.eax=u(quotient);registers.edx=u(numerator-quotient*divisor)
        elseif op=='xorps'then put(a,{0,0,0,0})
        elseif op=='pmulld'or op=='paddd'then
          local left,right=get(a),get(b);local value={}
          for i=1,4 do value[i]=op=='paddd'and u(left[i]+right[i])or product(left[i],right[i],true)end
          put(a,value)
        elseif op=='rep movsd'or op=='rep stosd'then
          assert(registers.ecx<=16384,'tile repeated copy exceeds bound')
          for _=1,registers.ecx do
            write(registers.edi,op=='rep movsd'and read(registers.esi,4)or registers.eax,4)
            registers.edi=u(registers.edi+4)
            if op=='rep movsd'then registers.esi=u(registers.esi+4)end
          end
          registers.ecx=0
        elseif op~='nop'then error('unsupported recovered tile operation '..op)end
      end
    end
    state.tiles.last_operation_count=budget
    return result or 0
  end
  for hash in pairs(program.entries)do
    local number=hash
    define(number,function(vm,args)return execute(vm,number,args,false)end)
  end
  local api={execute=function(vm,hash,args)return execute(vm,hash,args,true)end,program=program}
  -- The immutable operation graph belongs to the module, not saved gameplay.
  state.tiles_analysis={execute=api.execute}
  return api
end
return M
