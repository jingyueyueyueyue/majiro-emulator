#include "majiro_native_import.hpp"
#include "majiro_opcodes.hpp"
#include <algorithm>
#include <array>
#include <cstring>
#include <stdexcept>
#include <string_view>

namespace majiro {
namespace {
void require(bool value, const char* why) { if (!value) throw std::runtime_error(why); }
uint16_t u16(const std::vector<uint8_t>& data, size_t at) {
    require(at <= data.size() && data.size()-at>=2,"truncated import script field");
    return uint16_t(data[at]) | uint16_t(data[at+1])<<8;
}
uint32_t u32(const std::vector<uint8_t>& data, size_t at) {
    require(at <= data.size() && data.size()-at>=4,"truncated import script field");
    return uint32_t(data[at]) | uint32_t(data[at+1])<<8 | uint32_t(data[at+2])<<16 | uint32_t(data[at+3])<<24;
}
bool digest(std::string_view value) {
    return value.size()==64 && std::all_of(value.begin(),value.end(),
        [](char c){return(c>='0'&&c<='9')||(c>='a'&&c<='f');});
}
uint32_t rotate(uint32_t value,unsigned bits){return(value>>bits)|(value<<(32-bits));}
std::set<uint32_t> positions(const std::vector<uint8_t>& raw) {
    auto checked=read_script(raw);
    size_t start=32+size_t(checked.functions)*8;
    std::vector<uint8_t> code(raw.begin()+start,raw.end());
    if(std::memcmp(raw.data(),"MajiroObjX1.000",16)==0){
        std::array<uint8_t,1024> key{};
        for(uint32_t i=0;i<256;++i){
            uint32_t value=i;
            for(unsigned n=0;n<8;++n)value=(value>>1)^((value&1)?0xedb88320u:0);
            for(unsigned n=0;n<4;++n)key[i*4+n]=uint8_t(value>>(8*n));
        }
        for(size_t i=0;i<code.size();++i)code[i]^=key[i&1023];
    }
    size_t at=0;
    std::set<uint32_t> result;
    while(at<code.size()){
        result.insert(static_cast<uint32_t>(at));
        uint16_t opcode=u16(code,at);at+=2;
        auto definition=std::find_if(kOpcodes.begin(),kOpcodes.end(),
            [opcode](const auto& row){return row.code==opcode;});
        require(definition!=kOpcodes.end(),"native import script opcode missing");
        for(const char* field=definition->encoding;*field;++field){
            size_t bytes=0;
            if(*field=='s'||*field=='t'){bytes=size_t(u16(code,at))+2;}
            else if(*field=='c'){bytes=size_t(u16(code,at))*4+2;}
            else bytes=(*field=='f'||*field=='o'||*field=='a'||*field=='l')?2:4;
            require(at<=code.size()&&bytes<=code.size()-at,"truncated import script operand");
            at+=bytes;
        }
    }
    return result;
}
}

std::string native_import_sha256(const std::vector<uint8_t>& data) {
    static constexpr std::array<uint32_t,64> k{
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    require(data.size()<=64u*1024*1024,"native import digest input exceeds bound");
    std::array<uint32_t,8> state{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                                0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    size_t total=((data.size()+9+63)/64)*64;
    for(size_t block=0;block<total;block+=64){
        std::array<uint8_t,64> bytes{};
        for(size_t i=0;i<64;++i){
            size_t at=block+i;
            if(at<data.size())bytes[i]=data[at];
            else if(at==data.size())bytes[i]=0x80;
            else if(at>=total-8)bytes[i]=uint8_t((uint64_t(data.size())*8)>>(8*(total-at-1)));
        }
        std::array<uint32_t,64> w{};
        for(size_t i=0;i<16;++i)w[i]=uint32_t(bytes[i*4])<<24|uint32_t(bytes[i*4+1])<<16|
            uint32_t(bytes[i*4+2])<<8|bytes[i*4+3];
        for(size_t i=16;i<64;++i){
            uint32_t a=rotate(w[i-15],7)^rotate(w[i-15],18)^(w[i-15]>>3);
            uint32_t b=rotate(w[i-2],17)^rotate(w[i-2],19)^(w[i-2]>>10);
            w[i]=w[i-16]+a+w[i-7]+b;
        }
        auto v=state;
        for(size_t i=0;i<64;++i){
            uint32_t a=rotate(v[4],6)^rotate(v[4],11)^rotate(v[4],25);
            uint32_t choice=(v[4]&v[5])^(~v[4]&v[6]);
            uint32_t t1=v[7]+a+choice+k[i]+w[i];
            uint32_t b=rotate(v[0],2)^rotate(v[0],13)^rotate(v[0],22);
            uint32_t majority=(v[0]&v[1])^(v[0]&v[2])^(v[1]&v[2]);
            for(size_t j=7;j>0;--j)v[j]=v[j-1];
            v[4]+=t1;v[0]=t1+b+majority;
        }
        for(size_t i=0;i<8;++i)state[i]+=v[i];
    }
    static constexpr char hex[]="0123456789abcdef";
    std::string result;
    for(uint32_t value:state)for(int shift=28;shift>=0;shift-=4)result.push_back(hex[(value>>shift)&15]);
    return result;
}
NativeImportPlan plan_native_story_import(const std::vector<uint8_t>& bytes,
        const NativeImportManifest& manifest,const NativeImportScriptLoader& loader) {
    require(manifest.verified_same_script_version,"native save source version is not verified");
    require(digest(manifest.game_identity)&&digest(manifest.original_exe_sha256)&&digest(manifest.save_sha256),
            "native import identity digest missing");
    require(native_import_sha256(bytes)==manifest.save_sha256,"native import save bytes changed");
    auto story=read_native_story_save(bytes);
    require(story.frames.size()==1&&story.frame_depth==0,"nested native frame restore is not implemented");
    require(std::all_of(story.serialized_header.begin()+story.opaque_header_offset,
            story.serialized_header.end(),[](uint8_t b){return b==0;}),
            "native scene/media/wait state requires an unimplemented restore adapter");
    const auto& frame=story.frames[0];
    require(frame.fields[1]==0&&frame.fields[3]==0,"native parameter/return state is not mapped");
    require(std::all_of(frame.fields.begin()+5,frame.fields.end(),[](uint32_t value){return value==0;}),
            "native event handlers require an unimplemented restore adapter");
    require(frame.fields[2]==0&&story.groups[2].values.empty(),"native operand/local restore is not implemented");
    auto binding=std::find_if(manifest.scripts.begin(),manifest.scripts.end(),
        [&](const auto& row){return row.raw_name==frame.script_bytes;});
    require(binding!=manifest.scripts.end()&&digest(binding->sha256),"native saved script binding missing");
    require(binding->verified_idle_root_offsets.count(frame.fields[0])!=0,
            "native resume boundary has no original layout evidence");
    auto source=loader(binding->raw_name);
    require(native_import_sha256(source)==binding->sha256,"native saved script bytes changed");
    require(positions(source).count(frame.fields[0])!=0,"native PC is not an MJO instruction boundary");
    NativeImportPlan result;
    result.story=std::move(story);result.game_identity=manifest.game_identity;
    result.original_exe_sha256=manifest.original_exe_sha256;result.save_sha256=manifest.save_sha256;
    result.script=*binding;result.offset=frame.fields[0];result.identity_verified=true;
    result.supported_scope="verified idle root, neutral scene, no operand/local stack";
    return result;
}
}
