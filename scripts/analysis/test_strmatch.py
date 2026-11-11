"""Check original symmetric prefix matching, used by portrait offset tables."""
from __future__ import annotations
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua,setup_runtime
from port_common import ROOT,write_json


def run():
    lua=create_lua()
    _,vm,system,_=setup_runtime(lua,False)
    cases=[]
    for first,second in (
        ("sana_b2a@02","sana_**"),("sana_**","sana_b2a@02"),
        ("SANA_B2A","sana_*"),("sana_b2a","yuzu_*"),
        ("abc*ignored","abcd"),("abcd","abc*ignored"),
        ("ab?d","abcd"),("abcd","ab?d"),("ab?","ab"),
        ("","*tail"),("abc","abc"),("abc","abcd"),("",""),
    ):
        original=Original()
        results=[]
        def capture(uc,at,size,data):
            stack=uc.reg_read(original.reg.UC_X86_REG_ESP)
            results.append(struct.unpack("<i",original.read(stack+4,4))[0])
        hook=original.machine.hook_add(unicorn.UC_HOOK_CODE,capture,begin=0x44eab0,end=0x44eab0)
        try:
            pointers=[original.memory(bytes(12)+text.encode("cp932")+b"\0")
                      for text in(first,second)]
            original.syscall(0x4307e0,pointers)
        finally:original.machine.hook_del(hook)
        actual=system["providers"]["$strmatch"](vm,lua.table_from([first,second]))
        assert results==[actual],(first,second,results,actual)
        cases.append({"arguments":[first,second],"original_and_runtime":actual})
    report={"status":"passed","source_exe_sha256":Original.IDENTITY,"cases":cases}
    write_json(ROOT/"reports/strmatch_tests.json",report)
    print("original/runtime strmatch: passed",len(cases),"cases")
    return report


if __name__=="__main__":run()
