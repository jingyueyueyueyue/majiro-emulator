// The generated include contains the actual Android raw-loader functions.
// Only filesystem/JNI and SHA transport are substituted for a desktop test.
#include "../../player/core/lua_script.hpp"
#include "../../player/core/majiro_core.hpp"
#include "../../player/core/image_resource.hpp"
#include <windows.h>
#include <bcrypt.h>
#include <algorithm>
#include <cmath>
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
fs::path raw_root;
int old_reads=0;
int overlay_reads=0,narc_reads=0;
std::string translation_mode="local";
struct PatchLayer{std::string directory,kind;};
std::vector<PatchLayer> patch_layers={{"patches/scenario","pinned_raw_patch"},{"patches/local/scripts","source_mjo"}};
std::string fixture_patch_directory="patches/scenario";
struct Source {int descriptor;};
struct Entry {size_t archive;majiro::Member member;std::string name;};
std::vector<Source> sources;
std::map<std::string,Entry> members;
void need(bool value,const std::string& why){if(!value)throw std::runtime_error(why);}
std::string utf8(const fs::path& path){
    auto value=path.u8string();return std::string(value.begin(),value.end());
}
std::vector<uint8_t> read_file(const fs::path& path){
    std::ifstream input(path,std::ios::binary);
    need(bool(input),"cannot open fixture input");
    auto size=fs::file_size(path);need(size<=64*1024*1024,"fixture input limit");
    std::vector<uint8_t> result(size);
    input.read(reinterpret_cast<char*>(result.data()),result.size());
    need(size_t(input.gcount())==result.size(),"short fixture read");return result;
}
std::vector<uint8_t> java_read(const std::string& name,bool asset=false,bool* found=nullptr){
    if(name.find("player-data")!=std::string::npos){++old_reads;throw std::runtime_error("legacy script read");}
    fs::path path;
    const std::string prefix=fixture_patch_directory+"/";
    if(name.rfind(prefix,0)==0){++overlay_reads;path=raw_root/fs::u8path(name.substr(prefix.size()));}
    else if(name.rfind("patches/narcissu/scripts/",0)==0){++narc_reads;}
    else if(name=="fixture-original.mjo"){
        if(found)*found=true;
        std::vector<uint8_t> plain(34);
        std::memcpy(plain.data(),"MajiroObjV1.000",16);
        plain[28]=2;plain[32]=0x2b;plain[33]=8;return plain;
    }
    if(found)*found=!path.empty()&&fs::is_regular_file(path);
    return path.empty()||!fs::is_regular_file(path)?std::vector<uint8_t>{}:read_file(path);
}
std::vector<uint8_t> read_at(int,uint64_t,size_t){throw std::runtime_error("unexpected archive read");}
std::string hash(const std::vector<uint8_t>& data){
    BCRYPT_ALG_HANDLE algorithm=nullptr;BCRYPT_HASH_HANDLE digest=nullptr;
    need(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0,"SHA provider");
    NTSTATUS status=BCryptCreateHash(algorithm,&digest,nullptr,0,nullptr,0,0);
    if(status<0){BCryptCloseAlgorithmProvider(algorithm,0);throw std::runtime_error("SHA creation");}
    uint8_t output[32]{};
    status=BCryptHashData(digest,const_cast<PUCHAR>(data.data()),ULONG(data.size()),0);
    if(status>=0)status=BCryptFinishHash(digest,output,sizeof(output),0);
    BCryptDestroyHash(digest);BCryptCloseAlgorithmProvider(algorithm,0);
    need(status>=0,"SHA digest");
    std::ostringstream text;text<<std::hex<<std::setfill('0');
    for(auto value:output)text<<std::setw(2)<<unsigned(value);return text.str();
}
std::string convert(const std::vector<uint8_t>& bytes,const char*,const char*){
    lua_getglobal(lua,"FIXTURE_CP932");
    lua_pushlstring(lua,reinterpret_cast<const char*>(bytes.data()),bytes.size());
    need(lua_pcall(lua,1,1,0)==0,"fixture CP932 decoder");
    size_t size;const char* value=lua_tolstring(lua,-1,&size);
    need(value!=nullptr,"fixture decoder result");
    std::string result(value,size);lua_pop(lua,1);return result;
}
#include "raw_loader.inc"

