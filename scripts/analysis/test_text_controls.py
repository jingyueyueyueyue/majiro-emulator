"""Original font-control parsing, mixed styles, stack balance and glyph positions."""
from __future__ import annotations
import struct
from pathlib import Path
import sys
from original_x86 import Original
from lua_bridge import create_lua,setup_runtime,table_value
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,write_json


def original_font(height,width,step,style):
    import unicorn
    original=Original()
    for address,value in((0x52ccd4,1),(0x5306b8,20),(0x5306bc,10),(0x5306c0,27),(0x5306b0,0)):
        original.machine.mem_write(address,struct.pack("<I",value))
    original.machine.mem_write(0x530690,b"MS Gothic\0")
    command="f"+"".join("0x%08x"%(v&0xffffffff)for v in(height,width,step,style))
    pointer=original.memory(command.encode()+b"\0")
    def skip_font(uc,at,size,data):
        esp=uc.reg_read(original.reg.UC_X86_REG_ESP)
        target=struct.unpack("<I",original.read(esp,4))[0]
        uc.reg_write(original.reg.UC_X86_REG_EAX,0)
        uc.reg_write(original.reg.UC_X86_REG_ESP,esp+4)
        uc.reg_write(original.reg.UC_X86_REG_EIP,target)
    hook=original.machine.hook_add(unicorn.UC_HOOK_CODE,skip_font,begin=0x444260,end=0x444260)
    try:original.call(0x443bb0,[pointer,1])
    finally:original.machine.hook_del(hook)
    return [struct.unpack("<I",original.read(address,4))[0]
            for address in(0x5306b8,0x5306bc,0x5306c0,0x5306b0)]


def run():
    lua=create_lua()
    mod,_,system,_=setup_runtime(lua,False)
    console=system["state"]["console"]
    vm=lua.eval("""
    function(mod,system,ops)
      local script={name='font-control-fixture',main=0,functions={},code={{0,0x840}}}
      local vm=mod.new{opcodes=ops,loader=function()return script end,
        syscall=system.invoke,text_ops=system.text,string_ops=system.strings}
      vm:begin(script.name)
      return vm
    end
    """)(mod,system,lua.execute((ROOT/"resources/scripts/opcodes.lua").read_text(encoding="utf-8")))
    execute=lua.eval("""
    function(vm,kind,value)
      local frame=vm:frame()
      frame.script.code[1]={0,({text=0x840,proc=0x841,ctrl=0x842})[kind],value}
      frame.index=1
      vm:step()
    end
    """)
    page=system["providers"]["$page_create"](vm,lua.table_from([1000,400]))
    assert page==1
    console["visible"]=True
    cases=[]
    for args in((50,-1,-1,-1),(30,11,42,1),(-1,-1,-1,-1)):
        console["font_spec"]=lua.table_from(["MS Gothic",20,10,27,0])
        console["configuration"]=lua.table_from([1,20,30,600,200])
        console["x"],console["y"]=20,30
        console["text"]="";console["page"]=lua.table()
        execute(vm,"text","A");execute(vm,"proc",None)
        for value in(*args[:3],"",args[3]):vm["push"](vm,value)
        execute(vm,"ctrl","f")
        expected=original_font(*args)
        got=list(console["font_spec"].values())[1:]
        assert got==expected,(args,got,expected)
        execute(vm,"text","B");execute(vm,"proc",None)
        assert len(vm["stack"])==0
        assert console["page"][1]["font_spec"][2]==20
        assert console["page"][2]["font_spec"][2]==expected[0]
        assert console["x"]==30+expected[1]
        cases.append({"arguments":args,"original_and_runtime":expected})
    original=Original()
    original.machine.mem_write(0x52ccd4,struct.pack("<I",1))
    pointer=original.memory(b"c0x001122330x00445566\0")
    original.call(0x443bb0,[pointer,1])
    colors=[struct.unpack("<I",original.read(address,4))[0]for address in(0x5306c4,0x5306c8)]
    vm["push"](vm,0x112233);vm["push"](vm,0x445566);execute(vm,"ctrl","c")
    assert colors==[console["color"],console["shadow"]]==[0x112233,0x445566]
    report={"status":"passed","source_exe_sha256":Original.IDENTITY,"cases":cases,
            "scope":"original f parser with GDI cache boundary stubbed; runtime mixed-run styles and stack"}
    write_json(ROOT/"reports/text_control_tests.json",report)
    print(report)
    return report


if __name__=="__main__":
    run()
