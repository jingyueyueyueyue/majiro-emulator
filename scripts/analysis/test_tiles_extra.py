"""Focused original quad mutation and typed-array checks for remaining entries."""
from __future__ import annotations
import struct
from pathlib import Path
import sys
from lua_bridge import create_lua,table_value
from original_x86 import Original
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,write_json,timestamp


def run():
    cases=[]
    for values,groups,requested in(
        ([110,111,112,120,121,122,130,131,132,210,211,212,213,600],[0]*14,210),
        ([110,111,112,120,121,122,130,131,132,210,211,212,213,214],[0]*14,210),
        ([110,111,112,120,121,122,130,131,132,210,211,212,213,600],[4,4,4]+[0]*11,110)):
        native=Original();base=native.memory(struct.pack("<14i",*values))
        flags=native.memory(struct.pack("<14i",*groups));marks=native.memory(bytes(56))
        native.machine.mem_write(0x5d1578,struct.pack("<I",4))
        native.machine.mem_write(0x5d0a30,struct.pack("<I",1))
        result=native.call(0x4658c0,[base,flags,marks,requested])
        wanted=[list(struct.unpack("<14i",native.read(at,56)))for at in(base,flags,marks)]
        lua=create_lua()
        data={0x5d1578:4,0x5d0a30:1}
        for i,value in enumerate(values):data[0x5d1280+i*4]=value
        for i,value in enumerate(groups):data[0x5d11c0+i*4]=value
        state=lua.table_from({"tiles":{"memory":data}},recursive=True)
        providers=lua.table()
        define=lua.eval("function(providers)return function(name,fn)providers[name]=fn end end")(providers)
        def module(name):
            return lua.execute((ROOT/"player"/name).read_text(encoding="utf8"))
        api=module("runtime/majiro_tiles_analysis.lua")["install"](
            define,state,lua.table_from({"modules":module}))
        got=api["execute"](lua.table(),0x850eecf4,lua.table_from([0,requested]))
        memory=state["tiles"]["memory"]
        actual=[[memory[address+i*4]or 0 for i in range(14)]for address in(0x5d1280,0x5d11c0,0x5d1100)]
        assert got==result and actual==wanted,(got,result,actual,wanted)
        cases.append({"requested":requested,"return":got,"values":actual[0],"groups":actual[1],"markers":actual[2]})
    report={"status":"passed","timestamp":timestamp(),"cases":cases,
        "scope":"three original quad mutation paths; shared hand wait evaluator remains separately validated"}
    folder=archive_dir("tiles_extra")
    write_json(folder/(folder.name[:8]+"_tiles_extra.json"),report)
    write_json(ROOT/"reports/tiles_extra_tests.json",report)
    print("extra tile quad mutations: 3 original checks passed")


if __name__=="__main__":run()
