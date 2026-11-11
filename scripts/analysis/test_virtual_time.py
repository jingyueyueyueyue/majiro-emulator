"""Compare original accelerated clock and preserve wall-time audio and saves."""
from __future__ import annotations
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua,setup_runtime,table_value
from port_common import ROOT,archive_dir,write_json


def run():
    original=Original()
    raw=[1000]
    def tick(uc,at,size,data):
        stack=uc.reg_read(original.reg.UC_X86_REG_ESP)
        target=struct.unpack("<I",original.read(stack,4))[0]
        uc.reg_write(original.reg.UC_X86_REG_EAX,raw[0])
        uc.reg_write(original.reg.UC_X86_REG_ESP,stack+4)
        uc.reg_write(original.reg.UC_X86_REG_EIP,target)
    original.machine.mem_write(0x4a9420,struct.pack("<I",original.STOP+16))
    original.machine.hook_add(unicorn.UC_HOOK_CODE,tick,begin=original.STOP+16,end=original.STOP+16)
    for address,value in((0x4dc374,0),(0x4dc380,0),(0x4dc36c,1000)):
        original.machine.mem_write(address,struct.pack("<I",value))
    original.machine.mem_write(0x4dc378,struct.pack("<d",1))
    lua=create_lua()
    evidence=archive_dir("virtual_time")
    _,vm,system,clock=setup_runtime(lua,False,save_root=evidence/"saves")
    clock["ms"]=1000
    def call(name,*values):return system["providers"][name](vm,lua.table_from(values))
    cases=[]
    for at,factor in((1000,2),(1017,None),(1150,None),(1150,3),(1200,None),
                      (1200,1),(1230,None),(1300,.5),(1333,None),(1440,1),(1450,None),
                      (1450,120),(1460,None),(1500,1),(1501,None)):
        raw[0]=at;clock["ms"]=at
        cached=original.call(0x43a780,[])
        original.machine.mem_write(0x4dc36c,struct.pack("<I",cached))
        if factor is not None:
            bits=struct.unpack("<I",struct.pack("<f",factor))[0]
            original.syscall(0x4423b0,[bits])
            call("$time_accera",factor)
        expected=original.call(0x43a780,[])
        actual=call("$timer")
        assert expected==actual,(at,factor,expected,actual)
        cases.append({"raw_ms":at,"factor":factor,"original_and_runtime_ms":actual})
    # Virtual waits reach 1200 game ms in 10 wall ms at 120x.
    call("$time_accera",120)
    call("$wait",1200)
    deadline=vm["next_deadline"](vm)
    assert deadline==1511,deadline
    clock["ms"]=1510;system["poll"](vm,lua.table());assert vm["waiting"]
    clock["ms"]=1511;system["poll"](vm,lua.table());assert not vm["waiting"]
    # Actual sound duration must remain independent of the virtual timer.
    audio=lua.table_from({"id":"voice","name":"fixture","file":"audio/fixture.ogg",
                         "started":1511,"duration_ms":1000,"loop":False})
    system["state"]["media"]["channels"]["voice"]=audio
    call("$voice_wait")
    assert vm["next_deadline"](vm)==2511
    clock["ms"]=1520;system["poll"](vm,lua.table());assert vm["waiting"]["kind"]=="audio"
    clock["ms"]=2511;system["poll"](vm,lua.table());assert not vm["waiting"]
    # Use the real story checkpoint to verify both clock anchors survive load.
    from model_session import Session
    session=Session(False,save_root=evidence/"roundtrip")
    assert session.advance()=="ui"and session.click(1)=="text"
    providers=session.system["providers"]
    providers["$time_accera"](session.vm,session.lua.table_from([2]))
    session.clock["ms"]+=75
    providers["$save_point"](session.vm,session.lua.table(),False)
    providers["$do_save"](session.vm,session.lua.table_from([9394]))
    saved_raw=session.clock["ms"]
    saved_virtual=providers["$timer"](session.vm,session.lua.table())
    providers["$time_accera"](session.vm,session.lua.table_from([1]))
    session.clock["ms"]+=50
    providers["$do_load"](session.vm,session.lua.table_from([9394]))
    assert session.clock["ms"]==saved_raw
    assert providers["$timer"](session.vm,session.lua.table())==saved_virtual
    session.clock["ms"]+=10
    assert providers["$timer"](session.vm,session.lua.table())==saved_virtual+20
    report={"status":"passed","source_exe_sha256":Original.IDENTITY,"cases":cases,
            "deadline_wall_ms":deadline,"audio_wall_clock":True,"save_clock_roundtrip":True,
            "evidence":str(evidence)}
    write_json(ROOT/"reports/virtual_time_tests.json",report)
    print("original/runtime virtual clock: passed",len(cases),"samples; deadlines, audio and save")
    return report


if __name__=="__main__":run()
