"""Exercise every decoded opcode once; independently check array identity against x86."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua, table_value
from port_common import ROOT, write_json


def array_original(address, a, b):
    original = Original()
    slots = original.memory(struct.pack("<8I", 0, 0, 0, a, 0, 0, 0, b))
    result, pops = [], []
    def boundary(uc, at, size, data):
        esp = uc.reg_read(original.reg.UC_X86_REG_ESP)
        ret = struct.unpack("<I", original.read(esp, 4))[0]
        if at == 0x44ec90:
            uc.reg_write(original.reg.UC_X86_REG_EAX, slots+16)
        elif at == 0x44e9a0:
            pops.append(at)
        else:
            result.append(struct.unpack("<I", original.read(esp+4, 4))[0])
        uc.reg_write(original.reg.UC_X86_REG_ESP, esp+4)
        uc.reg_write(original.reg.UC_X86_REG_EIP, ret)
    for address_hook in (0x44ec90, 0x44e9a0, 0x44eab0):
        original.machine.hook_add(unicorn.UC_HOOK_CODE, boundary,
                                  begin=address_hook, end=address_hook)
    original.call(address, [])
    assert len(pops) == 2 and len(result) == 1
    return result[0]


def run():
    lua = create_lua()
    mod = lua.execute((ROOT/"runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    ops = lua.execute((ROOT/"resources/scripts/opcodes.lua").read_text(encoding="utf-8"))
    smoke = lua.eval("""
    function(mod,ops,opcode)
      local name=ops[opcode][1]
      local arr={kind='array',element_type=0,dims={3},values={[0]=4}}
      local row={0,opcode}
      local stack={7,2}
      if name=='line'then row[3]=1;stack={}
      elseif name=='argcheck'or name=='alloca'then row[3]={};stack={}
      elseif name=='ldc.i'or name=='ldc.r'then row[3]=2;stack={}
      elseif name=='ldstr'then row[3]='x';stack={}
      elseif name=='ret'then stack={0}
      elseif name=='call'or name=='callp'then row[3]=42;row[4]=0;row[5]=0;stack={}
      elseif name=='syscall'or name=='syscallp'then row[3]=42;row[4]=0;stack={}
      elseif name=='switch'then row[3]={100};stack={0}
      elseif name=='br'or name:match('^b.*case$')or name:match('^bsel')or
             name=='brtrue'or name=='brfalse'then row[3]=100;stack={1}
      elseif name=='ld'or name=='ldelem'or name:match('^st')then
        local t=name:match('sarr$')and 5 or name:match('rarr$')and 4 or
          name:match('iarr$')and 3 or name:match('%.s$')and 2 or name:match('%.r$')and 1 or 0
        row[3]=32+t*256+(name:find('elem',1,true)and 2048 or 0);row[4]=99;row[5]=-1
        stack={t>=3 and arr or t==2 and 'x' or 2}
        if name:find('elem',1,true)then
          arr.element_type=t;arr.values[0]=t==2 and 'a'or 7
          if name=='ldelem'then stack={0}else stack[#stack+1]=0 end
        elseif name=='ld'then stack={}end
      elseif name:match('arr$')then stack={arr,arr}
      elseif name:match('%.s$')then stack={'a','b'}
      elseif name=='text'or name=='ctrl'then row[3]=name=='text'and'x'or'w';stack={}
      end
      local script={name='all-opcodes.mjo',main=0,functions={{42,200}},
        code={row,{100,0x800,0},{106,0x82b},{200,0x800,0},{206,0x82b}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=function()return 7 end,text_ops={execute=function()end}}
      vm:begin(script.name)
      vm:frame().case=1
      vm.globals[2][0x11f91fd3]=1
      vm:frame().handlers={{kind='left',offset=100,stack_size=0}}
      if name:find('elem',1,true)then vm.globals[1][99]=arr
      elseif name:match('^st')then
        vm.globals[1][99]=name:match('%.s$')and'a'or name:match('arr$')and arr or 7
      else vm.globals[1][99]=1 end
      for _,v in ipairs(stack)do vm:push(v)end
      local ok,err=pcall(function()vm:step()end)
      return ok,tostring(err or''),vm
    end
    """)
    cases = []
    for opcode, spec in sorted(ops.items()):
        ok, error, _ = smoke(mod, ops, opcode)
        assert ok, (hex(opcode), spec[1], error)
        cases.append({"opcode": "%04x" % opcode, "name": spec[1]})
    original_cases = []
    for equal, address in ((True, 0x43bb90), (False, 0x43ce30)):
        for same in (True, False):
            value = array_original(address, 0x123456, 0x123456 if same else 0x345678)
            assert value == int(same == equal)
            original_cases.append({"equal_operator": equal, "same_object": same, "result": value})
    for opcode in (0x15b, 0x15c, 0x15d, 0x163, 0x164, 0x165):
        for same in (True, False):
            lua.globals()["TEST_SAME"] = same
            lua.globals()["TEST_OP"] = opcode
            lua.globals()["TEST_MOD"], lua.globals()["TEST_OPS"] = mod, ops
            value = lua.execute("""
              local a={kind='array',dims={1},element_type=0,values={[0]=5}}
              local b=TEST_SAME and a or {kind='array',dims={1},element_type=0,values={[0]=5}}
              local s={name='arrays',main=0,functions={},code={{0,TEST_OP},{2,0x82b}}}
              local v=TEST_MOD.new{loader=function()return s end,opcodes=TEST_OPS,syscall=function()end}
              v:begin('arrays');v:push(a);v:push(b);v:step();return v:pop()
            """)
            assert value == int(same == (opcode < 0x160))
    write_json(ROOT/"reports/opcode_execution_tests.json", {
        "status": "passed", "opcodes": len(cases), "cases": cases,
        "array_original_cases": original_cases, "array_lua_cases": 12,
        "source_exe_sha256": Original.IDENTITY,
        "scope": "all parser opcodes enter an executor; array equality separately compares original semantics"})
    print("opcode executor:", len(cases), "dispatch fixtures; array identity original/Lua checks passed")


if __name__ == "__main__":
    run()
