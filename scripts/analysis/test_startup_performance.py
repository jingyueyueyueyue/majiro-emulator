"""Guard lazy original scripts, opcode lookup and byte-exact ASCII conversion."""
from pathlib import Path
import json
import os
import subprocess
import sys
import statistics
import struct
import time

from lua_bridge import create_lua
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/name)for name in("convert","build")]
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json
from build_player_core import msvc_environment
from test_formats import arc,mjo


def main():
    folder=archive_dir("startup_performance")
    lua=create_lua()
    lua.globals()["RESOURCES"]=lua.execute(
        (ROOT/"player/runtime/majiro_text_resources.lua").read_text("utf8"))
    lua.execute("""
      local scripts,pins,verifies={},{},{}
      local api
      local host={
        text_resource_names=function()return {'patched.mjo','start.mjo','loose.mjo'}end,
        patch_resource_names=function()return {}end,
        text_resource_pinned=function(name)return name=='start.mjo'end,
        pin_text_resource=function(name)
          pins[#pins+1]=name;api.verify(name,string.rep('b',64),'original',name)
        end,
        script=function(name)
          scripts[#scripts+1]=name
          api.verify(name,string.rep('a',64),'utf-8',name);return {name=name}
        end,
        seal_text_resources=function()verifies[#verifies+1]='sealed'end
      }
      local config={resource_hashes={['patched.mjo']=string.rep('a',64)}}
      api=RESOURCES.new('local',config,host,function()error('unexpected adapter')end)
      api.preflight()
      assert(#scripts==1 and scripts[1]=='patched.mjo','untouched original parsed during local preflight')
      assert(#pins==1 and pins[1]=='loose.mjo','loose original was not pinned')
      assert(api.seen['patched.mjo'] and verifies[1]=='sealed','declared patch not checked before seal')
      host.script=function(name)api.verify(name,string.rep('c',64),'utf-8',name)end
      assert(not pcall(api.preflight),'modified declared patch accepted')
      host.script=function(name)api.verify(name,string.rep('a',64),'utf-8',name);return {}end
      host.pin_text_resource=function(name)api.verify(name,string.rep('a',64),'cp936',name)end
      assert(not pcall(api.preflight),'undeclared localized script accepted')
    """)
    build=read_json(ROOT/"reports/player_core_build.json")
    generated=Path(build["build_evidence"])
    (folder/"repeated.mjo").write_bytes(mjo(
        (struct.pack("<HH",0x840,4)+b"abc\0")*1000+struct.pack("<H",0x82b),
        encrypted=False))
    source=folder/"startup_checks.cpp"
    source.write_text(r'''
#include "opcode_lookup.hpp"
#include "text_resource.hpp"
#include "lua_script.hpp"
#include <fstream>
#include <cassert>
#include <iostream>
extern "C" {
#include "lua.h"
void lua_createtable(lua_State*,int,int){}
void lua_pushnumber(lua_State*,lua_Number){}
void lua_pushlstring(lua_State*,const char*,size_t){}
void lua_rawseti(lua_State*,int,int){}
void lua_setfield(lua_State*,int,const char*){}
}
int main(int argc,char**argv){
    for(unsigned value=0;value<=65535;++value){
        auto expected=std::find_if(majiro::kOpcodes.begin(),majiro::kOpcodes.end(),
            [&](const auto& row){return row.code==value;});
        assert(majiro::opcode_definition(uint16_t(value))==expected);
    }
    const char* charsets[]={"UTF-8","windows-31j","GBK"};
    std::vector<uint8_t> ascii(128);
    for(unsigned i=0;i<128;++i)ascii[i]=uint8_t(i);
    for(auto from:charsets)for(auto to:charsets){
        assert(majiro::ascii_text_conversion(ascii,from,to));
        assert(majiro::ascii_text_conversion({},from,to));
        assert(!majiro::ascii_text_conversion({0x81,0x40},from,to));
    }
    assert(!majiro::ascii_text_conversion(ascii,"UTF-16","UTF-8"));
    assert(!majiro::ascii_text_conversion(ascii,"UTF-8","UTF-16"));
    assert(argc==2);
    std::ifstream input(argv[1],std::ios::binary);
    std::vector<uint8_t> script((std::istreambuf_iterator<char>(input)),{});
    unsigned decodes=0;
    auto decode=[&](const std::vector<uint8_t>& bytes){
        ++decodes;return std::string(bytes.begin(),bytes.end());
    };
    majiro::push_script(nullptr,"fixture",script,std::string(64,'a'),decode);
    assert(decodes==1);
    majiro::push_script(nullptr,"fixture",script,std::string(64,'a'),decode);
    assert(decodes==2); // decoding cache is scoped to one load/source.
    std::cout<<"{\"opcode_values\":65536,\"ascii_cases\":29,\"string_decode_checks\":2}";
}
''',encoding="utf8")
    env=msvc_environment()
    compiler=Path(env["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    executable=folder/"startup_checks.exe"
    for label,command in (
        ("compile",[str(compiler),"/nologo","/O2","/EHsc","/std:c++20","/utf-8",
                    "/I"+str(generated),"/I"+str(ROOT/"player/core"),
                    "/I"+str(ROOT/"references/player/lua/src"),str(source),
                    str(ROOT/"player/core/lua_script.cpp"),str(ROOT/"player/core/majiro_core.cpp"),
                    "/Fo"+str(folder)+os.sep,"/Fe"+str(executable)]),
        ("run",[str(executable),str(folder/"repeated.mjo")])):
        result=subprocess.run(command,cwd=folder,env=env,capture_output=True,timeout=60)
        (folder/(label+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(label+" failed: "+str(folder/(label+".txt")))
    report=json.loads(result.stdout)
    workspace=next((parent for parent in ROOT.parents if (parent/"recovered").is_dir()),ROOT.parent)
    baseline=workspace/"archive/2026-11/20261117_startup_baseline_majiro_probe.exe"
    receipt=baseline.with_name("20261117_startup_baseline_core_build.json")
    if baseline.is_file()and receipt.is_file():
        assert sha256(baseline)==read_json(receipt)["probe_sha256"]
        fixture=folder/"parser.arc"
        fixture.write_bytes(arc(3,[(b"start.mjo",mjo(
            struct.pack("<Hi",0x800,1)*100000+struct.pack("<H",0x82b)))]))
        observed={};timings={}
        for label,probe in (("baseline",baseline),("current",ROOT/"resources/player/majiro_probe.exe")):
            samples=[]
            for _ in range(3):
                began=time.perf_counter()
                result=subprocess.run([str(probe),str(fixture)],capture_output=True,timeout=30)
                samples.append((time.perf_counter()-began)*1000)
                assert result.returncode==0,result.stderr
                observed[label]=json.loads(result.stdout)
            timings[label]=round(statistics.median(samples),3)
        assert observed["baseline"]==observed["current"]
        report["parser_benchmark"]={"instructions":100001,"median_ms":timings,
            "improvement_percent":round(100*(1-timings["current"]/timings["baseline"]),2),
            "scope":"same synthetic MJO and validated ARC; process startup included; not tablet launch time"}
    report.update(status="passed",timestamp=timestamp(),preflight_cases=5,device_accessed=False,
                  evidence=str(folder.relative_to(ROOT)),
                  source_sha256={name:sha256(ROOT/name)for name in(
                      "player/core/opcode_lookup.hpp","player/core/text_resource.hpp",
                      "player/runtime/majiro_text_resources.lua")})
    write_json(folder/"report.json",report)
    write_json(ROOT/"reports/startup_performance_tests.json",report)
    print(json.dumps(report))
    return 0


if __name__=="__main__":raise SystemExit(main())
