"""Run original music filename resolution, including shipped legacy folder prefixes."""
from __future__ import annotations
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua
from port_common import ROOT,write_json
from majiro_arc import Catalogue


def original_resolve(text,available):
    source=Original()
    input_pointer=source.memory(text.encode("cp932")+b"\0")
    output_pointer=source.memory(bytes(300))
    probes=[]
    def boundary(uc,at,size,data):
        esp=uc.reg_read(source.reg.UC_X86_REG_ESP)
        target,value=struct.unpack("<2I",source.read(esp,8))
        if at==0x492d13:
            # The CRT's thread-local locale boundary is replaced with the
            # documented CP932 lead-byte ranges. _splitpath itself executes.
            byte=value&255
            result=4 if 0x81<=byte<=0x9f or 0xe0<=byte<=0xfc else 0
        elif at==0x492c9e:
            dest,capacity,origin,count=struct.unpack("<4I",source.read(esp+4,16))
            raw=source.read(origin,count).split(b"\0",1)[0]
            assert len(raw)<capacity
            uc.mem_write(dest,raw+b"\0")
            result=0
        else:
            raw=source.read(value,260).split(b"\0",1)[0].decode("cp932")
            probes.append(raw)
            result=int(raw.casefold()in available)
        uc.reg_write(source.reg.UC_X86_REG_EAX,result)
        uc.reg_write(source.reg.UC_X86_REG_ESP,esp+4)
        uc.reg_write(source.reg.UC_X86_REG_EIP,target)
    for at in(0x492d13,0x492c9e,0x47be30):
        source.machine.hook_add(unicorn.UC_HOOK_CODE,boundary,begin=at,end=at)
    found=source.call(0x46c330,[output_pointer,input_pointer])
    result=source.read(output_pointer,260).split(b"\0",1)[0].decode("cp932")if found else None
    return result,probes


def run():
    catalogue=Catalogue(ROOT)
    available={name.casefold()for name in catalogue.by_name}
    lua=create_lua()
    module=lua.execute((ROOT/"runtime/majiro_media.lua").read_text(encoding="utf-8"))
    providers={}
    state=lua.table_from({"settings":{"musicvolume":1000}},recursive=True)
    options=lua.table_from({"resource_names":{name:True for name in available}},recursive=True)
    options["now"]=lambda:0
    options["audio"]=lambda action,spec:True
    requests=[]
    def asset(name):
        requests.append(name)
        return lua.table_from({"file":name,"duration_ms":1000})
    options["media_asset"]=asset
    module["install"](lambda name,fn:providers.update({name:fn}),state,options)
    cases=[]
    for name in("過去\\bol_kanashi","過去\\b_a01","過去\\bgm112",
                 "folder/bol_kanashi.ogg","C:\\old\\bol_kanashi.wav",
                 "folder.name/PL_OP_INST.ogg","pl_op_inst","OP_short",
                 "ソフト\\bol_kanashi","missing_audio_fixture"):
        expected,probes=original_resolve(name,available)
        exists=providers["$sound_is_exist"](lua.table(),lua.table_from([name]))
        assert exists==int(expected is not None),(name,expected,exists)
        if expected is not None:
            providers["$music"](lua.table(),lua.table_from([name]))
            actual=requests[-1]
            assert actual==expected.casefold(),(name,actual,expected)
        else:actual=None
        cases.append({"request":name,"original_and_runtime":actual,"original_probes":probes})
    # An explicit extension does not change Majiro's WAV-before-OGG order.
    result,probes=original_resolve("folder/dual.ogg",{"dual.wav","dual.ogg"})
    assert result=="dual.wav"and probes==["dual.wav"]
    playback=[]
    for active,changing,expected in((0,0,0),(1,0,1),(1,1,-1)):
        source=Original()
        audio=source.memory(bytes(0x700))
        source.machine.mem_write(audio+0x650,struct.pack("<I",active))
        source.machine.mem_write(audio+0x648,struct.pack("<I",changing))
        # Critical-section boundaries have no effect on the scalar query.
        source.machine.mem_write(source.STOP+16,b"\xc2\x04\x00")
        for pointer in(0x4a923c,0x4a9238):
            source.machine.mem_write(pointer,struct.pack("<I",source.STOP+16))
        def lookup(uc,at,size,data):
            esp=uc.reg_read(source.reg.UC_X86_REG_ESP)
            target=struct.unpack("<I",source.read(esp,4))[0]
            uc.reg_write(source.reg.UC_X86_REG_EAX,audio)
            uc.reg_write(source.reg.UC_X86_REG_ESP,esp+4)
            uc.reg_write(source.reg.UC_X86_REG_EIP,target)
        source.machine.hook_add(unicorn.UC_HOOK_CODE,lookup,begin=0x485430,end=0x485430)
        actual=source.call(0x485340,[1])
        if actual>=2**31:actual-=2**32
        assert actual==expected
        playback.append({"active":active,"changing":changing,"original_status":actual})
    for loop in(False,True):
        state["media"]["channels"]["music"]=lua.table_from({"id":"music","name":"fixture","loop":loop})
        assert providers["$music_stat"](lua.table(),lua.table())==1
    providers["$extmusic"](lua.table(),lua.table_from(["過去\\bol_kanashi","過去\\bol_kanashi_loop"]))
    spec=state["media"]["channels"]["music"]
    assert spec["file"]=="bol_kanashi.wav"and spec["loop_file"]=="bol_kanashi_loop.ogg"
    assert spec["loop"]and spec["loop_duration_ms"]==1000
    source=Original()
    request=source.memory(b"01_full\0");empty=source.memory(b"\0")
    source.machine.mem_write(0x5b7400,struct.pack("<I",0x12345678))
    stopped=[]
    def missing_boundary(uc,at,size,data):
        esp=uc.reg_read(source.reg.UC_X86_REG_ESP)
        target=struct.unpack("<I",source.read(esp,4))[0]
        if at==0x4853e0:stopped.append(True)
        uc.reg_write(source.reg.UC_X86_REG_EAX,0)
        uc.reg_write(source.reg.UC_X86_REG_ESP,esp+4)
        uc.reg_write(source.reg.UC_X86_REG_EIP,target)
    for at in(0x46c330,0x457130,0x4570d0,0x48a437,0x4853e0):
        source.machine.hook_add(unicorn.UC_HOOK_CODE,missing_boundary,begin=at,end=at)
    source.call(0x46c5b0,[request,empty,1])
    assert not stopped and struct.unpack("<I",source.read(0x5b7400,4))[0]==0x12345678
    before_generation=state["media"]["generation"]
    providers["$music"](lua.table(),lua.table_from(["01_full",1]))
    assert state["media"]["channels"]["music"]["file"]==spec["file"]
    assert state["media"]["generation"]==before_generation
    report={"status":"passed","source_exe_sha256":Original.IDENTITY,"cases":cases,
            "playback_status":playback,"loop_flag_changes_playback_status":False,
            "missing_music_retains_active_channel":True,
            "extension_priority":"wav_before_ogg","runtime_file_copies":0,
            "scope":"original audio resolver and path splitting; CP932 locale, bounded string copy and existence boundaries stubbed"}
    write_json(ROOT/"reports/audio_path_tests.json",report)
    print("original/runtime audio paths: passed",len(cases),"requests and extension priority")
    return report


if __name__=="__main__":run()
