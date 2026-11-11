"""Compare mouse selection slots and cleanup with the original executable."""
import struct

from original_x86 import Original
from lua_bridge import create_lua, table_value
from test_vm import HARNESS
from port_common import ROOT, write_json


def original_slots():
    original = Original()
    context = original.memory(bytes(0x5100))
    frame = context + 0x28
    operand = original.memory(struct.pack("<i", 100))
    original.machine.mem_write(0x5d1a5c, struct.pack("<I", context))
    original.machine.mem_write(0x5d1a58, struct.pack("<I", frame))
    rows = []
    for name, address, slot in (
        ("left", 0x43be70, 0x14), ("right", 0x43bf50, 0x18),
        ("middle", 0x43bee0, 0x1c), ("any", 0x43bfc0, None),
    ):
        for _ in range(2):
            original.machine.mem_write(frame, struct.pack("<I", operand))
            original.call(address, [])
            values = struct.unpack("<3I", original.read(frame + 0x14, 12))
            if slot is None:
                assert values == (operand + 104,) * 3
            else:
                assert values[(slot - 0x14) // 4] == operand + 104
        rows.append({"operation": name, "repeated_registration": True})
    original.machine.mem_write(frame + 0x24, struct.pack("<I", 12345))
    for _ in range(3):
        original.call(0x43c810, [])
        assert original.read(frame + 0x14, 12) == bytes(12)
        assert struct.unpack("<I", original.read(frame + 0x24, 4))[0] == 12345
    original.call(0x43d140, [])
    assert struct.unpack("<I", original.read(frame, 4))[0] == 12345
    assert original.read(frame + 0x24, 4) == bytes(4)
    return rows


def run():
    original = original_slots()
    lua = create_lua()
    lua.globals()["VM_MODULE"] = lua.execute((ROOT/"runtime/majiro_vm.lua").read_text(encoding="utf8"))
    lua.globals()["OPCODES"] = lua.execute((ROOT/"resources/scripts/opcodes.lua").read_text(encoding="utf8"))
    lua.execute(HARNESS)
    cases = table_value(lua.execute("""
      local results={}
      -- pic.mjo's adjacent clears are legal even after selection consumed slots.
      local v=makevm{{0,0x844},{2,0x844},{4,0x800,0},{10,0x82b}}
      assert(finish(v));results[#results+1]='repeated_empty_clear'
      v=makevm{{0,0x847,100},{6,0x83b,80},{12,0x83c,82},
        {18,0x844},{20,0x844},{22,0x800,7},{28,0x82b},
        {100,0x800,99},{106,0x1b0,32,77,-1},{116,0x82b}}
      assert(finish(v) and v.globals[1][77]==99 and #v.stack==0)
      results[#results+1]='clear_preserves_single_use_destructor'
      v=makevm{{0,0x843,100},{6,0x83b,200},{12,0x82c,12},
        {100,0x844},{102,0x800,10},{108,0x1b0,32,77,-1},{118,0x82b},
        {200,0x844},{202,0x800,20},{208,0x1b0,32,77,-1},{218,0x82b}}
      v:run(3)
      assert(#v:frame().handlers==3 and v:signal('right'))
      assert(not v:signal('left'))
      assert(finish(v) and v.globals[1][77]==10)
      results[#results+1]='any_then_independent_button_override'
      v=makevm{{0,0x83b,100},{6,0x83b,200},{12,0x82c,12},
        {100,0x844},{102,0x800,10},{108,0x1b0,32,77,-1},{118,0x82b},
        {200,0x844},{202,0x800,20},{208,0x1b0,32,77,-1},{218,0x82b}}
      v:run(3)
      assert(#v:frame().handlers==1 and v:signal('left'))
      assert(finish(v) and v.globals[1][77]==20)
      results[#results+1]='registration_replaces_same_slot'
      -- Restored older checkpoints can still contain an any entry.
      v=makevm{{0,0x844},{2,0x800,0},{8,0x82b}}
      v:frame().handlers={{kind='any',offset=2},{kind='right',offset=2}}
      assert(finish(v));results[#results+1]='legacy_checkpoint_clear'
      return results
    """))
    catalogue=lua.execute((ROOT/"resources/scripts/catalogue.lua").read_text(encoding="utf8"))
    script_path=ROOT/"resources/scripts"/(catalogue["scripts"]["pic.mjo"]["id"]+".lua")
    lua.globals()["PIC"]=lua.execute(script_path.read_text(encoding="utf8"))
    real_script=table_value(lua.execute("""
      local cases={}
      for _,button in ipairs{'left','right'}do
        local v=VM_MODULE.new{opcodes=OPCODES,loader=function()return PIC end,
          syscall=function()error('unexpected syscall')end}
        local pic=v:load('pic.mjo')
        v.status='running';v:enter(pic,48978,{},false)
        v:run(2);assert(v:signal(button))
        v:run(button=='left'and 3 or 2)
        assert(v.current_offset==49060 and v.status~='blocked')
        cases[#cases+1]={button=button,last_cleared_offset=v.current_offset}
      end
      return cases
    """))
    report = {"status": "passed", "source_exe_sha256": Original.IDENTITY,
              "original_slots": original, "runtime_cases": cases,"pic_script_cases":real_script,
              "clear_handler": "0x43C810", "destructor_consumption": "0x43D2B8"}
    write_json(ROOT/"reports/event_handler_tests.json", report)
    print("event handlers: original slots, repeated clears and 5 VM cases passed")
    return report


if __name__ == "__main__":
    run()
