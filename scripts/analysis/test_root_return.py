"""Execute original root-return phases and the enclosing game host's restart branch."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua, table_value
from port_common import ROOT, write_json


def run():
    reset=Original()
    sprite=reset.memory(bytes(0x630))
    reset.machine.mem_write(0x5bab28,struct.pack("<I",sprite))
    for offset in (0x18,0x1c,0x34,0x3c,0x5c,0x27c,0x3b0,0x3c8,0x3e0,0x500):
        reset.machine.mem_write(sprite+offset,struct.pack("<I",123))
    def skip_refresh(uc,at,size,data):
        esp=uc.reg_read(reset.reg.UC_X86_REG_ESP)
        uc.reg_write(reset.reg.UC_X86_REG_EIP,struct.unpack("<I",reset.read(esp,4))[0])
        uc.reg_write(reset.reg.UC_X86_REG_ESP,esp+4)
    for at in (0x4712e0,0x475630,0x46f380,0x4752d0,0x475d20):
        reset.machine.hook_add(unicorn.UC_HOOK_CODE,skip_refresh,begin=at,end=at)
    reset.call(0x470070,[])
    assert struct.unpack("<2I",reset.read(sprite+0x18,8))==(100000,100000)
    assert all(reset.read(sprite+offset,4)==bytes(4)
               for offset in (0x34,0x3c,0x5c,0x27c,0x3b0,0x3c8,0x3e0,0x500))
    banks=Original()
    for pointer,count in ((0x53465c,0x5348bc),(0x534660,0x5348c0),(0x534664,0x5348c4)):
        at=banks.memory(struct.pack("<4I",12,0,0,99))
        banks.machine.mem_write(pointer,struct.pack("<I",at))
        banks.machine.mem_write(count,struct.pack("<I",1))
    banks.call(0x44f2d0,[])
    assert all(banks.read(at,4)==bytes(4)for at in (0x5348bc,0x5348c0,0x5348c4))
    phases = []
    for phase, expected in ((0, 1), (1, 1), (2, 3), (3, 3), (4, -1)):
        o = Original()
        context = o.memory(bytes(0x5100))
        o.machine.mem_write(0x5d1a5c, struct.pack("<I", context))
        o.machine.mem_write(0x5d1a58, struct.pack("<I", context + 0x28))
        o.machine.mem_write(context + 0x5034, struct.pack("<I", phase))
        o.call(0x43d140, [])
        actual = struct.unpack("<i", o.read(context + 0x5034, 4))[0]
        assert actual == expected, (phase, actual)
        phases.append({"phase": phase, "after_root_return": actual})
    o = Original()
    reached = []
    def branch(uc, at, size, data):
        reached.append(at)
        uc.reg_write(o.reg.UC_X86_REG_EIP, o.STOP)
    o.machine.hook_add(unicorn.UC_HOOK_CODE, branch, begin=0x45ac70, end=0x45ac70)
    o.machine.reg_write(o.reg.UC_X86_REG_ESI, 5)
    o.call(0x45adc6, [])
    assert reached == [0x45ac70]
    lua = create_lua()
    lua.globals()["V"] = lua.execute((ROOT / "runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    lua.globals()["OPS"] = lua.execute((ROOT / "resources/scripts/opcodes.lua").read_text(encoding="utf-8"))
    actual = table_value(lua.execute("""
      local scripts={
        ['start.mjo']={name='start.mjo',main=0,functions={},
          code={{0,0x835,10,0},{8,0x800,0},{14,0x82b}}},
        ['ending.mjo']={name='ending.mjo',main=0,functions={},code={{0,0x800,0},{6,0x82b}}}}
      local resets,begins={},0
      local v=V.new{opcodes=OPS,loader=function(n)return scripts[n]end,
        restart_on_return=true,
        syscall=function(v)
          begins=begins+1
          if begins==1 then v:exec(v:load('ending'),{})
          else v.waiting={kind='input'}end
          return nil,true
        end,
        after_init=function(v,done,reason)
          resets[#resets+1]=reason
          v:resume(done)
        end}
      v:begin('start');v:run(1000)
      assert(begins==2 and v.status~='finished'and v.waiting.kind=='input')
      assert(v.frames[1].script.name=='start.mjo'and #v.frames==1)
      assert(#resets==1 and resets[1]==5)
      return {status=v.status,reset_reason=resets[1],start_entries=begins}
    """))
    report = {"status": "passed", "source_exe_sha256": Original.IDENTITY,
              "original_sprite_reset":"0x470070",
              "original_volatile_bank_reset":"0x44F2D0",
              "original_phases": phases, "original_host_restart": hex(reached[0]),
              "vm": actual, "scope": "actual original root return and host branch, independent exec/return program"}
    write_json(ROOT / "reports/root_return_tests.json", report)
    print("root return: original phases, host restart and VM reentry passed")
    return report


if __name__ == "__main__":
    run()
