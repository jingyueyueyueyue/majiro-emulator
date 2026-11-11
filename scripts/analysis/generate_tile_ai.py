"""Convert recovered pure tile-AI instructions into a platform-neutral Lua table."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import capstone
from original_graphics_remaining import GraphicsOracle
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,atomic_bytes,lua_value,sha256,write_json


def main():
    records=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    sample=next(row for row in records if row["sample"]=="RURINOKASANE")
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]
    entries=next(row["entries"]for row in dispatch if row["sample"]==sample["sample"])
    oracle=GraphicsOracle(sample,entries)
    source=ROOT/"archive/2026-11/20261110_122345_076577_tile_ai/20261110_ai.c"
    addresses=[int(line,16)for line in source.read_text(encoding="utf8").splitlines()
               if line and not line.startswith(("FUNCTION","MISSING"))]
    instructions={}
    def operand(ins,value):
        if value.type==capstone.CS_OP_IMM:return["i",value.imm]
        if value.type==capstone.CS_OP_REG:return["r",ins.reg_name(value.reg)]
        if value.type==capstone.CS_OP_MEM:
            m=value.mem
            return["m",value.size,ins.reg_name(m.base)if m.base else"",ins.reg_name(m.index)if m.index else"",
                   m.scale,m.disp]
        raise ValueError("unsupported tile AI operand")
    for at in addresses:
        ins=oracle.functions.instruction(at)
        instructions[at]=[ins.mnemonic,at+ins.size,*[operand(ins,value)for value in ins.operands]]
    constants={}
    for begin,end in((0x4a9000,0x4ae000),(0x4c7000,0x4ca000)):
        for at in range(begin,end,4):
            value=oracle.read_u32(at)
            if value:constants[at]=value
    for at in addresses:
        ins=oracle.functions.instruction(at)
        if ins.mnemonic=="jmp"and ins.operands[0].type==capstone.CS_OP_MEM:
            table=ins.operands[0].mem.disp
            if ins.operands[0].mem.index and 0x400000<=table<0x4a9000:
                for index in range(256):
                    target=oracle.read_u32(table+index*4)
                    if target not in instructions:break
                    constants[table+index*4]=target
    data={"entry":0x4326c0,"instructions":instructions,"constants":constants,
          "reference_exe_sha256":sample["exe_sha256"],"instruction_source_sha256":sha256(source)}
    output=ROOT/"player/tables/tile_ai.lua"
    atomic_bytes(output,("-- Generated pure integer tile AI; no original EXE is loaded at runtime.\nreturn "+
                         lua_value(data)+"\n").encode("utf8"))
    write_json(ROOT/"reports/tile_ai_conversion.json",{"status":"generated","instructions":len(instructions),
        "constants":len(constants),"output_sha256":sha256(output),"reference_exe_sha256":sample["exe_sha256"]})
    print(len(instructions),"tile AI instructions generated")


if __name__=="__main__":main()
