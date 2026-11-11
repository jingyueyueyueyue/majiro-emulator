"""Compare branch-4 frame mutation with the original x86 handler, then execute Lua fixtures."""
import struct
from original_x86 import Original
from lua_bridge import create_lua, table_value
from port_common import ROOT, write_json


def original_case(frames):
    source = Original()
    context = source.memory(bytes(0x5100))
    source.machine.mem_write(context, struct.pack("<I", len(frames)-1))
    source.machine.mem_write(0x5d1a5c, struct.pack("<I", context))
    for i, (pc, target) in enumerate(frames):
        at = context + 0x28 + i*0x50
        for offset, value in ((0, pc), (0x20, target), (0x48, 91), (0x4c, 92)):
            source.machine.mem_write(at+offset, struct.pack("<I", value))
    source.call(0x4407e0, [])
    return [[struct.unpack("<I", source.read(context+0x28+i*0x50+offset, 4))[0]
             for offset in (0, 0x20, 0x48, 0x4c)] for i in range(len(frames))]


def run():
    cases = []
    for frames, expected in (
        ([(100, 200)], [[200, 0, 0, 0]]),
        ([(100, 100)], [[100, 100, 91, 92]]),
        ([(100, 80)], [[100, 80, 91, 92]]),
        ([(100, 0)], [[100, 0, 91, 92]]),
        ([(100, 200), (300, 400)], [[100, 200, 91, 92], [400, 0, 0, 0]]),
        ([(100, 200), (300, 200)], [[200, 0, 0, 0], [300, 200, 91, 92]]),
        ([(100, 200), (300, 0)], [[200, 0, 0, 0], [300, 0, 91, 92]]),
    ):
        actual = original_case(frames)
        assert actual == expected, (frames, actual, expected)
        cases.append({"frames": frames, "after": actual})
    lua = create_lua()
    lua.globals()["VM_MODULE"] = lua.execute((ROOT/"runtime/majiro_vm.lua").read_text(encoding="utf-8"))
    lua.globals()["OPCODES"] = lua.execute((ROOT/"resources/scripts/opcodes.lua").read_text(encoding="utf-8"))
    from test_vm import HARNESS
    lua.execute(HARNESS)
    # The child performs observable work after jmp.4 before returning.
    # A signal/unwind approximation would discard this write.
    got = lua.execute("""
      local v=makevm({
        {0,0x845,100},{6,0x810,42,0,0},
        {18,0x800,-1},{24,0x1b0,32,99,-1},{34,0x82b},
        {40,0x846},{42,0x800,73},{48,0x1b0,32,100,-1},{58,0x82b},
        {100,0x800,91},{106,0x1b0,32,99,-1},{116,0x82b}},{{42,40}})
      assert(finish(v))
      return {v.globals[1][99],v.globals[1][100],#v.stack}
    """)
    assert table_value(got) == [91, 73, 0]
    got = lua.execute("""
      local v=makevm{{0,0x845,20},{6,0x845,40},{12,0x846},
        {14,0x800,-1},{20,0x800,-2},{26,0x82b},{40,0x800,5},{46,0x82b}}
      v:run(3)
      assert(v:frame().script.code[v:frame().index][1]==40)
      assert(v:frame().branch4==nil and #v.stack==0)
      assert(finish(v))
      local b=makevm{{0,0x845,6},{6,0x846},{8,0x800,0},{14,0x82b}}
      b:run(2)
      return {b:frame().branch4,b:frame().script.code[b:frame().index][1]}
    """)
    assert table_value(got) == [6, 8]
    write_json(ROOT/"reports/branch4_tests.json", {
        "status": "passed", "source_exe_sha256": Original.IDENTITY,
        "handlers": {"bsel.4": "0x439E23", "bsel.jmp.4": "0x4407E0"},
        "original_cases": cases, "lua_cases": 3,
        "semantics": "single frame slot, forward-only search, consume chosen slot, no stack pop or forced unwind"})
    print("branch4: 7 original frame cases, 3 executable Lua fixtures passed")


if __name__ == "__main__":
    run()
