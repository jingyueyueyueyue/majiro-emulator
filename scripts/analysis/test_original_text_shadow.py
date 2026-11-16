"""Recover text effect modes from original x86 and verify portable compositing."""
from pathlib import Path
import json
import os
import struct
import subprocess
import sys

import unicorn
from unicorn import x86_const as reg
from original_text import TextOracle
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/part)for part in("convert","build")]
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json
from build_player_core import msvc_environment
from generate_portable_pixels import generate


def original():
    sample=next(x for x in read_json(ROOT/"reports/opcode_dispatch.json")["samples"]
                if x["sample"]=="happy_princess")
    assert sample["exe_sha256"]=="371fc97d0500b2bb20d23a82e604630d52ec9eb994d7bf32aba34eeae08571f1"
    entries=next(x["entries"]for x in read_json(ROOT/"reports/syscall_dispatch.json")["samples"]
                 if x["sample"]==sample["sample"])
    fixture=TextOracle(sample,entries);machine=fixture.machine
    observed=[];calls=[]
    def hook(uc,address,size,data):
        stack=uc.reg_read(reg.UC_X86_REG_ESP)
        if address==0x445650:
            args=struct.unpack("<5I",uc.mem_read(stack+4,20))
            x,y,_,_=struct.unpack("<4i",uc.mem_read(args[3],16))
            calls.append([x-50,y-60,args[4]&0xffffff])
        fixture._return(0,0)
    for address in(0x440150,0x445650):
        machine.hook_add(unicorn.UC_HOOK_CODE,hook,begin=address,end=address)
    for height in(16,20,24,30,36):
        for mode in(0,1,2,3,4,127,128):
            stack=fixture.STACK+0x8000;page=fixture.DATA+0x70000;output=page+0x1000
            for address,size in((stack,0x1000),(page,0x1000),(output,32)):
                machine.mem_write(address,bytes(size))
            machine.reg_write(reg.UC_X86_REG_ESP,stack)
            machine.reg_write(reg.UC_X86_REG_EBX,fixture.font)
            machine.reg_write(reg.UC_X86_REG_EBP,max(1,height//18))
            machine.reg_write(reg.UC_X86_REG_EDX,height)
            for address,value in((fixture.font+0x28,height),
                (fixture.font+0x38,(mode<<24)|0x123456),
                (fixture.font+0x48,50),(fixture.font+0x4c,60),(0x4eeb34,page)):
                fixture.write_u32(address,value)
            for offset,value in((0x50,100),(0x54,height),(0xb8,page),(0xc0,output),
                                 (0x7c,max(1,height//9)),(0x38,0),(0x3c,0),(0x40,100)):
                fixture.write_u32(stack+offset,value)
            calls.clear();machine.emu_start(0x414301,0x415403,count=20000)
            assert machine.reg_read(reg.UC_X86_REG_EIP)==0x415403
            observed.append({"height":height,"mode":mode,"effects":list(calls)})
    return observed


def main():
    folder=archive_dir("original_text_shadow")
    rows=original()
    write_json(folder/"original.json",rows)
    generated=folder/"portable_pixels.cpp";generate(generated)
    source=folder/"shadow_checks.cpp"
    source.write_text(r'''
#include "text_shadow.hpp"
#include "portable_pixels.hpp"
#include <cassert>
#include <iostream>
int main(){
    for(int h:{16,20,24,30,36})for(int mode:{0,1,2,3,4,127,128}){
        auto effects=majiro::text_shadow(h,(uint32_t(mode)<<24)|0x123456);
        std::cout<<h<<" "<<mode;
        for(const auto& value:effects)std::cout<<" "<<value.x<<","<<value.y<<","<<value.color;
        std::cout<<"\n";
    }
    assert(majiro::text_shadow(20,0xffffffffu).empty());
    std::vector<uint8_t> frame;
    majiro::pixels::Host host;
    host.glyphs=[](const auto&,int,const auto&,const auto&,bool){
        majiro::pixels::GlyphRaster r;r.width=r.height=20;r.alpha.assign(400,0);
        r.alpha[10*20+10]=255;return r;
    };
    host.present=[&](int,int,std::vector<uint8_t> bytes){frame=std::move(bytes);};
    majiro::pixels::install(std::move(host));
    auto api=[](const std::string& text){
        auto reply=majiro::pixels::command(text);assert(reply.starts_with("OK"));
    };
    api("create 1 40 40 0");api("fill 1 0 0 40 40 16777215");
    api("glyphs 1 5 5 20 10 16777215 50331648\nfixture\n20\nA");
    api("present 1 screen");
    auto pixel=[&](int x,int y){return frame[(size_t(y)*40+x)*4];};
    assert(pixel(15,15)==255); // foreground
    assert(pixel(14,14)==0&&pixel(16,15)==0); // thin eight-neighbour stroke
    assert(pixel(17,17)==128&&pixel(17,16)==128); // mode 3 lightened drop
    assert(pixel(18,15)==255); // old radius=3 incorrectly painted this black
    std::cout<<"PIXELS passed\n";
}
''',encoding="utf8")
    env=msvc_environment()
    compiler=Path(env["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    binary=folder/"shadow_checks.exe"
    command=[str(compiler),"/nologo","/O2","/EHsc","/std:c++20","/utf-8",
             "/I"+str(ROOT/"player/core"),str(source),str(generated),
             "/Fo"+str(folder)+os.sep,"/Fe"+str(binary)]
    result=subprocess.run(command,cwd=folder,env=env,capture_output=True,timeout=60)
    (folder/"compile.txt").write_bytes(result.stdout+result.stderr)
    assert result.returncode==0,str(folder/"compile.txt")
    result=subprocess.run([str(binary)],capture_output=True,timeout=30)
    (folder/"run.txt").write_bytes(result.stdout+result.stderr)
    assert result.returncode==0,str(folder/"run.txt")
    lines=result.stdout.decode().splitlines();assert lines.pop()=="PIXELS passed"
    assert len(lines)==len(rows)
    for text,expected in zip(lines,rows):
        height,mode,*effects=text.split()
        assert int(height)==expected["height"]and int(mode)==expected["mode"]
        assert [list(map(int,e.split(",")))for e in effects]==expected["effects"],expected
    report={"status":"passed","timestamp":timestamp(),"original_cases":len(rows),
            "portable_pixel_checks":6,"original_code_range":["0x414301","0x415403"],
            "scope":"original effect dispatch and exact ordered color/offset samples; real shared pixel composition",
            "device_accessed":False,"evidence":str(folder.relative_to(ROOT)),
            "source_sha256":{name:sha256(ROOT/name)for name in(
                "player/core/text_shadow.hpp","player/core/portable_pixels_wrapper.inc")}}
    write_json(folder/"report.json",report);write_json(ROOT/"reports/original_text_shadow_tests.json",report)
    print(json.dumps(report))
    return 0


if __name__=="__main__":raise SystemExit(main())
