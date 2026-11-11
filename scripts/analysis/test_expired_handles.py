"""Prove original expired-sprite and invalid-copy return values without OS dialogs."""
from __future__ import annotations
import struct
from pathlib import Path
import sys
from native_disassembly import Research
from lua_bridge import create_lua,setup_runtime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,write_json,read_json


def run():
    import unicorn
    research=Research();original=research.original
    lua=create_lua();_,vm,system,_=setup_runtime(lua,False)
    names={v:int(k,16)for k,v in read_json(ROOT/"references/majiro-py/known_hashes.json")["syscalls"].items()}
    cases=[]
    values=[]
    def hook(uc,at,size,data):
        esp=uc.reg_read(original.reg.UC_X86_REG_ESP)
        if at==0x44eab0:
            values.append(struct.unpack("<I",original.read(esp+4,4))[0])
        else:
            uc.reg_write(original.reg.UC_X86_REG_EIP,struct.unpack("<I",original.read(esp,4))[0])
            uc.reg_write(original.reg.UC_X86_REG_ESP,esp+4)
    handles=[original.machine.hook_add(unicorn.UC_HOOK_CODE,hook,begin=a,end=a)
             for a in(0x441150,0x44eab0)]
    try:
        for name,count in[
            ("$sprite_get_page",1),("$sprite_get_pos_x",1),("$sprite_get_pos_y",1),
            ("$sprite_len_x",1),("$sprite_len_y",1),("$sprite_release",1),
            ("$sprite_move",3),("$sprite_alfa_set",2),("$sprite_clip",5),
            ("$sprite_priority_high",1),("$sprite_paste",2),("$sprite_page_auto_release",1),
            ("$sprite_push",1),("$sprite_pop",1),("$grp_copy",8),("$grp_extcopy",9),
            ("$page_release",1),
        ]:
            address=research.handler(names[name])
            original.machine.mem_write(0x5bab28,bytes(4))
            values.clear()
            args=[0x12345678]+[0]*(count-1)
            try:original.syscall(address,args,invalid_pages=name.startswith('$grp_')or name=='$page_release')
            except Exception as error:raise RuntimeError((name,hex(address),values))from error
            assert values and values[-1]==0,(name,values)
            got=system["providers"][name](vm,lua.table_from(args))
            assert got==0,(name,got)
            cases.append({"name":name,"address":hex(address),"result":0})
    finally:
        for handle in handles:original.machine.hook_del(handle)
    report={"status":"passed","source_exe_sha256":original.IDENTITY,"cases":cases,
            "scope":"original wrappers with expired sprite/page input; only error-display boundary stubbed"}
    write_json(ROOT/"reports/expired_handle_tests.json",report)
    print("original expired handles:",len(cases),"cases passed")
    return report


if __name__=="__main__":
    run()
