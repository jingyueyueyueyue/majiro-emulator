// Compiles the exact Android loader functions (generated text_loader.inc).
// Only JNI/filesystem transport is replaced. No game executable is opened.
#include "../../player/core/lua_script.hpp"
#include "../../player/core/majiro_core.hpp"
#include "../../player/core/image_resource.hpp"
#include "../../player/core/text_resource.hpp"
#include <windows.h>
#include <bcrypt.h>
#include <algorithm>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
extern "C" {
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"
}
namespace fs=std::filesystem;
lua_State* lua;
fs::path root,project;
std::string translation_mode;
struct PatchLayer{std::string directory,kind,encoding;};
std::vector<PatchLayer> patch_layers;
struct Source{int descriptor;std::string name,scope;};
struct Entry{size_t archive;majiro::Member member;std::string name;};
std::vector<Source> sources;
std::vector<fs::path> descriptors;
std::map<std::string,Entry> members;
std::set<std::string> names;
std::map<std::string,std::string> text_snapshot;
bool text_snapshot_ready=false;
majiro::TextSnapshotCache text_bytes;
size_t file_bytes=0,arc_bytes=0,hash_bytes=0,file_reads=0,arc_reads=0,hash_calls=0;
int parsed_scripts=0;
void need(bool value,const std::string& why){if(!value)throw std::runtime_error(why);}
std::string utf8(const fs::path& p){auto s=p.u8string();return {s.begin(),s.end()};}
std::vector<uint8_t> read_at(int fd,uint64_t offset,size_t size){
    need(size<=64*1024*1024,"fixture resource size");
    std::ifstream file(descriptors.at(fd),std::ios::binary);need(bool(file),"fixture open");
    file.seekg(offset);std::vector<uint8_t> bytes(size);file.read(reinterpret_cast<char*>(bytes.data()),size);
    need(size_t(file.gcount())==size,"fixture short read");arc_bytes+=size;++arc_reads;return bytes;
}
std::vector<uint8_t> java_read(const std::string& path,bool asset=false,bool* found=nullptr){
    auto file=(asset?project/"player":root)/fs::u8path(path);
    bool exists=fs::is_regular_file(file);if(found)*found=exists;
    if(!exists)return {};
    std::ifstream input(file,std::ios::binary);need(bool(input),"fixture open");
    auto size=fs::file_size(file);need(size<=64*1024*1024,"fixture size");
    std::vector<uint8_t> raw(size);input.read(reinterpret_cast<char*>(raw.data()),size);
    need(size_t(input.gcount())==size,"fixture read");
    if(!asset){file_bytes+=size;++file_reads;}
    return raw;
}
std::string hash(const std::vector<uint8_t>& data){
    hash_bytes+=data.size();++hash_calls;
    BCRYPT_ALG_HANDLE algorithm=nullptr;BCRYPT_HASH_HANDLE digest=nullptr;
    need(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0,"SHA provider");
    need(BCryptCreateHash(algorithm,&digest,nullptr,0,nullptr,0,0)>=0,"SHA creation");
    uint8_t bytes[32]{};
    auto status=BCryptHashData(digest,const_cast<PUCHAR>(data.data()),ULONG(data.size()),0);
    if(status>=0)status=BCryptFinishHash(digest,bytes,sizeof(bytes),0);
    BCryptDestroyHash(digest);BCryptCloseAlgorithmProvider(algorithm,0);need(status>=0,"SHA digest");
    std::ostringstream out;out<<std::hex<<std::setfill('0');
    for(auto byte:bytes)out<<std::setw(2)<<unsigned(byte);return out.str();
}
std::string convert(const std::vector<uint8_t>& bytes,const char* from,const char* to){
    auto page=[](const std::string& value)->UINT{
        if(value=="windows-31j")return 932;if(value=="GBK")return 936;if(value=="UTF-8")return CP_UTF8;
        throw std::runtime_error("unknown fixture charset");
    };
    if(bytes.empty())return {};
    UINT source=page(from),target=page(to);
    int count=MultiByteToWideChar(source,MB_ERR_INVALID_CHARS,reinterpret_cast<const char*>(bytes.data()),int(bytes.size()),nullptr,0);
    need(count>0,"invalid declared source encoding");
    std::wstring wide(count,0);
    need(MultiByteToWideChar(source,MB_ERR_INVALID_CHARS,reinterpret_cast<const char*>(bytes.data()),int(bytes.size()),wide.data(),count)==count,
        "fixture wide conversion");
    BOOL used=FALSE;BOOL* usedp=target==CP_UTF8?nullptr:&used;
    DWORD flags=target==CP_UTF8?WC_ERR_INVALID_CHARS:WC_NO_BEST_FIT_CHARS;
    count=WideCharToMultiByte(target,flags,wide.data(),int(wide.size()),nullptr,0,nullptr,usedp);
    need(count>0&&!used,"unmappable fixture text");
    std::string result(count,0);
    need(WideCharToMultiByte(target,flags,wide.data(),int(wide.size()),result.data(),count,nullptr,usedp)==count&&!used,
        "unmappable fixture text");return result;
}
#include "text_loader.inc"
int guarded(lua_State* state,lua_CFunction callback){
    try{return callback(state);}catch(const std::exception& error){lua_pushstring(state,error.what());}
    return lua_error(state);
}
int host_script(lua_State* state){++parsed_scripts;return guarded(state,script);}
int host_resource(lua_State* state){return guarded(state,resource_text);}
int host_pin(lua_State* state){return guarded(state,pin_text_resource);}
int host_game_text(lua_State* state){
    try{bool found=false;auto raw=java_read(argument(state,1),false,&found);
        if(found)lua_pushlstring(state,reinterpret_cast<const char*>(raw.data()),raw.size());else lua_pushnil(state);return 1;
    }catch(const std::exception& error){lua_pushstring(state,error.what());}return lua_error(state);
}
int host_cp932(lua_State* state){
    try{auto value=argument(state,1);auto raw=convert({value.begin(),value.end()},"UTF-8","windows-31j");
        lua_pushlstring(state,raw.data(),raw.size());return 1;
    }catch(const std::exception& error){lua_pushstring(state,error.what());}return lua_error(state);
}
int host_decode(lua_State* state){
    try{auto value=argument(state,1);auto raw=convert({value.begin(),value.end()},"windows-31j","UTF-8");
        lua_pushlstring(state,raw.data(),raw.size());return 1;
    }catch(const std::exception& error){lua_pushstring(state,error.what());}return lua_error(state);
}
int host_hash(lua_State* state){
    auto value=argument(state,1);need(value.starts_with("sha256\n"),"fixture storage is hash only");
    value=value.substr(7);auto digest="OK "+hash({value.begin(),value.end()});
    lua_pushlstring(state,digest.data(),digest.size());return 1;
}
int host_names(lua_State* state){
    lua_newtable(state);int i=1;for(const auto& name:names){
        lua_pushlstring(state,name.data(),name.size());lua_rawseti(state,-2,i++);
    }return 1;
}
int host_patch_names(lua_State* state){
    lua_newtable(state);int i=1;
    for(const auto& layer:patch_layers)for(const auto& file:fs::directory_iterator(root/fs::u8path(layer.directory))){
        auto name=utf8(file.path().filename());if(!majiro::text_resource_member(name))continue;
        auto row=layer.directory+"\t"+name;lua_pushlstring(state,row.data(),row.size());lua_rawseti(state,-2,i++);
    }return 1;
}
int host_seal(lua_State*){text_snapshot_ready=true;return 0;}
int host_clear_cache(lua_State*){text_bytes.clear();return 0;}
int host_module(lua_State* state){
    auto path=argument(state,1);auto bytes=java_read(path,true);
    if(luaL_loadbuffer(state,reinterpret_cast<const char*>(bytes.data()),bytes.size(),path.c_str())||
        lua_pcall(state,0,1,0))return lua_error(state);
    return 1;
}
int host_asset(lua_State* state){
    auto bytes=java_read(argument(state,1),true);
    lua_pushlstring(state,reinterpret_cast<const char*>(bytes.data()),bytes.size());return 1;
}
int host_mode(lua_State* state){lua_pushstring(state,translation_mode.c_str());return 1;}
int host_configuration(lua_State* state){
    lua_getglobal(state,"PATCH_CONFIG_JSON");return 1;
}
void call(const std::string& code,int results=0){
    need(luaL_loadbuffer(lua,code.data(),code.size(),"fixture")==0,"fixture Lua parse");
    if(lua_pcall(lua,0,results,0))throw std::runtime_error(lua_tostring(lua,-1));
}
int wmain(int argc,wchar_t** argv){
    try{
        need(argc==3||argc==4,"usage: text_resource_probe project plan.lua [uncached]");
        if(argc==4)text_bytes.limit=0;
        project=argv[1];lua=luaL_newstate();luaL_openlibs(lua);
        std::ifstream file(argv[2],std::ios::binary);std::string plan((std::istreambuf_iterator<char>(file)),{});
        call(plan,1);lua_setglobal(lua,"PLAN");
        call("return PLAN.root,PLAN.mode",2);translation_mode=argument(lua,-1);root=fs::u8path(argument(lua,-2));lua_settop(lua,0);
        call("return PLAN.layers",1);
        for(size_t i=1;i<=lua_objlen(lua,-1);++i){
            lua_rawgeti(lua,-1,int(i));patch_layers.push_back({field(lua,-1,"directory"),field(lua,-1,"kind"),field(lua,-1,"encoding")});
            lua_pop(lua,1);
        }lua_settop(lua,0);
        call("return PLAN.archives",1);
        for(size_t i=1;i<=lua_objlen(lua,-1);++i){
            lua_rawgeti(lua,-1,int(i));auto path=field(lua,-1,"path"),scope=field(lua,-1,"scope");lua_pop(lua,1);
            int fd=int(descriptors.size());descriptors.push_back(root/fs::u8path(path));
            auto archive=majiro::read_archive([fd](uint64_t at,uint8_t* out,size_t size){
                auto bytes=read_at(fd,at,size);std::copy(bytes.begin(),bytes.end(),out);
            },fs::file_size(descriptors.back()));
            size_t index=sources.size();sources.push_back({fd,path,scope});
            for(const auto& member:archive.members){
                auto name=convert({member.raw_name.begin(),member.raw_name.end()},"windows-31j","UTF-8");
                if(scope=="images"&&!majiro::image_archive_member(name))continue;
                members.emplace(key(name),Entry{index,member,name});
                if(majiro::text_resource_member(key(name)))names.insert(key(name));
            }
        }lua_settop(lua,0);
        for(const auto& path:fs::directory_iterator(root)){
            auto name=key(utf8(path.path().filename()));if(path.is_regular_file()&&majiro::text_resource_member(name))names.insert(name);
        }
        for(const auto& layer:patch_layers)for(const auto& path:fs::directory_iterator(root/fs::u8path(layer.directory))){
            auto name=key(utf8(path.path().filename()));if(path.is_regular_file()&&majiro::text_resource_member(name))names.insert(name);
        }
        lua_newtable(lua);
        auto bind=[](const char* name,lua_CFunction f){lua_pushcfunction(lua,f);lua_setfield(lua,-2,name);};
        bind("script",host_script);bind("resource_text",host_resource);bind("game_text",host_game_text);bind("pin_text_resource",host_pin);
        bind("cp932",host_cp932);bind("decode_bytes",host_decode);bind("storage",host_hash);
        bind("text_resource_names",host_names);bind("patch_resource_names",host_patch_names);bind("seal_text_resources",host_seal);
        bind("clear_snapshot_cache_for_test",host_clear_cache);
        bind("asset_text",host_asset);bind("text_mode",host_mode);bind("patch_configuration",host_configuration);
        lua_setglobal(lua,"HOST");lua_pushcfunction(lua,host_module);lua_setglobal(lua,"module");
        call(R"LUA(
          PATCH_CONFIG_JSON=module('runtime/majiro_json.lua').encode(PLAN.configuration)
          local boot=HOST.asset_text('runtime/player_boot.lua')
          local finish=assert(boot:find('local configuration=',1,true))
          -- The exact production boot prefix constructs, validates and seals.
          RES=assert(loadstring(boot:sub(1,finish-1)..'\nreturn resources'))()
          local codec=module('runtime/majiro_codec.lua')
          local VM=module('runtime/majiro_vm.lua')
          local ops=module('tables/opcodes.lua')
          executed=0;anchors=0
          if RES.text then
            for name,record in pairs(RES.text_data.scripts)do
              local source=HOST.script(name)
              local before=codec.encode(source.code)
              local invalid=codec.clone(source);invalid.source_sha256=string.rep('f',64)
              assert(not pcall(RES.text.apply,invalid)and codec.encode(invalid.code)==before)
              local changed=RES.text.apply(source)
              local trace={}
              local vm=VM.new{loader=function()return changed end,opcodes=ops,
                syscall=function()error('unexpected fixture syscall')end,
                text_ops={execute=function(_,kind,value)trace[#trace+1]={kind,value}end}}
              vm:begin(name)
              for _,entry in ipairs(record.entries)do
                vm:frame().index=assert(changed.positions[entry.offset])
                if entry.opcode==0x801 then vm:step();assert(vm:pop()==entry.value)
                else
                  trace={}
                  local steps=0
                  repeat vm:step();steps=steps+1;assert(steps<1000)
                  until vm:frame().script.code[vm:frame().index][1]==entry.resume_offset
                  local expected,at={},1
                  while true do
                    local ending=entry.value:find('\\n',at,true)
                    expected[#expected+1]={'text',ending and entry.value:sub(at,ending-1)or entry.value:sub(at)}
                    if not ending then break end
                    expected[#expected+1]={'proc'};expected[#expected+1]={'ctrl','n'};at=ending+2
                  end
                  assert(codec.encode(trace)==codec.encode(expected))
                end
                executed=executed+1
              end
              anchors=anchors+1
            end
          end
          if PLAN.test then assert(loadstring(PLAN.test))()end
        )LUA");
        // Exact identity uses content and owner scope; original and online agree.
        std::string identity="text-resources-v2\n"+std::string(translation_mode=="local"?"local":"original");
        for(const auto& row:text_snapshot)identity+="\n"+row.first+"\n"+row.second;
        auto source_hash=hash({identity.begin(),identity.end()});
        lua_getglobal(lua,"RES");lua_getfield(lua,-1,"identity");lua_pushstring(lua,source_hash.c_str());
        need(lua_pcall(lua,1,1,0)==0,"fixture identity");auto final_hash=argument(lua,-1);lua_settop(lua,0);
        lua_getglobal(lua,"executed");auto executed=lua_tointeger(lua,-1);lua_pop(lua,1);
        lua_getglobal(lua,"anchors");auto anchors=lua_tointeger(lua,-1);lua_pop(lua,1);
        // Changing only the owner, scope, or same-size digest is rejected.
        int rejected=0;
        for(const auto& name:names){
            majiro::TextSource source;auto bytes=read_member(name,&source);
            auto mutated=bytes;if(!mutated.empty())mutated.back()^=1;
            try{verify_text_resource(name,mutated,source);}catch(const std::exception&){++rejected;}
            source.scope="untyped_local";
            try{verify_text_resource(name,bytes,source);}catch(const std::exception&){++rejected;}
            break;
        }
        need(rejected==2,"stale resource checks failed");
        std::cout<<"{\"status\":\"passed\",\"scripts\":"<<parsed_scripts<<",\"resources\":"<<text_snapshot.size()
          <<",\"executed_replacements\":"<<executed<<",\"adapter_scripts\":"<<anchors
          <<",\"stale_rejections\":"<<rejected<<",\"identity\":\""<<final_hash
          <<"\",\"io\":{\"loose_bytes\":"<<file_bytes<<",\"archive_bytes\":"<<arc_bytes
          <<",\"loose_reads\":"<<file_reads<<",\"archive_reads\":"<<arc_reads
          <<",\"hash_bytes\":"<<hash_bytes<<",\"hash_calls\":"<<hash_calls
          <<",\"snapshot_bytes\":"<<text_bytes.bytes()<<"}}\n";
        lua_close(lua);return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 1;}
}