void load(const fs::path& path){
    auto bytes=read_file(path);
    need(luaL_loadbuffer(lua,reinterpret_cast<const char*>(bytes.data()),bytes.size(),"fixture")==0,
         "fixture Lua syntax");
    if(lua_pcall(lua,0,1,0))throw std::runtime_error(lua_tostring(lua,-1));
}
void json_string(std::ostream& out,const char* value,size_t size){
    constexpr char hex[]="0123456789abcdef";out<<'"';
    for(size_t i=0;i<size;++i){
        unsigned char c=value[i];
        if(c=='"'||c=='\\')out<<'\\'<<char(c);
        else if(c<32)out<<"\\u00"<<hex[c>>4]<<hex[c&15];
        else out<<char(c);
    }
    out<<'"';
}
void dump(std::ostream& out,int at,int depth=0){
    need(depth<16,"fixture JSON depth");if(at<0)at=lua_gettop(lua)+1+at;
    int type=lua_type(lua,at);
    if(type==LUA_TNIL)out<<"null";
    else if(type==LUA_TBOOLEAN)out<<(lua_toboolean(lua,at)?"true":"false");
    else if(type==LUA_TNUMBER){
        double value=lua_tonumber(lua,at);
        if(std::isnan(value))out<<"NaN";
        else if(std::isinf(value))out<<(value<0?"-Infinity":"Infinity");
        else out<<std::setprecision(17)<<value;
    }else if(type==LUA_TSTRING){size_t size;const char* text=lua_tolstring(lua,at,&size);json_string(out,text,size);}
    else if(type==LUA_TTABLE){
        bool array=true;size_t count=lua_objlen(lua,at),keys=0;
        lua_pushnil(lua);while(lua_next(lua,at)){
            ++keys;if(lua_type(lua,-2)!=LUA_TNUMBER)array=false;lua_pop(lua,1);
        }
        array=array&&count==keys;
        if(array){
            out<<'[';for(size_t i=1;i<=count;++i){if(i>1)out<<',';lua_rawgeti(lua,at,int(i));dump(out,-1,depth+1);lua_pop(lua,1);}out<<']';
        }else{
            out<<'{';bool first=true;lua_pushnil(lua);
            while(lua_next(lua,at)){
                need(lua_type(lua,-2)==LUA_TSTRING,"fixture JSON object key");
                if(!first)out<<',';first=false;
                size_t size;const char* text=lua_tolstring(lua,-2,&size);
                json_string(out,text,size);out<<':';dump(out,-1,depth+1);lua_pop(lua,1);
            }out<<'}';
        }
    }else throw std::runtime_error("fixture JSON type");
}
void save(const fs::path& path){
    std::ofstream output(path,std::ios::binary);need(bool(output),"fixture output");
    dump(output,-1);output<<'\n';
}
int wmain(int argc,wchar_t** argv){
    try{
        need(argc==6,"usage: probe metadata decoder module raw-dir output-dir");
        const fs::path metadata=argv[1],decoder=argv[2],module=argv[3];
        raw_root=argv[4];const fs::path output=argv[5];
        lua=luaL_newstate();luaL_openlibs(lua);
        load(metadata);lua_setglobal(lua,"PLAYER_PATCH_METADATA");
        load(decoder);lua_setglobal(lua,"FIXTURE_CP932");
        load(module);lua_getfield(lua,-1,"new");lua_getglobal(lua,"PLAYER_PATCH_METADATA");
        lua_getglobal(lua,"FIXTURE_CP932");
        need(lua_pcall(lua,2,1,0)==0,"raw patch module construction");
        lua_getfield(lua,-1,"decode");lua_setglobal(lua,"PLAYER_DECODE_PATCH");
        lua_setglobal(lua,"FIXTURE_PATCH");lua_settop(lua,0);
        int count=0,negative=0,configs=0;
        std::vector<fs::path> paths;
        for(const auto& entry:fs::directory_iterator(raw_root))
            if(entry.path().extension()==".mjo")paths.push_back(entry.path());
        std::sort(paths.begin(),paths.end());
        for(const auto& path:paths){
            auto name=utf8(path.filename());lua_settop(lua,0);lua_pushlstring(lua,name.data(),name.size());
            need(script(lua)==1,"native HOST.script returns one script");
            save(output/(path.filename().wstring()+L".raw.json"));
            lua_getglobal(lua,"FIXTURE_PATCH");lua_getfield(lua,-1,"apply");lua_pushvalue(lua,-3);
            if(lua_pcall(lua,1,1,0))throw std::runtime_error(lua_tostring(lua,-1));
            save(output/(path.filename().wstring()+L".json"));++count;
            auto raw=read_file(path);raw.back()^=1;
            try{verify_patch_member(name,raw);throw std::logic_error("changed hash accepted");}
            catch(const std::runtime_error&){++negative;}
        }
        for(const auto& entry:fs::directory_iterator(raw_root)){
            if(entry.path().extension()!=".cfg")continue;
            auto name=utf8(entry.path().filename());lua_settop(lua,0);lua_pushlstring(lua,name.data(),name.size());
            need(resource_text(lua)==1,"native HOST.resource_text returns one string");
            save(output/(entry.path().filename().wstring()+L".json"));++configs;
        }
        auto start=read_file(raw_root/"start.mjo");std::vector<majiro::ScriptRelocation> invalid;
        auto reject=[&](const std::vector<uint8_t>& data,const std::vector<majiro::ScriptRelocation>& repair){
            try{majiro::repair_script_jumps(data,repair);}
            catch(const std::runtime_error&){++negative;return;}
            throw std::runtime_error("malformed relocation accepted");
        };
        reject({},{});reject(std::vector<uint8_t>(start.begin(),start.begin()+31),{});
        reject(start,{{0,0,0}});reject(start,{{0xffffffff,0,0}});
        // A branch-looking word inside an ldstr payload is not an instruction.
        std::vector<uint8_t> fake(32+16);
        std::memcpy(fake.data(),"MajiroObjV1.000",16);
        fake[28]=16;fake[32]=1;fake[33]=8;fake[34]=10;
        fake[36]=0x2c;fake[37]=8;fake[38]=4;
        fake[46]=0x2b;fake[47]=8;
        reject(fake,{{4,14,14}});
        lua_settop(lua,0);lua_pushstring(lua,"fixture-original.mjo");need(script(lua)==1,"raw loose original fallback");
        lua_getfield(lua,-1,"patch_source");need(lua_isnil(lua,-1),"original falsely marked patched");++negative;
        fixture_patch_directory="patches/arbitrary-source";
        patch_layers[0].directory=fixture_patch_directory;
        lua_settop(lua,0);lua_pushstring(lua,"start.mjo");need(script(lua)==1,"arbitrary manifest script directory");
        lua_getfield(lua,-1,"patch_source");need(lua_toboolean(lua,-1)!=0,"declared arbitrary layer was not used");
        const int original_overlay_reads=overlay_reads,original_narc_reads=narc_reads;
        for(const auto& mode:{"original","online"}){
            translation_mode=mode;lua_settop(lua,0);lua_pushstring(lua,"fixture-original.mjo");
            need(script(lua)==1,"source-only script load");
            lua_getfield(lua,-1,"patch_source");need(lua_isnil(lua,-1),"source-only script falsely patched");++negative;
            need(overlay_reads==original_overlay_reads&&narc_reads==original_narc_reads,
                 "non-local mode read translation override");
        }
        translation_mode="local";
        lua_pushnil(lua);lua_setglobal(lua,"PLAYER_PATCH_METADATA");
        try{verify_patch_member("start.mjo",start);throw std::logic_error("missing metadata accepted");}
        catch(const std::runtime_error&){++negative;}
        need(old_reads==0,"legacy precompiled Lua was accessed");
        lua_close(lua);lua=nullptr;
        std::cout<<"{\"status\":\"passed\",\"scripts\":"<<count<<",\"configs\":"<<configs
            <<",\"native_rejections\":"<<negative<<",\"legacy_reads\":"<<old_reads
            <<",\"source_modes_overlay_reads\":0,\"arbitrary_layer_directory_verified\":true}\n";
        return 0;
    }catch(const std::exception& error){
        std::cerr<<error.what()<<'\n';if(lua)lua_close(lua);return 1;
    }
}
