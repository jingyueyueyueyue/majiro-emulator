"""Original file-call wrappers, named function lookup and nested VM returns."""
from __future__ import annotations
import struct
import zlib
import unicorn
from original_x86 import Original
from lua_bridge import create_lua,setup_runtime,table_value
from port_common import ROOT,write_json


def original_call(named,wants):
    source=Original()
    strings=("callee","entry@GLOBAL")if named else("callee",)
    values=[source.memory(bytes(12)+text.encode("cp932")+b"\0")for text in strings]+[11,7]
    slots=source.memory(bytes(16*(len(values)+2)))
    top=slots+16*(len(values)-1)
    for i,value in enumerate(values):
        source.machine.mem_write(top-16*i,struct.pack("<4I",0,0,0,value))
    context=source.memory(bytes(0x5100))
    frame=context+0x28
    source.machine.mem_write(0x5d1a5c,struct.pack("<I",context))
    source.machine.mem_write(0x5d1a58,struct.pack("<I",frame))
    operand=source.memory(bytes(1024))
    source.machine.mem_write(context+0x24,struct.pack("<I",operand))
    source.machine.mem_write(frame,struct.pack("<4I",0x12345678,len(values)+1,len(values)+1,0))
    script=bytearray(0xac)
    struct.pack_into("<I",script,0x94,0x44)
    struct.pack_into("<I",script,0xa4,0x100e0000)
    script_pointer=source.memory(bytes(script))
    seen=[]
    def boundary(uc,at,size,data):
        esp=uc.reg_read(source.reg.UC_X86_REG_ESP)
        target=struct.unpack("<I",source.read(esp,4))[0]
        active=struct.unpack("<I",source.read(0x5d1a58,4))[0]
        if at==0x44ec90:uc.reg_write(source.reg.UC_X86_REG_EAX,top)
        elif at==0x44e9a0:
            count=struct.unpack("<I",source.read(active+8,4))[0]
            uc.mem_write(active+8,struct.pack("<I",count-1))
        elif at==0x44eab0:
            value=struct.unpack("<I",source.read(esp+4,4))[0]
            seen.append(("parameter_count",value))
            count=struct.unpack("<I",source.read(active+8,4))[0]
            uc.mem_write(active+8,struct.pack("<I",count+1))
        elif at==0x440ab0:uc.reg_write(source.reg.UC_X86_REG_EAX,script_pointer)
        elif at==0x43a470:
            value=struct.unpack("<I",source.read(esp+4,4))[0]
            seen.append(("function_hash",value))
            uc.reg_write(source.reg.UC_X86_REG_EAX,0x100e0088)
        uc.reg_write(source.reg.UC_X86_REG_ESP,esp+4)
        uc.reg_write(source.reg.UC_X86_REG_EIP,target)
    for at in(0x44ec90,0x44e9a0,0x44eab0,0x452e20,0x440ab0,0x43a470,0x48a437):
        source.machine.hook_add(unicorn.UC_HOOK_CODE,boundary,begin=at,end=at)
    source.call(0x4387a0 if named else 0x438620,[len(values),int(wants)])
    active=struct.unpack("<I",source.read(0x5d1a58,4))[0]
    assert struct.unpack("<I",source.read(context,4))[0]==1
    assert struct.unpack("<I",source.read(frame+12,4))[0]==int(wants)
    assert struct.unpack("<I",source.read(active,4))[0]==(0x100e0088 if named else 0x100e0044)
    assert seen[0]==("parameter_count",2),seen
    if named:assert seen[1]==("function_hash",zlib.crc32(b"entry@GLOBAL")),seen
    return {"named":named,"wants_result":wants,"argument_count":2,
            "caller_preserved":True,"target":"named_function"if named else"script_main"}


def run():
    original=[original_call(named,wants)for named in(False,True)for wants in(False,True)]
    cases=[]
    for named in(False,True):
        for wants in(False,True):
            lua=create_lua()
            _,vm,system,_=setup_runtime(lua,False)
            vm["restart_on_return"]=False  # Independent terminating fixture, without the game host loop.
            op={spec[1]:number for number,spec in vm["opcodes"].items()}
            target_hash=zlib.crc32(b"entry@GLOBAL")
            call_hash=0xedefb0b0 if named else 0xcd00d280
            root=[
                [0,op["ldc.i"],7],[10,op["ldc.i"],11],
                *([[20,op["ldstr"],"entry@GLOBAL"]]if named else[]),
                [30,op["ldstr"],"callee"],
                [40,op["syscall"if wants else"syscallp"],call_hash,4 if named else 3],
                *([[50,op["stp.i"],32,100,-1]]if wants else[]),
                [60,op["ldc.i"],0],[70,op["ret"]]]
            callee=[
                [0,op["argcheck"],[0,0]],[10,op["alloca"],[]],
                [20,op["ld"],96,1,-2],[30,op["ld"],96,2,-3],
                [40,op["sub.i"]],[50,op["ret"]],
                [100,op["argcheck"],[0,0]],[110,op["alloca"],[]],
                [120,op["ld"],96,1,-2],[130,op["ld"],96,2,-3],
                [140,op["add.i"]],[150,op["ret"]]]
            fixtures={
                "root.mjo":{"name":"root.mjo","main":0,"functions":[[0x1d128f30,0]],"code":root},
                "callee.mjo":{"name":"callee.mjo","main":0,"functions":[[0x1d128f30,0],[target_hash,100]],"code":callee},
            }
            vm["loader"]=lambda name:lua.table_from(fixtures[name],recursive=True)
            vm["begin"](vm,"root.mjo")
            assert vm["run"](vm,1000)=="finished",table_value(vm["problem"])
            assert len(vm["stack"])==0
            assert len(vm["frames"])==0
            if wants:assert vm["globals"][1][100]==(18 if named else 4)
            cases.append({"named":named,"wants_result":wants,
                          "result":vm["globals"][1][100],"stack_balanced":True})
    # Native scalar hash must match the Lua string boundary for replay names.
    lua=create_lua();_,vm,system,_=setup_runtime(lua,False)
    for text in("entry@GLOBAL","$回想@GLOBAL","$main@GLOBAL"):
        source=Original();p=source.memory(text.encode("cp932")+b"\0")
        assert source.call(0x478e10,[p])==system["strings"]["hash"](text)==zlib.crc32(text.encode("cp932"))
    report={"status":"passed","source_exe_sha256":Original.IDENTITY,"original_wrappers":original,
            "runtime_cases":cases,"function_hash_samples":3,
            "scope":"actual original call wrappers with file I/O boundaries stubbed; independent VM programs"}
    write_json(ROOT/"reports/script_call_tests.json",report)
    print("script calls: passed; 4 original wrappers, 4 VM returns, 3 function hashes")
    return report


if __name__=="__main__":run()
