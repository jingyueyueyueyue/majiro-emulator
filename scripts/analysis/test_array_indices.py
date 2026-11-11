"""Execute original one/two/three-dimensional wrapped array indexing."""
from __future__ import annotations
from pathlib import Path
import struct
import sys
from original_x86 import Original
from lua_bridge import create_lua
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,write_json


def run():
    original=Original()
    rows=[]
    for dims,indices in [
        ((32,),(-65,)),((32,),(-1,)),((32,),(0,)),((32,),(31,)),
        ((32,),(32,)),((32,),(67,)),((3,4),(-1,-1)),((3,4),(5,10)),
        ((2,3,4),(-1,-2,-3)),((2,3,4),(4,5,6)),
    ]:
        padded=(1,)*(3-len(dims))+dims
        array=original.memory(struct.pack("<5I",1,0,*padded))
        variable=original.memory(struct.pack("<4I",0,3,0,array))
        values=b"".join(struct.pack("<4I",0,0,0,i&0xffffffff)for i in indices)
        stack=original.memory(values)
        view=bytearray(16);struct.pack_into("<I",view,8,len(indices))
        vm=original.memory(bytes(view))
        context=bytearray(0x28);struct.pack_into("<I",context,0x24,stack)
        context=original.memory(bytes(context))
        original.machine.mem_write(0x5d1a58,struct.pack("<I",vm))
        original.machine.mem_write(0x5d1a5c,struct.pack("<I",context))
        got=original.call(0x43a590,[variable,len(dims)])
        expected=0
        for dimension,index in zip(dims,indices):expected=expected*dimension+index%dimension
        assert got==expected,(dims,indices,got,expected)
        rows.append({"dimensions":dims,"indices":indices,"flat":got})
    lua=create_lua()
    module=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
    lua.globals()["VM"]=module
    assert lua.execute("""
      local v=VM.new{loader=function()end,opcodes={},syscall=function()end}
      v.globals[1][7]={kind='array',element_type=0,dims={3,4},values={}}
      v:push(-1);v:push(10)
      local bank,index=v:slot(32+2048*2,7,-1,true)
      return index==10
    """)
    report={"status":"passed","source_exe_sha256":Original.IDENTITY,"cases":rows,
            "scope":"actual original array-coordinate helper vs modulo flattening and VM"}
    write_json(ROOT/"reports/array_index_tests.json",report)
    print("original wrapped array indexing:",len(rows),"cases passed")
    return report


if __name__=="__main__":
    run()
