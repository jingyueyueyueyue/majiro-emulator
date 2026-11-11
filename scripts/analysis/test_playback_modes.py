"""Compare AUTO/skip mode commands against original x86 state transitions."""
from __future__ import annotations
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua,setup_runtime
from port_common import ROOT,write_json


def run():
    lua=create_lua()
    _,vm,system,_=setup_runtime(lua,False)
    settings=system["state"]["settings"]
    fields=(("auto_mode",0x4dc330),("ultra_fast_mode",0x4dc334),
            ("fast_mode",0x4dc35c),("fast_read_mode",0x4dc360))
    commands=(("$set_auto_mode",0x441fb0,()),("$set_fast_mode",0x441ff0,()),
              ("$set_ultra_fast_mode",0x442150,()),("$reset_fast_mode",0x441f00,()),
              ("$reset_ultra_fast_mode",0x441f40,()),("$set_fast_mode_force",0x442090,()),
              ("$set_fast_mode_ar",0x442050,()),
              *(("$set_fast_stat",0x4420d0,(value,))for value in range(8)))
    cases=[]
    for config in(0,1):
        for name,address,args in commands:
            original=Original()
            original.machine.mem_write(0x4dc338,struct.pack("<I",1))
            original.machine.mem_write(0x4dc344,struct.pack("<I",config))
            settings["fast_allowed"]=True;settings["fast_mode_config"]=config
            for field,location in fields:
                original.machine.mem_write(location,struct.pack("<I",1))
                settings[field]=True
            def reset_keyboard(uc,at,size,data):
                stack=uc.reg_read(original.reg.UC_X86_REG_ESP)
                target=struct.unpack("<I",original.read(stack,4))[0]
                uc.reg_write(original.reg.UC_X86_REG_ESP,stack+4)
                uc.reg_write(original.reg.UC_X86_REG_EIP,target)
            hook=original.machine.hook_add(unicorn.UC_HOOK_CODE,reset_keyboard,begin=0x44bb70,end=0x44bb70)
            try:original.syscall(address,list(args))
            finally:original.machine.hook_del(hook)
            system["providers"][name](vm,lua.table_from(args))
            actual=[int(bool(settings[field]))for field,_ in fields]
            expected=[int(bool(struct.unpack("<I",original.read(location,4))[0]))for _,location in fields]
            assert actual==expected,(config,name,args,actual,expected)
            cases.append({"config":config,"command":name,"arguments":list(args),"state":actual})
    for ultra in(False,True):
        for buttons in(1,2,3):
            original=Original()
            for field,location in fields:
                value=ultra if field=="ultra_fast_mode"else True
                original.machine.mem_write(location,struct.pack("<I",int(value)))
                settings[field]=value
            if not ultra:
                if buttons&1:original.call(0x440fe0,[8])
                if buttons&2:original.call(0x440fe0,[0])
            system["input"]["sample"](lua.table_from({"buttons":buttons}))
            actual=[int(bool(settings[field]))for field,_ in fields]
            expected=[int(bool(struct.unpack("<I",original.read(location,4))[0]))for _,location in fields]
            assert actual==expected,(ultra,buttons,actual,expected)
            cases.append({"mouse_buttons":buttons,"ultra":ultra,"state":actual})
    report={"status":"passed","source_exe_sha256":Original.IDENTITY,"cases":cases}
    write_json(ROOT/"reports/playback_mode_tests.json",report)
    print("original/runtime playback modes: passed",len(cases),"cases")
    return report


if __name__=="__main__":run()
