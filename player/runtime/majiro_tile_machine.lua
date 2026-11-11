-- Bounded platform-neutral execution of recovered pure integer tile recipes.
-- Only the random stream and tile-reservation helper are external operations.
local M={}
local U=4294967296
local function u(value)return value%U end
local function s(value)value=u(value);return value>=2147483648 and value-U or value end
local function trunc(value)return value<0 and math.ceil(value)or math.floor(value)end
local function band(a,b)
  local result,power=0,1;a,b=u(a),u(b)
  for _=1,32 do
    if a%2==1 and b%2==1 then result=result+power end
    a,b,power=math.floor(a/2),math.floor(b/2),power*2
  end
  return result
end
local function bxor(a,b)return u(a+b-2*band(a,b))end
function M.run(data,context)
  local r={eax=0,ebx=0,ecx=0,edx=0,esi=0,edi=0,ebp=0,esp=0x70008000}
  for i=0,7 do r['xmm'..i]={0,0,0,0}end
  local scratch={};local flags={z=false,s=false,o=false,c=false}
  local partial={al={'eax',0,8},ah={'eax',8,8},ax={'eax',0,16},
    bl={'ebx',0,8},bh={'ebx',8,8},bx={'ebx',0,16},
    cl={'ecx',0,8},ch={'ecx',8,8},cx={'ecx',0,16},
    dl={'edx',0,8},dh={'edx',8,8},dx={'edx',0,16}}
  local function register(name)
    local part=partial[name]
    return part and math.floor(r[part[1]]/2^part[2])%2^part[3]or r[name]
  end
  local function byte(at)
    local base=at-at%4
    local word
    if at>=0x70000000 and at<0x70010000 then word=scratch[base]or 0
    else word=context.memory[base]
      if word==nil then word=data.constants[base]or 0 end
    end
    return math.floor(u(word)/2^(at%4*8))%256
  end
  local function read(at,size)
    local value=0
    for i=0,size-1 do value=value+byte(u(at+i))*2^(i*8)end
    return value
  end
  local function write(at,value,size)
    for i=0,size-1 do
      local address=u(at+i);local base=address-address%4;local power=2^(address%4*8)
      local old=byte(address);local next=math.floor(u(value)/2^(i*8))%256
      if address>=0x70000000 and address<0x70010000 then
        scratch[base]=u((scratch[base]or 0)+(next-old)*power)
      else
        assert(address>=0x4a9000 and address<0x600000,'tile recipe memory write outside state')
        local current=context.memory[base]
        if current==nil then current=data.constants[base]or 0 end
        context.memory[base]=s(current+(next-old)*power)
      end
    end
  end
  local function address(op)
    return u((r[op[3]]or 0)+(r[op[4]]or 0)*op[5]+op[6])
  end
  local function value(op)
    if op[1]=='i'then return u(op[2])end
    if op[1]=='r'then return register(op[2])end
    return read(address(op),op[2])
  end
  local function assign(op,v)
    if op[1]=='r'then
      local part=partial[op[2]]
      if part then r[part[1]]=u(r[part[1]]+(v%2^part[3]-register(op[2]))*2^part[2])
      else r[op[2]]=type(v)=='table'and{unpack(v)}or u(v)end
    else write(address(op),v,op[2])end
  end
  local function push(v)r.esp=u(r.esp-4);write(r.esp,v,4)end
  local function pop()local v=read(r.esp,4);r.esp=u(r.esp+4);return v end
  local function arithmetic(a,b,result,sub)
    a,b,result=u(a),u(b),u(result)
    flags.z=result==0;flags.s=result>=2147483648
    flags.c=sub and a<b or not sub and a+b>=U
    flags.o=sub and((s(a)<0)~=(s(b)<0))and((s(result)<0)~=(s(a)<0))or
      not sub and((s(a)<0)==(s(b)<0))and((s(result)<0)~=(s(a)<0))
    return result
  end
  local function logical(result)
    result=u(result);flags.z=result==0;flags.s=result>=2147483648;flags.c=false;flags.o=false
    return result
  end
  local function condition(name)
    if name=='e'or name=='z'then return flags.z
    elseif name=='ne'or name=='nz'then return not flags.z
    elseif name=='l'then return flags.s~=flags.o
    elseif name=='ge'then return flags.s==flags.o
    elseif name=='le'then return flags.z or flags.s~=flags.o
    elseif name=='g'then return not flags.z and flags.s==flags.o
    elseif name=='a'then return not flags.c and not flags.z
    elseif name=='ae'then return not flags.c
    elseif name=='b'then return flags.c
    elseif name=='be'then return flags.c or flags.z
    elseif name=='s'then return flags.s
    elseif name=='ns'then return not flags.s
    end
    error('unsupported tile recipe branch '..name)
  end
  local function vector(op,bytes)
    if op[1]=='r'then
      local v=r[op[2]]
      if type(v)=='number'then return{v,0,0,0}end
      return{unpack(v)}
    end
    local at=address(op);local out={}
    for i=0,3 do out[i+1]=i*4<bytes and read(at+i*4,4)or 0 end
    return out
  end
  local function vector_assign(op,v,bytes)
    if op[1]=='r'then r[op[2]]={unpack(v)}
    else
      local at=address(op)
      for i=0,bytes/4-1 do write(at+i*4,v[i+1],4)end
    end
  end
  push(0);local pc=data.entry
  for steps=1,2000000 do
    if pc==0 then return s(r.eax),steps end
    if context.trace then context.trace(pc,r.eax,r.ebx,r.ecx,r.edx,r.esi,r.edi,r.esp,r.ebp)end
    local ins=assert(data.instructions[pc],'unconverted tile instruction '..string.format('%x',pc))
    local op,next,a,b=ins[1],ins[2],ins[3],ins[4];pc=next
    if op=='mov'then assign(a,value(b))
    elseif op=='lea'then assign(a,address(b))
    elseif op=='push'then push(value(a))
    elseif op=='pop'then assign(a,pop())
    elseif op=='ret'then pc=pop();if a then r.esp=u(r.esp+value(a))end
    elseif op=='call'then
      local target=value(a)
      if target==0x48999c then r.eax=context.random(32768);r.ecx=r.eax
      elseif target==0x48a437 then
        -- Compiler stack-cookie check has no Majiro-visible state.
      elseif target==0x455cb0 then r.eax=u(context.difficulty or 0)
      elseif target==0x467910 then
        local kind=s(read(r.esp,4));local at=context.reserve(kind);r.eax=u(at)
      else assert(data.instructions[target],'unconverted tile helper');push(next);pc=target end
    elseif op=='jmp'then pc=value(a);assert(data.instructions[pc],'unconverted tile jump target')
    elseif op:sub(1,1)=='j'then if condition(op:sub(2))then pc=value(a)end
    elseif op:sub(1,4)=='cmov'then if condition(op:sub(5))then assign(a,value(b))end
    elseif op=='cmp'then arithmetic(value(a),value(b),value(a)-value(b),true)
    elseif op=='test'then logical(band(value(a),value(b)))
    elseif op=='add'then assign(a,arithmetic(value(a),value(b),value(a)+value(b),false))
    elseif op=='sub'or op=='sbb'then
      local borrow=op=='sbb'and flags.c and 1 or 0
      local av,bv=value(a),value(b)+borrow;assign(a,arithmetic(av,bv,av-bv,true))
    elseif op=='inc'or op=='dec'then
      local carry=flags.c;local av=value(a);local amount=op=='inc'and 1 or-1
      assign(a,arithmetic(av,1,av+amount,amount<0));flags.c=carry
    elseif op=='neg'then local av=value(a);assign(a,arithmetic(0,av,-av,true))
    elseif op=='xor'then assign(a,logical(bxor(value(a),value(b))))
    elseif op=='and'then assign(a,logical(band(value(a),value(b))))
    elseif op=='or'then local av,bv=value(a),value(b);assign(a,logical(av+bv-band(av,bv)))
    elseif op=='shl'then assign(a,logical(value(a)*2^(value(b)%32)))
    elseif op=='cdq'then r.edx=s(r.eax)<0 and 0xffffffff or 0
    elseif op=='idiv'then
      local divisor=s(value(a));assert(divisor~=0,'tile recipe division by zero')
      local numerator=s(r.edx)*U+r.eax;local quotient=trunc(numerator/divisor)
      assert(quotient>=-2147483648 and quotient<2147483648,'tile recipe division overflow')
      r.eax=u(quotient);r.edx=u(numerator-quotient*divisor)
    elseif op=='movdqu'or op=='movdqa'or op=='movq'then
      local bytes=op=='movq'and 8 or 16;vector_assign(a,vector(b,bytes),bytes)
    elseif op=='xorps'then local av,bv=vector(a,16),vector(b,16);local out={}
      for i=1,4 do out[i]=bxor(av[i],bv[i])end;vector_assign(a,out,16)
    elseif op=='paddd'then local av,bv=vector(a,16),vector(b,16);local out={}
      for i=1,4 do out[i]=u(av[i]+bv[i])end;vector_assign(a,out,16)
    elseif op=='rep movsd'then
      assert(r.ecx<10000,'tile recipe copy bound')
      for i=1,r.ecx do write(r.edi,read(r.esi,4),4);r.edi=u(r.edi+4);r.esi=u(r.esi+4)end;r.ecx=0
    elseif op~='nop'then error('unsupported tile recipe instruction '..op)end
  end
  error('tile recipe instruction budget exceeded')
end
return M
