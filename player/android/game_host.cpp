#include "../core/majiro_core.hpp"
#include "../core/majiro_image.hpp"
#include "../core/image_resource.hpp"
#include "../core/lua_script.hpp"
#include "../core/portable_pixels.hpp"
#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>
#include "render_recovery.hpp"
#include <jni.h>
#include <android/log.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <cstring>
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

namespace {
JNIEnv* env;
jclass bridge;
lua_State* lua;
SDL_Renderer* renderer;
majiro::android_host::Presentation presentation;
std::vector<uint8_t> screen_rgba;
uint64_t screen_cpu_updates=0,last_render_error_log=0;
bool render_paused=false;
std::string application_id;
std::string json_string(const std::string& text);
int screen_width, screen_height;
uint64_t began, clock_adjust;
std::string root_uri;
std::string translation_mode="original";
struct PatchLayer{std::string directory,kind;};
std::vector<PatchLayer> patch_layers;
struct Source { int descriptor; uint64_t bytes; majiro::Archive archive;std::string name;bool active=true;bool image_only=false; };
struct Entry { size_t archive; majiro::Member member; std::string name; };
std::vector<Source> sources;
std::vector<size_t> source_order;
std::map<std::string, Entry> members;
std::set<std::string> loose_names;
std::map<std::string, majiro::Image> image_cache;
std::map<std::string,bool> png_transparency_cache;
std::vector<uint8_t> password;
bool has_password = false;
std::map<std::string, std::pair<std::string, double>> audio_cache;
int debug_socket = -1;
struct DebugClient { int descriptor; std::string command; };
std::vector<DebugClient> debug_clients;
majiro::Image movie_rgba;
int movie_page=0,movie_sequence=-1;
bool movie_texture_mode=false,movie_surface_active=false;
bool movie_clear_background=false;
SDL_FRect movie_rect{0,0,1280,720};
void refresh_movie_frame();
std::map<int, bool> key_down;
bool cursor_warp_pending=false;
SDL_Cursor* script_cursor=nullptr;
float cursor_warp_x=0,cursor_warp_y=0;

void need(bool value, const std::string& why) { if (!value) throw std::runtime_error(why); }
void checked_java() {
    if (!env->ExceptionCheck()) return;
    jthrowable error = env->ExceptionOccurred();
    env->ExceptionClear();
    jclass cls = env->FindClass("java/lang/Throwable");
    auto method = env->GetMethodID(cls, "toString", "()Ljava/lang/String;");
    auto message = static_cast<jstring>(env->CallObjectMethod(error, method));
    const char* text = env->GetStringUTFChars(message, nullptr);
    std::string copy = text ? text : "Java host error";
    if (text) env->ReleaseStringUTFChars(message, text);
    env->DeleteLocalRef(message); env->DeleteLocalRef(cls); env->DeleteLocalRef(error);
    throw std::runtime_error(copy);
}
jmethodID method(const char* name, const char* signature) {
    auto result = env->GetStaticMethodID(bridge, name, signature); checked_java(); return result;
}
jbyteArray byte_array(const std::vector<uint8_t>& bytes) {
    auto value = env->NewByteArray(static_cast<jsize>(bytes.size())); checked_java();
    if (!bytes.empty()) env->SetByteArrayRegion(value, 0, static_cast<jsize>(bytes.size()),
                                               reinterpret_cast<const jbyte*>(bytes.data()));
    checked_java(); return value;
}
jbyteArray byte_array(const std::string& bytes) {
    return byte_array(std::vector<uint8_t>(bytes.begin(), bytes.end()));
}
jstring java_string(const std::string& text) {
    jclass cls = env->FindClass("java/lang/String");
    auto ctor = env->GetMethodID(cls, "<init>", "([BLjava/lang/String;)V");
    auto bytes = byte_array(text);
    auto encoding = env->NewStringUTF("UTF-8");
    auto value = static_cast<jstring>(env->NewObject(cls, ctor, bytes, encoding));
    checked_java();
    env->DeleteLocalRef(bytes); env->DeleteLocalRef(encoding); env->DeleteLocalRef(cls);
    return value;
}
std::string native_string(jstring value) {
    if (!value) return {};
    jclass cls = env->FindClass("java/lang/String");
    auto encoding = env->NewStringUTF("UTF-8");
    auto bytes = static_cast<jbyteArray>(env->CallObjectMethod(
        value, env->GetMethodID(cls, "getBytes", "(Ljava/lang/String;)[B"), encoding));
    checked_java();
    std::string result(static_cast<size_t>(env->GetArrayLength(bytes)), '\0');
    if (!result.empty()) env->GetByteArrayRegion(bytes, 0, result.size(), reinterpret_cast<jbyte*>(result.data()));
    env->DeleteLocalRef(bytes); env->DeleteLocalRef(encoding); env->DeleteLocalRef(cls);
    checked_java(); return result;
}
std::vector<uint8_t> native_bytes(jbyteArray bytes) {
    if (!bytes) return {};
    std::vector<uint8_t> result(static_cast<size_t>(env->GetArrayLength(bytes)));
    if (!result.empty()) env->GetByteArrayRegion(bytes, 0, result.size(), reinterpret_cast<jbyte*>(result.data()));
    checked_java(); return result;
}
std::vector<uint8_t> java_read(const std::string& path, bool asset = false, bool* found = nullptr) {
    auto name = java_string(path);
    auto bytes = static_cast<jbyteArray>(env->CallStaticObjectMethod(
        bridge, method(asset ? "asset" : "readFile", "(Ljava/lang/String;)[B"), name));
    env->DeleteLocalRef(name); checked_java();
    if (found) *found = bytes != nullptr;
    auto result = native_bytes(bytes); if (bytes) env->DeleteLocalRef(bytes); return result;
}
std::string convert(const std::vector<uint8_t>& data, const char* from, const char* to) {
    auto bytes = byte_array(data);
    auto first = env->NewStringUTF(from), second = env->NewStringUTF(to);
    auto output = static_cast<jbyteArray>(env->CallStaticObjectMethod(
        bridge, method("convert", "([BLjava/lang/String;Ljava/lang/String;)[B"), bytes, first, second));
    env->DeleteLocalRef(bytes); env->DeleteLocalRef(first); env->DeleteLocalRef(second);
    checked_java();
    auto result = native_bytes(output); env->DeleteLocalRef(output);
    return std::string(result.begin(), result.end());
}
std::string key(std::string name) {
    return majiro::resource_key(std::move(name));
}
std::vector<uint8_t> read_at(int descriptor, uint64_t offset, size_t size) {
    need(size <= 64u * 1024 * 1024, "resource read memory limit");
    std::vector<uint8_t> result(size);
    size_t done = 0;
    while (done < size) {
        auto got = pread(descriptor, result.data() + done, size - done, static_cast<off_t>(offset + done));
        if (got < 0 && errno == EINTR) continue;
        need(got > 0, "game archive read failed"); done += static_cast<size_t>(got);
    }
    return result;
}
void verify_patch_member(const std::string& name,const std::vector<uint8_t>& raw);
void push_source_script(lua_State* state,const std::string& name,const std::vector<uint8_t>& raw,bool patched);
std::string decode_source(const std::vector<uint8_t>& raw,bool patched);
bool read_patch(const std::string& name,std::vector<uint8_t>& bytes,bool& patched){
    patched=false;
    if(translation_mode!="local")return false;
    for(const auto& layer:patch_layers){
        const auto normalized=key(name);
        if(layer.kind=="source_mjo"&&!normalized.ends_with(".mjo"))continue;
        bool found=false;auto raw=java_read(layer.directory+"/"+normalized,false,&found);
        if(!found)continue;
        patched=layer.kind=="pinned_raw_patch";
        if(patched)verify_patch_member(name,raw);
        bytes=std::move(raw);return true;
    }
    return false;
}
std::vector<uint8_t> read_member(const std::string& name,bool* patch_source=nullptr) {
    if(patch_source)*patch_source=false;
    // A raw translation overlay is data, never a precompiled Lua program.
    bool patched=false;std::vector<uint8_t> overlay;
    if(read_patch(name,overlay,patched)){
        if(patch_source)*patch_source=patched;
        return overlay;
    }
    bool loose=false;auto raw=java_read(name,false,&loose);
    if(loose)return raw;
    auto found = members.find(key(name)); need(found != members.end(), "resource not found: " + name);
    const auto& entry = found->second;
    return read_at(sources[entry.archive].descriptor, entry.member.offset, entry.member.size);
}
void rebuild_members(){
    members.clear();
    for(auto index:source_order){
        const auto& source=sources[index];if(!source.active)continue;
        for(const auto& member:source.archive.members){
            auto name=convert(std::vector<uint8_t>(member.raw_name.begin(),member.raw_name.end()),
                "windows-31j","UTF-8");
            if(source.image_only&&!majiro::image_archive_member(name))continue;
            members.emplace(key(name),Entry{index,member,name});
        }
    }
    image_cache.clear();png_transparency_cache.clear();audio_cache.clear();password.clear();has_password=false;
}
std::string resource_name(const std::string& name,const std::vector<std::string>& extensions={}){
    std::vector<std::string> candidates{name};
    for(const auto& extension:extensions)candidates.push_back(name+extension);
    for(const auto& candidate:candidates){
        auto normalized=key(candidate);
        if(loose_names.count(normalized))return normalized;
        auto found=members.find(normalized);if(found!=members.end())return found->second.name;
    }
    throw std::runtime_error("resource missing: "+name);
}
bool image_transparency(const std::string& name) {
    const auto cached=png_transparency_cache.find(name);
    if(cached!=png_transparency_cache.end())return cached->second;
    std::vector<uint8_t> data;
    const auto found=members.find(name);
    if(!loose_names.count(name)&&found!=members.end()) {
        const auto& entry=found->second;
        data=read_at(sources[entry.archive].descriptor,entry.member.offset,std::min<size_t>(entry.member.size,33));
    }else data=read_member(name);
    if(data.size()>=29&&data[25]!=4&&data[25]!=6)data=read_member(name);
    const bool result=majiro::png_has_transparency(data);
    png_transparency_cache[name]=result;return result;
}
majiro::ImageResource image_resource(const std::string& name) {
    auto resource=majiro::resolve_page_image_resource(name,[](const auto& candidate){
        return loose_names.count(candidate)||members.count(candidate);
    });
    need(!resource.derived_mask()||image_transparency(resource.alpha_source),"PNG has no transparency mask: "+name);
    return resource;
}
std::string hash(const std::vector<uint8_t>& bytes) {
    auto data = byte_array(bytes);
    auto result = static_cast<jstring>(env->CallStaticObjectMethod(bridge, method("hash", "([B)Ljava/lang/String;"), data));
    env->DeleteLocalRef(data); checked_java();
    auto text = native_string(result); env->DeleteLocalRef(result); return text;
}
void text_field(lua_State* state, const char* name, const std::string& value) {
    lua_pushlstring(state, value.data(), value.size()); lua_setfield(state, -2, name);
}
void number_field(lua_State* state, const char* name, double value) {
    lua_pushnumber(state, value); lua_setfield(state, -2, name);
}
std::string argument(lua_State* state, int at) {
    size_t length; auto value = luaL_checklstring(state, at, &length); return std::string(value, length);
}
std::string field(lua_State* state, int at, const char* name) {
    lua_getfield(state, at, name);
    size_t length = 0; auto value = lua_tolstring(state, -1, &length);
    std::string result = value ? std::string(value, length) : ""; lua_pop(state, 1); return result;
}
double numeric(lua_State* state, int at, const char* name, double fallback = 0) {
    lua_getfield(state, at, name);
    double value = lua_isnil(state, -1) ? fallback : lua_tonumber(state, -1);
    lua_pop(state, 1); return value;
}
bool boolean(lua_State* state, int at, const char* name) {
    lua_getfield(state, at, name); bool value = lua_toboolean(state, -1); lua_pop(state, 1); return value;
}
void execute(const std::string& source, const std::string& name, int returns) {
    need(luaL_loadbuffer(lua, source.data(), source.size(), name.c_str()) == 0,
         lua_tostring(lua, -1) ? lua_tostring(lua, -1) : "Lua load error");
    int status = lua_pcall(lua, 0, returns, 0);
    if (status) throw std::runtime_error(lua_tostring(lua, -1));
}
void verify_patch_member(const std::string& name,const std::vector<uint8_t>& raw){
    const int base=lua_gettop(lua);
    try{
        lua_getglobal(lua,"PLAYER_PATCH_METADATA");
        need(lua_istable(lua,-1),"raw translation patch metadata absent");
        lua_getfield(lua,-1,"files");
        need(lua_istable(lua,-1),"raw translation patch file hashes absent");
        lua_getfield(lua,-1,key(name).c_str());
        need(lua_isstring(lua,-1),"raw translation patch member is not pinned: "+name);
        need(argument(lua,-1)==hash(raw),"raw translation patch member hash changed: "+name);
        lua_settop(lua,base);
    }catch(...){lua_settop(lua,base);throw;}
}
std::string decode_source(const std::vector<uint8_t>& raw,bool patched){
    if(!patched)return convert(raw,"windows-31j","UTF-8");
    const int base=lua_gettop(lua);
    try{
        lua_getglobal(lua,"PLAYER_DECODE_PATCH");
        need(lua_isfunction(lua,-1),"raw translation patch decoder absent");
        lua_pushlstring(lua,reinterpret_cast<const char*>(raw.data()),raw.size());
        if(lua_pcall(lua,1,1,0)!=0)throw std::runtime_error(argument(lua,-1));
        need(lua_isstring(lua,-1),"raw translation patch decoder returned non-text");
        auto value=argument(lua,-1);lua_settop(lua,base);return value;
    }catch(...){lua_settop(lua,base);throw;}
}
void push_source_script(lua_State* state,const std::string& name,const std::vector<uint8_t>& raw,bool patched){
    std::vector<majiro::ScriptRelocation> repairs;
    const int base=lua_gettop(state);
    const auto sha=hash(raw);
    if(patched){
        try{
            lua_getglobal(state,"PLAYER_PATCH_METADATA");
            need(lua_istable(state,-1),"raw translation patch metadata absent");
            lua_getfield(state,-1,"scripts");
            need(lua_istable(state,-1),"raw translation patch scripts absent");
            lua_getfield(state,-1,key(name).c_str());
            need(lua_istable(state,-1)&&field(state,-1,"sha256")==sha,
                "raw translation patch script hash changed: "+name);
            lua_getfield(state,-1,"relocations");
            need(lua_istable(state,-1),"raw translation patch relocations absent");
            const size_t count=lua_objlen(state,-1);
            need(count<=4096,"raw translation patch relocation limit");
            auto integer=[&](const char* name){
                lua_getfield(state,-1,name);
                need(lua_type(state,-1)==LUA_TNUMBER,"raw translation patch relocation is not numeric");
                double value=lua_tonumber(state,-1);
                need(value>=0&&value<=4294967295.0&&value==double(uint32_t(value)),
                    "invalid raw translation patch relocation number");
                lua_pop(state,1);return uint32_t(value);
            };
            for(size_t i=1;i<=count;++i){
                lua_rawgeti(state,-1,int(i));need(lua_istable(state,-1),"invalid patch relocation record");
                repairs.push_back({integer("offset"),integer("old_target"),integer("new_target")});
                lua_pop(state,1);
            }
            lua_settop(state,base);
        }catch(...){lua_settop(state,base);throw;}
    }
    const auto data=patched?majiro::repair_script_jumps(raw,repairs):raw;
    majiro::push_script(state,name,data,sha,[patched](const auto& bytes){return decode_source(bytes,patched);});
    if(patched){lua_pushboolean(state,1);lua_setfield(state,-2,"patch_source");}
}
std::vector<uint8_t> parse_hex(const std::string& hex) {
    need(hex.size() % 2 == 0, "invalid hex data");
    std::vector<uint8_t> result;
    for (size_t i = 0; i < hex.size(); i += 2)
        result.push_back(static_cast<uint8_t>(std::stoul(hex.substr(i, 2), nullptr, 16)));
    return result;
}
void discover_password() {
    if (has_password) return;
    bool patched=false;auto data = read_member("start.mjo",&patched);
    push_source_script(lua,"start.mjo",data,patched);
    lua_getfield(lua, -1, "code");
    std::string previous;
    int previous_opcode = 0;
    std::set<std::string> values;
    for (size_t i = 1; i <= lua_objlen(lua, -1); ++i) {
        lua_rawgeti(lua, -1, int(i));
        lua_rawgeti(lua, -1, 2); int opcode = int(lua_tonumber(lua, -1)); lua_pop(lua, 1);
        lua_rawgeti(lua, -1, 3);
        if ((opcode == 0x834 || opcode == 0x835) && lua_tonumber(lua, -1) == 0x7A7B6ED4u) {
            need(previous_opcode == 0x801, "dynamic image password requires executed setter");
            values.insert(previous);
        }
        if (opcode == 0x801) previous = argument(lua, -1);
        previous_opcode = opcode;
        lua_pop(lua, 2);
    }
    lua_pop(lua, 2);
    need(values.size() == 1, "encrypted image key is unavailable or ambiguous");
    auto raw = convert(std::vector<uint8_t>(values.begin()->begin(), values.begin()->end()), "UTF-8", "windows-31j");
    password.assign(raw.begin(), raw.end()); has_password = true;
}
void debug(const std::string& text);
majiro::Image load_image(std::string name, bool with_alpha=false, std::set<std::string> active = {}) {
    if(name=="movie://texture"){
        need(movie_rgba.width>0&&movie_rgba.height>0&&!movie_rgba.rgba.empty(),"movie frame unavailable");
        return movie_rgba;
    }
    if (name.rfind("save://", 0) == 0) {
        auto bytes = java_read(name); auto data = byte_array(bytes);
        auto array = static_cast<jintArray>(env->CallStaticObjectMethod(bridge, method("bitmap", "([B)[I"), data));
        env->DeleteLocalRef(data); checked_java();
        std::vector<jint> values(static_cast<size_t>(env->GetArrayLength(array)));
        env->GetIntArrayRegion(array, 0, values.size(), values.data()); env->DeleteLocalRef(array);
        majiro::Image image; image.width = values[0]; image.height = values[1];
        image.rgba.resize(size_t(image.width) * image.height * 4);
        for (size_t i = 2; i < values.size(); ++i) {
            auto value = uint32_t(values[i]); auto at = (i - 2) * 4;
            image.rgba[at] = uint8_t(value >> 16); image.rgba[at + 1] = uint8_t(value >> 8);
            image.rgba[at + 2] = uint8_t(value); image.rgba[at + 3] = uint8_t(value >> 24);
        }
        return image;
    }
    const auto resource=image_resource(name);
    if(resource.missing_page){
        debug("optional image absent; using original 1x1 page: "+name);
        return majiro::missing_page_image(resource);
    }
    if(resource.derived_mask())return majiro::transparency_mask(load_image(resource.alpha_source,false,active));
    name=resource.name;
    need(active.size() < 12 && active.insert(name).second, "image base cycle or depth");
    auto finish=[&](majiro::Image image){
        // A normal page is independent of its companion RC8. Only
        // page_create_file_withalfa asks the host to combine that mask.
        return majiro::image_for_page(std::move(image),name,with_alpha,
            [](const auto& mask){return members.count(mask)||loose_names.count(mask);},
            [&](const auto& mask){return load_image(mask,false,active);});
    };
    auto cached = image_cache.find(name);
    if (cached != image_cache.end()) return finish(cached->second);
    auto bytes = read_member(name);
    const auto& original_name = name;
    auto label_hash = hash(std::vector<uint8_t>(original_name.begin(), original_name.end()));
    bool localized_image = false;
    auto replacement = translation_mode=="local"?
        java_read("patches/images/" + label_hash.substr(0, 20) + ".png", false, &localized_image):std::vector<uint8_t>{};
    if (localized_image) bytes = std::move(replacement);
    majiro::Image image;
    if (bytes.size() >= 8 && ((bytes[0] == 0x89 && bytes[1] == 'P')||
            (bytes[0]=='B'&&bytes[1]=='M'))) {
        auto data = byte_array(bytes);
        auto array = static_cast<jintArray>(env->CallStaticObjectMethod(bridge, method("bitmap", "([B)[I"), data));
        env->DeleteLocalRef(data); checked_java();
        std::vector<jint> values(static_cast<size_t>(env->GetArrayLength(array)));
        env->GetIntArrayRegion(array, 0, values.size(), values.data()); env->DeleteLocalRef(array);
        image.width = values[0]; image.height = values[1]; image.rgba.resize(size_t(image.width) * image.height * 4);
        for (size_t i = 2; i < values.size(); ++i) {
            auto value = uint32_t(values[i]); auto at = (i - 2) * 4;
            image.rgba[at] = uint8_t(value >> 16); image.rgba[at + 1] = uint8_t(value >> 8);
            image.rgba[at + 2] = uint8_t(value); image.rgba[at + 3] = uint8_t(value >> 24);
        }
        if (name.size() >= 4 && name.substr(name.size() - 4) == ".rc8") {
            image.indices.resize(size_t(image.width) * image.height);
            for (unsigned i = 0; i < 256; ++i)
                image.palette[i * 3] = image.palette[i * 3 + 1] = image.palette[i * 3 + 2] = uint8_t(i);
            for (size_t i = 0; i < image.indices.size(); ++i) image.indices[i] = image.rgba[i * 4];
        }
    } else {
        bool encrypted = bytes.size() >= 8 && bytes[4] == 'T' && bytes[5] == 'S';
        if (encrypted && !has_password) discover_password();
        image = majiro::read_image(bytes, has_password ? &password : nullptr);
        if (!image.base_name.empty()) {
            auto base = convert(std::vector<uint8_t>(image.base_name.begin(), image.base_name.end()), "windows-31j", "UTF-8");
            majiro::combine_base(image, load_image(base,false,active));
        }
    }
    if (image_cache.size() >= 2) image_cache.erase(image_cache.begin());
    image_cache[name] = image;
    return finish(std::move(image));
}

template<int (*Function)(lua_State*)>
int guarded(lua_State* state) {
    try { return Function(state); }
    catch (const std::exception& error) { return luaL_error(state, "%s", error.what()); }
}
int asset_text(lua_State* state) {
    auto bytes = java_read(argument(state, 1), true);
    lua_pushlstring(state, reinterpret_cast<const char*>(bytes.data()), bytes.size()); return 1;
}
int game_text(lua_State* state) {
    bool found; auto bytes = java_read(argument(state, 1), false, &found);
    if (!found) lua_pushnil(state);
    else lua_pushlstring(state, reinterpret_cast<const char*>(bytes.data()), bytes.size());
    return 1;
}
int resource_text(lua_State* state) {
    bool patched=false;auto bytes=read_member(argument(state,1),&patched);
    auto text=decode_source(bytes,patched);
    lua_pushlstring(state, text.data(), text.size()); return 1;
}
int script_file_read(lua_State* state) {
    auto name=argument(state,1);auto path=java_string(name);
    auto data=static_cast<jbyteArray>(env->CallStaticObjectMethod(bridge,
        method("readScriptFile","(Ljava/lang/String;)[B"),path));
    env->DeleteLocalRef(path);checked_java();
    bool found=data!=nullptr;
    auto bytes=native_bytes(data);if(data)env->DeleteLocalRef(data);
    bool patched=false;
    if(!found){
        auto entry=members.find(key(name));
        if(entry!=members.end()||loose_names.count(key(name))){bytes=read_member(name,&patched);found=true;}
        else{
            if(read_patch(name,bytes,patched))found=true;
            else bytes=java_read(name,false,&found);
        }
    }
    if(!found){lua_pushnil(state);return 1;}
    auto text=decode_source(bytes,patched);lua_pushlstring(state,text.data(),text.size());return 1;
}
int script_file_write(lua_State* state) {
    auto name=java_string(argument(state,1));auto input=argument(state,2);
    auto raw=convert(std::vector<uint8_t>(input.begin(),input.end()),"UTF-8","windows-31j");
    auto data=byte_array(raw);
    env->CallStaticVoidMethod(bridge,method("writeScriptFile","(Ljava/lang/String;[B)V"),name,data);
    env->DeleteLocalRef(name);env->DeleteLocalRef(data);checked_java();return 0;
}
int cp932(lua_State* state) {
    auto text = argument(state, 1);
    auto result = convert(std::vector<uint8_t>(text.begin(), text.end()), "UTF-8", "windows-31j");
    lua_pushlstring(state, result.data(), result.size()); return 1;
}
int decode_bytes(lua_State* state){
    auto raw=argument(state,1);
    auto text=convert(std::vector<uint8_t>(raw.begin(),raw.end()),"windows-31j","UTF-8");
    lua_pushlstring(state,text.data(),text.size());return 1;
}
int service(lua_State* state){
    auto action=java_string(argument(state,1)),value=java_string(argument(state,2));
    auto output=static_cast<jstring>(env->CallStaticObjectMethod(bridge,
        method("service","(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;"),action,value));
    env->DeleteLocalRef(action);env->DeleteLocalRef(value);checked_java();
    auto result=native_string(output);env->DeleteLocalRef(output);
    lua_pushlstring(state,result.data(),result.size());return 1;
}
int translation(lua_State* state){
    auto action=java_string(argument(state,1)),payload=java_string(argument(state,2));
    auto output=static_cast<jstring>(env->CallStaticObjectMethod(bridge,
        method("translation","(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;"),action,payload));
    env->DeleteLocalRef(action);env->DeleteLocalRef(payload);checked_java();
    auto result=native_string(output);if(output)env->DeleteLocalRef(output);
    lua_pushlstring(state,result.data(),result.size());return 1;
}
int text_mode(lua_State* state){
    auto mode=static_cast<jstring>(env->CallStaticObjectMethod(bridge,method("translationMode","()Ljava/lang/String;")));
    checked_java();auto current=native_string(mode);if(mode)env->DeleteLocalRef(mode);
    lua_pushlstring(state,current.data(),current.size());return 1;
}
int patch_configuration(lua_State* state){
    auto value=static_cast<jstring>(env->CallStaticObjectMethod(
        bridge,method("patchConfiguration","()Ljava/lang/String;")));checked_java();
    auto text=native_string(value);if(value)env->DeleteLocalRef(value);
    lua_pushlstring(state,text.data(),text.size());return 1;
}
int member_names(lua_State* state) {
    lua_newtable(state); int index = 1;
    for (const auto& row : members) {
        lua_pushlstring(state, row.second.name.data(), row.second.name.size()); lua_rawseti(state, -2, index++);
    }
    auto rows=static_cast<jobjectArray>(env->CallStaticObjectMethod(bridge,
        method("resourceNames","()[Ljava/lang/String;")));checked_java();
    loose_names.clear();
    for(jsize i=0;i<env->GetArrayLength(rows);++i){
        auto item=static_cast<jstring>(env->GetObjectArrayElement(rows,i));auto name=native_string(item);
        env->DeleteLocalRef(item);loose_names.insert(key(name));
        lua_pushlstring(state,name.data(),name.size());lua_rawseti(state,-2,index++);
    }
    env->DeleteLocalRef(rows);
    std::set<std::string> actual=loose_names;
    for(const auto& row:members)actual.insert(row.first);
    for(const auto& name:actual)if(name.ends_with(".png")) {
        const auto mask=name.substr(0,name.size()-4)+"_.rc8";
        if(!actual.count(mask)&&image_transparency(name)){
            lua_pushlstring(state,mask.data(),mask.size());lua_rawseti(state,-2,index++);
        }
    }
    return 1;
}
int archive_control(lua_State* state){
    auto action=argument(state,1),name=key(argument(state,2));
    if(action=="release"){
        for(auto& source:sources)if(key(source.name)==name)source.active=false;
    }else if(action=="register"){
        size_t index=0;
        for(;index<sources.size();++index)if(key(sources[index].name)==name)break;
        if(index==sources.size()){
            auto path=java_string(name);
            auto value=static_cast<jstring>(env->CallStaticObjectMethod(bridge,
                method("openArchive","(Ljava/lang/String;)Ljava/lang/String;"),path));
            env->DeleteLocalRef(path);checked_java();auto row=native_string(value);env->DeleteLocalRef(value);
            if(row=="ABSENT"){lua_pushinteger(state,0);return 1;}
            auto tab=row.find('\t');need(tab!=std::string::npos,"invalid dynamic archive descriptor");
            int fd=std::stoi(row.substr(0,tab));uint64_t bytes=std::stoull(row.substr(tab+1));
            try{
                majiro::ReadAt read=[fd](uint64_t at,uint8_t* out,size_t count){
                    auto data=read_at(fd,at,count);std::copy(data.begin(),data.end(),out);
                };
                sources.push_back(Source{fd,bytes,majiro::read_archive(read,bytes),name,true});
            }catch(...){close(fd);throw;}
        }
        sources[index].active=true;
        source_order.erase(std::remove(source_order.begin(),source_order.end(),index),source_order.end());
        source_order.insert(source_order.begin(),index);
    }else throw std::runtime_error("unknown archive action: "+action);
    rebuild_members();lua_pushinteger(state,0);return 1;
}
int identity(lua_State* state) {
    auto source=static_cast<jstring>(env->CallStaticObjectMethod(
        bridge,method("sourceIdentity","()Ljava/lang/String;")));checked_java();
    need(source!=nullptr,"game source identity unavailable");
    std::string identity=native_string(source);env->DeleteLocalRef(source);
    for (const auto& row : sources)if(!row.image_only)identity += ":" + std::to_string(row.bytes);
    auto result = hash(std::vector<uint8_t>(identity.begin(), identity.end()));
    lua_pushstring(state, result.c_str()); return 1;
}
int engine_requirements(lua_State* state) {
    auto source=read_member("start.mjo");
    need(source.size()>=32,"Majiro start script is missing or truncated");
    const std::string signature(reinterpret_cast<const char*>(source.data()),15);
    need(signature=="MajiroObjX1.000"||signature=="MajiroObjV1.000"||
        signature=="MjPlainBytecode","unsupported Majiro start script format");
    unsigned version=0;
    for(const auto& row:sources)if(!row.image_only)version=std::max(version,row.archive.version);
    need(version>=1&&version<=3,"unsupported Majiro archive generation");
    lua_newtable(state);text_field(state,"family","Majiro");
    text_field(state,"script_signature",signature);
    number_field(state,"archive_version",version);
    auto rows = static_cast<jobjectArray>(env->CallStaticObjectMethod(
        bridge, method("engineFeatures", "()[Ljava/lang/String;")));
    checked_java();
    lua_newtable(state);
    const auto count = env->GetArrayLength(rows);
    for (jsize index = 0; index < count; ++index) {
        auto row = static_cast<jstring>(env->GetObjectArrayElement(rows, index));
        const auto text = native_string(row);
        lua_pushlstring(state, text.data(), text.size());
        lua_rawseti(state, -2, index + 1);
        env->DeleteLocalRef(row);
    }
    env->DeleteLocalRef(rows);
    lua_setfield(state,-2,"features");
    return 1;
}
int image_metadata(lua_State* state) {
    const auto resource=image_resource(argument(state,1));
    auto name=resource.derived_mask()?resource.alpha_source:resource.name;
    std::vector<uint8_t> bytes;
    if(resource.missing_page){
        bytes={};
    }else if(loose_names.count(key(name))){
        bytes=java_read(name);if(bytes.size()>32)bytes.resize(32);
    }else{
        const auto& entry=members.at(key(name));
        bytes=read_at(sources[entry.archive].descriptor,entry.member.offset,std::min<size_t>(entry.member.size,32));
    }
    uint32_t w = resource.missing_page?1:0, h = resource.missing_page?1:0;
    auto little = [&](size_t i) { return uint32_t(bytes[i]) | uint32_t(bytes[i + 1]) << 8
        | uint32_t(bytes[i + 2]) << 16 | uint32_t(bytes[i + 3]) << 24; };
    if (bytes.size() >= 20 && bytes[0] == 0x98) { w = little(8); h = little(12); }
    else if (bytes.size() >= 24 && bytes[0] == 0x89) {
        w = __builtin_bswap32(little(16)); h = __builtin_bswap32(little(20));
    }else if(bytes.size()>=26&&bytes[0]=='B'&&bytes[1]=='M'){
        const auto header=little(14);
        if(header>=40){
            w=little(18);const int64_t height=int32_t(little(22));
            h=uint32_t(height<0?-height:height);
        }else if(header==12){
            w=uint32_t(bytes[18])|uint32_t(bytes[19])<<8;
            h=uint32_t(bytes[20])|uint32_t(bytes[21])<<8;
        }
    }
    need(w && h && w <= 32768 && h <= 32768, "image metadata dimensions");
    lua_newtable(state); text_field(state, "name", resource.name);
    number_field(state, "width", w); number_field(state, "height", h);
    number_field(state, "depth", resource.derived_mask()||name.ends_with(".rc8") ? 8 : 32);
    number_field(state, "source_depth", resource.derived_mask()||name.ends_with(".rc8") ? 8 : 32);
    return 1;
}
int image_asset(lua_State* state) {
    auto name=image_resource(argument(state,1)).name;
    lua_pushlstring(state,name.data(),name.size());
    lua_replace(state, 1); image_metadata(state);
    lua_pushstring(state,name.c_str()); lua_setfield(state, -2, "file"); return 1;
}
int script(lua_State* state) {
    std::string name = argument(state, 1);
    bool patched=false;auto data = read_member(name,&patched);
    push_source_script(state,name,data,patched);
    return 1;
}
int graphics(lua_State* state) {
    if(movie_texture_mode)refresh_movie_frame();
    auto request=argument(state,1);
    auto result = majiro::pixels::command(request);
    if(result.rfind("ERR",0)==0){
        auto operation=request.substr(0,request.find('\n'));
        if(operation.size()>180)operation.resize(180);
        debug("pixel_error="+result+" operation="+operation);
    }
    lua_pushlstring(state, result.data(), result.size()); return 1;
}
int external_save(lua_State* state){
    auto action=java_string(argument(state,1)),data=java_string(argument(state,2));
    auto result=static_cast<jstring>(env->CallStaticObjectMethod(bridge,
        method("externalSave","(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;"),action,data));
    env->DeleteLocalRef(action);env->DeleteLocalRef(data);checked_java();
    auto text=native_string(result);env->DeleteLocalRef(result);
    lua_pushlstring(state,text.data(),text.size());return 1;
}
int storage(lua_State* state) {
    auto input = java_string(argument(state, 1));
    auto result = static_cast<jstring>(env->CallStaticObjectMethod(bridge,
        method("storage", "(Ljava/lang/String;)Ljava/lang/String;"), input));
    env->DeleteLocalRef(input); checked_java(); auto text = native_string(result); env->DeleteLocalRef(result);
    lua_pushlstring(state, text.data(), text.size()); return 1;
}
int dialog(lua_State* state) {
    auto action = java_string(argument(state, 1)), token = java_string(argument(state, 2));
    auto message = java_string(argument(state, 4)), caption = java_string(argument(state, 5));
    auto output = static_cast<jstring>(env->CallStaticObjectMethod(bridge,
        method("modalDialog", "(Ljava/lang/String;Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;)Ljava/lang/String;"),
        action, token, int(luaL_checknumber(state, 3)), message, caption));
    env->DeleteLocalRef(action); env->DeleteLocalRef(token);
    env->DeleteLocalRef(message); env->DeleteLocalRef(caption); checked_java();
    auto result = native_string(output); env->DeleteLocalRef(output);
    lua_pushlstring(state, result.data(), result.size()); return 1;
}
int debug_output(lua_State* state) {
    auto text=java_string(argument(state,1)),script=java_string(argument(state,2));
    env->CallStaticVoidMethod(bridge,
        method("diagnosticOutput","(Ljava/lang/String;Ljava/lang/String;I)V"),
        text,script,int(luaL_checknumber(state,3)));
    env->DeleteLocalRef(text);env->DeleteLocalRef(script);checked_java();return 0;
}
int load_font(lua_State* state) {
    auto name=java_string(argument(state,1));
    bool result=env->CallStaticBooleanMethod(bridge,method("loadFont","(Ljava/lang/String;)Z"),name);
    env->DeleteLocalRef(name);checked_java();lua_pushboolean(state,result);return 1;
}
int resize_window(lua_State* state) {
    int width=int(luaL_checknumber(state,1)),height=int(luaL_checknumber(state,2));
    need(width>0&&height>0&&width<=32768&&height<=32768,"invalid virtual client dimensions");
    auto window=SDL_GetRenderWindow(renderer);
    if(!(SDL_GetWindowFlags(window)&SDL_WINDOW_FULLSCREEN))
        need(SDL_SetWindowSize(window,width,height),SDL_GetError());
    need(SDL_SetRenderLogicalPresentation(renderer,width,height,SDL_LOGICAL_PRESENTATION_LETTERBOX),SDL_GetError());
    lua_pushboolean(state,true);return 1;
}
int wallpaper(lua_State* state) {
    auto image=load_image(argument(state,1));
    std::vector<jint> values(size_t(image.width)*image.height);
    for(size_t i=0;i<values.size();++i)
        values[i]=jint(uint32_t(image.rgba[i*4+3])<<24|uint32_t(image.rgba[i*4])<<16|
            uint32_t(image.rgba[i*4+1])<<8|image.rgba[i*4+2]);
    auto array=env->NewIntArray(values.size());
    env->SetIntArrayRegion(array,0,values.size(),values.data());
    int result=env->CallStaticIntMethod(bridge,method("wallpaper","(II[II)I"),
        int(image.width),int(image.height),array,int(luaL_checknumber(state,2)));
    env->DeleteLocalRef(array);checked_java();lua_pushinteger(state,result);return 1;
}
int change_cursor(lua_State* state) {
    auto bytes=java_read("movie/"+argument(state,1));
    need(bytes.size()>=22,"cursor resource missing or truncated");
    auto u16=[&](size_t at){need(at+2<=bytes.size(),"cursor header truncated");
        return unsigned(bytes[at])|(unsigned(bytes[at+1])<<8);};
    auto u32=[&](size_t at){need(at+4<=bytes.size(),"cursor field truncated");
        return uint32_t(bytes[at])|uint32_t(bytes[at+1])<<8|uint32_t(bytes[at+2])<<16|uint32_t(bytes[at+3])<<24;};
    need(u16(0)==0&&u16(2)==2&&u16(4)>0,"invalid Windows cursor directory");
    size_t offset=u32(18),length=u32(14);need(offset<=bytes.size()&&length<=bytes.size()-offset,"cursor data range");
    need(length>=40&&u32(offset)>=40,"unsupported cursor DIB");
    unsigned width=u32(offset+4),height=u32(offset+8)/2,depth=u16(offset+14);
    need(width>0&&height>0&&width<=256&&height<=256&&depth==32&&u32(offset+16)==0,
        "only uncompressed 32-bit cursor DIB is supported");
    size_t begin=offset+u32(offset),size=size_t(width)*height*4;
    need(begin<=bytes.size()&&size<=bytes.size()-begin,"cursor pixels truncated");
    std::vector<uint8_t> rgba(size);
    for(unsigned y=0;y<height;++y)for(unsigned x=0;x<width;++x){
        size_t from=begin+(size_t(height-y-1)*width+x)*4,to=(size_t(y)*width+x)*4;
        rgba[to]=bytes[from+2];rgba[to+1]=bytes[from+1];rgba[to+2]=bytes[from];rgba[to+3]=bytes[from+3];
    }
    auto surface=SDL_CreateSurfaceFrom(int(width),int(height),SDL_PIXELFORMAT_RGBA32,rgba.data(),int(width*4));
    need(surface!=nullptr,SDL_GetError());
    auto cursor=SDL_CreateColorCursor(surface,int(u16(10)),int(u16(12)));SDL_DestroySurface(surface);
    need(cursor!=nullptr,SDL_GetError());need(SDL_SetCursor(cursor),SDL_GetError());
    if(script_cursor)SDL_DestroyCursor(script_cursor);script_cursor=cursor;
    lua_pushboolean(state,true);return 1;
}
int set_clock(lua_State* state) {
    clock_adjust = uint64_t(luaL_checknumber(state, 1)) - (SDL_GetTicks() - began); return 0;
}
int platform(lua_State* state) {
    auto action=argument(state,1);
    if(action=="caps_get"){
        lua_pushinteger(state,(SDL_GetModState()&SDL_KMOD_CAPS)?1:0);return 1;
    }
    if(action=="service_events"){
        SDL_PumpEvents();
        auto window=SDL_GetRenderWindow(renderer);
        if(window)SDL_RaiseWindow(window);
        lua_pushinteger(state,0);return 1;
    }
    luaL_checktype(state,2,LUA_TTABLE);
    auto parameter=[&](int at){
        lua_rawgeti(state,2,at);double result=luaL_checknumber(state,-1);lua_pop(state,1);return result;
    };
    if(action=="caps_set"){
        auto mods=SDL_GetModState();
        SDL_SetModState(static_cast<SDL_Keymod>(parameter(1)!=0
            ?mods|SDL_KMOD_CAPS:mods&~SDL_KMOD_CAPS));
    }else if(action=="display_scale"){
        auto value=parameter(1);
        auto window=SDL_GetRenderWindow(renderer);
        if(!(SDL_GetWindowFlags(window)&SDL_WINDOW_FULLSCREEN))
            need(SDL_SetWindowSize(window,value==2?800:1024,value==2?450:576),SDL_GetError());
    }else if(action=="fullscreen_mode"){
        int minimum_w=int(parameter(1)),minimum_h=int(parameter(2));
        auto display=SDL_GetDisplayForWindow(SDL_GetRenderWindow(renderer));
        int count=0;auto modes=SDL_GetFullscreenDisplayModes(display,&count);
        int best_w=0,best_h=0;
        for(int at=0;at<count;++at){
            auto mode=modes[at];
            if(mode->w>=minimum_w&&mode->h>=minimum_h&&
                (best_w==0||int64_t(mode->w)*mode->h<int64_t(best_w)*best_h)){
                best_w=mode->w;best_h=mode->h;
            }
        }
        SDL_free(modes);
        if(best_w==0){
            auto mode=SDL_GetDesktopDisplayMode(display);need(mode!=nullptr,SDL_GetError());
            best_w=mode->w;best_h=mode->h;
        }
        lua_newtable(state);lua_pushinteger(state,best_w);lua_rawseti(state,-2,1);
        lua_pushinteger(state,best_h);lua_rawseti(state,-2,2);return 1;
    }else if(action=="cursor_move"){
        cursor_warp_x=float(parameter(1));cursor_warp_y=float(parameter(2));cursor_warp_pending=true;
        float wx=0,wy=0;
        need(SDL_RenderCoordinatesToWindow(renderer,cursor_warp_x,cursor_warp_y,&wx,&wy),SDL_GetError());
        SDL_WarpMouseInWindow(SDL_GetRenderWindow(renderer),wx,wy);
    }else throw std::runtime_error("unsupported platform action: "+action);
    lua_pushinteger(state,0);return 1;
}
int screen_mode(lua_State* state) {
    bool desired = lua_toboolean(state, 1);
    auto window = SDL_GetRenderWindow(renderer);
    need(window != nullptr && SDL_SetWindowFullscreen(window, desired), SDL_GetError());
    lua_pushboolean(state, (SDL_GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN) != 0);
    return 1;
}
int screen_fit(lua_State* state) {
    auto value=argument(state,1);
    char* end=nullptr;auto fit=std::strtol(value.c_str(),&end,10)!=0;
    int width=screen_width,height=screen_height;
    if(width<=0||height<=0){
        SDL_RendererLogicalPresentation mode;
        need(SDL_GetRenderLogicalPresentation(renderer,&width,&height,&mode),SDL_GetError());
    }
    need(width>0&&height>0,"screen fit requires initialized game dimensions");
    need(SDL_SetRenderLogicalPresentation(renderer,width,height,
        fit?SDL_LOGICAL_PRESENTATION_LETTERBOX:SDL_LOGICAL_PRESENTATION_STRETCH),SDL_GetError());
    lua_pushinteger(state,0);return 1;
}
int font_dialog(lua_State* state) {
    auto current = java_string(argument(state, 1));
    auto selected = static_cast<jstring>(env->CallStaticObjectMethod(
        bridge, method("fontDialog", "(Ljava/lang/String;I)Ljava/lang/String;"), current,
        int(luaL_optnumber(state,2,0))));
    env->DeleteLocalRef(current); checked_java();
    auto result = native_string(selected); env->DeleteLocalRef(selected);
    lua_pushlstring(state, result.data(), result.size()); return 1;
}
int font_filter(lua_State* state) {
    auto filter=java_string(argument(state,1));
    env->CallStaticVoidMethod(bridge,method("fontFilter","(Ljava/lang/String;)V"),filter);
    env->DeleteLocalRef(filter);checked_java();return 0;
}
int media_asset(lua_State* state) {
    auto name = argument(state, 1);
    auto found = audio_cache.find(key(name));
    if (found == audio_cache.end()) {
        auto bytes = byte_array(read_member(name));
        auto filename = java_string(name);
        auto path = static_cast<jstring>(env->CallStaticObjectMethod(
            bridge, method("cacheMedia", "(Ljava/lang/String;[B)Ljava/lang/String;"), filename, bytes));
        env->DeleteLocalRef(bytes); env->DeleteLocalRef(filename); checked_java();
        auto duration = env->CallStaticLongMethod(bridge, method("duration", "(Ljava/lang/String;)J"), path);
        checked_java(); auto cached_path = native_string(path); env->DeleteLocalRef(path);
        found = audio_cache.emplace(key(name), std::make_pair(cached_path, double(duration))).first;
    }
    lua_newtable(state); text_field(state, "file", found->second.first);
    number_field(state, "duration_ms", found->second.second); return 1;
}
int audio(lua_State* state) {
    if(argument(state,1)=="frequency"){
        auto id=java_string(field(state,2,"id"));
        bool result=env->CallStaticBooleanMethod(bridge,method("audioFrequency","(Ljava/lang/String;I)Z"),
            id,int(numeric(state,2,"frequency")));
        env->DeleteLocalRef(id);checked_java();lua_pushboolean(state,result);return 1;
    }
    auto action = java_string(argument(state, 1)), id = java_string(field(state, 2, "id"));
    auto file = java_string(field(state, 2, "file")), loop_file = java_string(field(state, 2, "loop_file"));
    bool result = env->CallStaticBooleanMethod(bridge,
        method("audio", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;ZIIIIZ)Z"),
        action, id, file, loop_file, boolean(state, 2, "loop"),
        int(numeric(state, 2, "volume", 1000)), int(numeric(state, 2, "pan")), int(numeric(state, 2, "fade_ms")),
        int(numeric(state, 2, "offset_ms")), boolean(state,2,"paused_at"));
    env->DeleteLocalRef(action); env->DeleteLocalRef(id); env->DeleteLocalRef(file); env->DeleteLocalRef(loop_file);
    checked_java(); lua_pushboolean(state, result); return 1;
}
int movie_command(const std::string& operation,const std::string& path,int volume,bool loop,jlong from,jlong to){
    auto action=java_string(operation),file=java_string(path);
    int result = env->CallStaticIntMethod(bridge,
        method("movie", "(Ljava/lang/String;Ljava/lang/String;IZJJ)I"),action,file,volume,
        jboolean(loop),from,to);
    env->DeleteLocalRef(action); env->DeleteLocalRef(file); checked_java();
    return result;
}
void refresh_movie_frame(){
    if(!movie_texture_mode&&!movie_surface_active)return;
    auto array=static_cast<jintArray>(env->CallStaticObjectMethod(bridge,method("movieFrame","()[I")));
    checked_java();
    if(!array)return;
    jsize count=env->GetArrayLength(array);
    need(count>=3,"movie RGBA frame missing dimensions");
    jint head[3];env->GetIntArrayRegion(array,0,3,head);checked_java();
    if(head[2]==movie_sequence){env->DeleteLocalRef(array);return;}
    need(head[0]>0&&head[1]>0&&uint64_t(head[0])*head[1]<=16u*1024*1024
         &&uint64_t(head[0])*head[1]+3==uint64_t(count),"movie RGBA frame bounds");
    std::vector<jint> values(size_t(count)-3);
    env->GetIntArrayRegion(array,3,count-3,values.data());env->DeleteLocalRef(array);checked_java();
    movie_rgba.width=unsigned(head[0]);movie_rgba.height=unsigned(head[1]);
    movie_rgba.rgba.resize(values.size()*4);
    for(size_t i=0;i<values.size();++i){
        uint32_t pixel=uint32_t(values[i]);
        movie_rgba.rgba[i*4]=uint8_t(pixel>>16);movie_rgba.rgba[i*4+1]=uint8_t(pixel>>8);
        movie_rgba.rgba[i*4+2]=uint8_t(pixel);movie_rgba.rgba[i*4+3]=uint8_t(pixel>>24);
    }
    movie_sequence=head[2];
    if(movie_texture_mode&&movie_page>0){
        auto response=majiro::pixels::command("load "+std::to_string(movie_page)+" 0 movie://texture");
        need(response.rfind("OK",0)==0,response);
    }else if(movie_surface_active){
        presentation.movie.changed();
    }
}
int movie(lua_State* state) {
    auto action=argument(state,1);
    if(action=="texture_width"){lua_pushnumber(state,movie_rgba.width);return 1;}
    if(action=="texture_height"){lua_pushnumber(state,movie_rgba.height);return 1;}
    if(action=="texture"){
        int active=movie_command("status","",1000,false,0,-1);
        if(active)refresh_movie_frame();
        lua_pushnumber(state,active&&movie_texture_mode?movie_page:0);return 1;
    }
    bool playing=action=="play"||action=="play_repeat"||action=="play_fromto"||action=="texture_play";
    if(playing){
        if(movie_page>0){
            majiro::pixels::command("release "+std::to_string(movie_page));
            movie_page=0;
        }
        movie_texture_mode=action=="texture_play";movie_surface_active=!movie_texture_mode;
        movie_clear_background=numeric(state,2,"clear",1)!=0;
        movie_sequence=-1;movie_rgba={};
        presentation.movie.invalidate();
        movie_rect={float(numeric(state,2,"x")),float(numeric(state,2,"y")),
                    float(numeric(state,2,"width",screen_width>0?screen_width:1280)),
                    float(numeric(state,2,"height",screen_height>0?screen_height:720))};
        if(movie_texture_mode){
            movie_page=int(numeric(state,2,"page_hint"));
            need(movie_page>0,"movie texture requires reserved page identifier");
        }
    }
    int result=movie_command(action,field(state,2,"file"),int(numeric(state,2,"volume",1000)),
                             boolean(state,2,"repeat_play"),jlong(numeric(state,2,"range_from")),
                             jlong(numeric(state,2,"range_to",-1)));
    if(playing)refresh_movie_frame();
    if(action=="status"&&result==0)movie_surface_active=false;
    if(action=="stop"||action=="texture_stop"){
        if(movie_page>0)majiro::pixels::command("release "+std::to_string(movie_page));
        movie_page=0;movie_texture_mode=false;movie_surface_active=false;movie_rgba={};
        presentation.movie.invalidate();
    }
    lua_pushnumber(state, result); return 1;
}
int movies(lua_State* state) {
    auto list = static_cast<jobjectArray>(env->CallStaticObjectMethod(
        bridge, method("movies", "()[Ljava/lang/String;"))); checked_java();
    lua_newtable(state);
    for (jsize i = 0; i < env->GetArrayLength(list); ++i) {
        auto text = static_cast<jstring>(env->GetObjectArrayElement(list, i));
        auto value = native_string(text); env->DeleteLocalRef(text);
        auto tab = value.find('\t'); need(tab != std::string::npos, "movie catalogue data");
        auto name = value.substr(0, tab);
        lua_newtable(state); text_field(state, "file", "movie/" + name);
        number_field(state, "duration_ms", std::stod(value.substr(tab + 1)));
        lua_setfield(state, -2, key(name).c_str());
    }
    env->DeleteLocalRef(list); return 1;
}
void host_functions() {
    lua_newtable(lua);
#define HOST(NAME) lua_pushcfunction(lua, guarded<NAME>); lua_setfield(lua, -2, #NAME)
    HOST(asset_text); HOST(game_text); HOST(resource_text); HOST(cp932);
    HOST(decode_bytes);HOST(service);HOST(translation);HOST(text_mode);HOST(patch_configuration);
    HOST(archive_control);
    HOST(script_file_read);HOST(script_file_write);
    HOST(member_names); HOST(identity); HOST(engine_requirements); HOST(image_metadata); HOST(image_asset); HOST(script);
    HOST(graphics); HOST(storage);HOST(external_save); HOST(dialog);HOST(platform); HOST(debug_output); HOST(load_font);
    HOST(wallpaper); HOST(change_cursor);
    HOST(resize_window);
    HOST(font_filter);
    HOST(set_clock); HOST(screen_mode);HOST(screen_fit); HOST(font_dialog);
    HOST(media_asset); HOST(audio); HOST(movie); HOST(movies);
#undef HOST
    lua_setglobal(lua, "HOST");
}
void initialize_sources() {
    auto mode=static_cast<jstring>(env->CallStaticObjectMethod(bridge,
        method("translationMode","()Ljava/lang/String;")));checked_java();
    translation_mode=native_string(mode);if(mode)env->DeleteLocalRef(mode);
    need(translation_mode=="original"||translation_mode=="local"||translation_mode=="online","invalid translation mode");
    patch_layers.clear();
    auto layers=static_cast<jobjectArray>(env->CallStaticObjectMethod(bridge,method("patchLayers","()[Ljava/lang/String;")));
    checked_java();need(layers!=nullptr,"patch layer declarations unavailable");
    for(jsize i=0;i<env->GetArrayLength(layers);++i){
        auto value=static_cast<jstring>(env->GetObjectArrayElement(layers,i));auto row=native_string(value);
        env->DeleteLocalRef(value);auto tab=row.find('\t');
        need(tab!=std::string::npos,"invalid patch layer declaration");
        patch_layers.push_back({row.substr(tab+1),row.substr(0,tab)});
    }
    env->DeleteLocalRef(layers);
    auto list = static_cast<jobjectArray>(env->CallStaticObjectMethod(bridge, method("archives", "()[Ljava/lang/String;")));
    checked_java();
    std::vector<std::string> rows;
    for (jsize i = 0; i < env->GetArrayLength(list); ++i) {
        auto text = static_cast<jstring>(env->GetObjectArrayElement(list, i));
        rows.push_back(native_string(text)); env->DeleteLocalRef(text);
    }
    env->DeleteLocalRef(list);
    // Java returns explicit highest-priority-first manifest layers, then
    // reverse-lexical base archives. Do not reorder declared patch precedence.
    for (const auto& row : rows) {
        auto first = row.find('\t'), second = row.find('\t', first + 1);
        need(first != std::string::npos && second != std::string::npos, "invalid archive descriptor");
        int fd = std::stoi(row.substr(first + 1, second - first - 1));
        const auto third=row.find('\t',second+1);
        uint64_t size = std::stoull(row.substr(second+1,third==std::string::npos?third:third-second-1));
        const bool image_only=third!=std::string::npos&&row.substr(third+1)=="images";
        need(third==std::string::npos||image_only,"unknown archive resource scope");
        majiro::ReadAt read = [fd](uint64_t at, uint8_t* out, size_t count) {
            auto bytes = read_at(fd, at, count); std::copy(bytes.begin(), bytes.end(), out);
        };
        auto archive = majiro::read_archive(read, size);
        size_t index = sources.size();
        for (const auto& member : archive.members) {
            auto name = convert(std::vector<uint8_t>(member.raw_name.begin(), member.raw_name.end()), "windows-31j", "UTF-8");
            if(image_only&&!majiro::image_archive_member(name))continue;
            members.emplace(key(name), Entry{index, member, name});
        }
        source_order.push_back(index);
        sources.push_back(Source{fd, size, std::move(archive),row.substr(0,first),true,image_only});
    }
    need(!sources.empty(), "no game archives found");
}

majiro::android_host::FrameView screen_frame() {
    return {screen_width,screen_height,&screen_rgba};
}
majiro::android_host::FrameView movie_frame() {
    return {int(movie_rgba.width),int(movie_rgba.height),&movie_rgba.rgba};
}
std::string render_stats() {
    int output_width=0,output_height=0;
    bool output_ok=renderer&&SDL_GetRenderOutputSize(renderer,&output_width,&output_height);
    auto name=renderer?SDL_GetRendererName(renderer):nullptr;
    auto window=renderer?SDL_GetRenderWindow(renderer):nullptr;
    std::ostringstream out;
    out<<"{\"schema\":1,\"application_id\":"<<json_string(application_id)
       <<",\"socket\":"<<json_string(majiro::android_host::socket_name(application_id))
       <<",\"renderer\":"<<json_string(name?name:"")
       <<",\"output_ok\":"<<(output_ok?"true":"false")
       <<",\"output_width\":"<<output_width<<",\"output_height\":"<<output_height
       <<",\"window_flags\":"<<(window?uint64_t(SDL_GetWindowFlags(window)):0)
       <<",\"paused\":"<<(render_paused?"true":"false")
       <<",\"cpu_updates\":"<<screen_cpu_updates
       <<",\"screen_texture\":"<<(presentation.screen.get()?"true":"false")
       <<",\"screen_dirty\":"<<(presentation.screen.dirty()?"true":"false")
       <<",\"screen_uploads\":"<<presentation.screen.uploads
       <<",\"screen_creations\":"<<presentation.screen.creations
       <<",\"screen_pixels\":"<<majiro::android_host::pixel_stats(screen_frame())
       <<",\"movie_surface_active\":"<<(movie_surface_active?"true":"false")
       <<",\"movie_clear_background\":"<<(movie_clear_background?"true":"false")
       <<",\"movie_sequence\":"<<movie_sequence
       <<",\"movie_uploads\":"<<presentation.movie.uploads
       <<",\"movie_creations\":"<<presentation.movie.creations
       <<",\"movie_pixels\":"<<majiro::android_host::pixel_stats(movie_frame())
       <<",\"attempts\":"<<presentation.attempts<<",\"presented\":"<<presentation.presented
       <<",\"failures\":"<<presentation.failures
       <<",\"device_resets\":"<<presentation.device_resets
       <<",\"target_resets\":"<<presentation.target_resets
       <<",\"foregrounds\":"<<presentation.foregrounds
       <<",\"last_event\":"<<json_string(presentation.last_event)
       <<",\"last_error\":"<<json_string(presentation.last_error)<<"}";
    return out.str();
}

void install_pixels() {
    majiro::pixels::Host callbacks;
    callbacks.load = [](const std::string& name,bool alpha) { return load_image(name,alpha); };
    callbacks.glyphs = [](const std::string& face, int height, const std::vector<int>& steps, const std::string& message, bool boxed) {
        auto family = java_string(face), text = java_string(message);
        auto advances = env->NewIntArray(steps.size()); env->SetIntArrayRegion(advances, 0, steps.size(), steps.data());
        auto values = static_cast<jintArray>(env->CallStaticObjectMethod(bridge,
            method("glyphs", "(Ljava/lang/String;I[ILjava/lang/String;Z)[I"), family, height, advances, text, boxed));
        env->DeleteLocalRef(family); env->DeleteLocalRef(text); env->DeleteLocalRef(advances); checked_java();
        std::vector<jint> data(env->GetArrayLength(values));
        env->GetIntArrayRegion(values, 0, data.size(), data.data()); env->DeleteLocalRef(values); checked_java();
        need(data.size() >= 2 && size_t(data[0]) * data[1] == data.size() - 2, "glyph host result");
        majiro::pixels::GlyphRaster result; result.width = data[0]; result.height = data[1];
        result.alpha.assign(data.begin() + 2, data.end()); return result;
    };
    callbacks.present = [](int width, int height, const std::vector<uint8_t>& pixels) {
        majiro::android_host::validate_rgba(width,height,pixels);
        bool resized=width!=screen_width||height!=screen_height;
        screen_rgba=pixels;screen_width=width;screen_height=height;++screen_cpu_updates;
        presentation.screen.changed();
        if(resized)need(SDL_SetRenderLogicalPresentation(renderer,width,height,
            SDL_LOGICAL_PRESENTATION_LETTERBOX),SDL_GetError());
    };
    callbacks.png = [](const std::string& path, int w, int h, const std::vector<uint8_t>& pixels) {
        std::vector<jint> values(size_t(w) * h);
        for (size_t i = 0; i < values.size(); ++i)
            values[i] = jint(uint32_t(pixels[i * 4 + 3]) << 24 | uint32_t(pixels[i * 4]) << 16
                            | uint32_t(pixels[i * 4 + 1]) << 8 | pixels[i * 4 + 2]);
        auto name = java_string(path);
        auto array = env->NewIntArray(values.size());
        env->SetIntArrayRegion(array, 0, values.size(), values.data());
        env->CallStaticVoidMethod(bridge, method("savePng", "(Ljava/lang/String;II[I)V"), name, w, h, array);
        env->DeleteLocalRef(name); env->DeleteLocalRef(array); checked_java();
    };
    callbacks.platform = [](const std::string& request) {
        if (request.rfind("font_dialog", 0) == 0) return std::string("OK cancelled");
        if (request.rfind("fontfile ", 0) == 0) throw std::runtime_error("private font provider not configured");
        auto input = java_string(request);
        auto output = static_cast<jstring>(env->CallStaticObjectMethod(
            bridge, method("storage", "(Ljava/lang/String;)Ljava/lang/String;"), input));
        env->DeleteLocalRef(input); checked_java();
        auto result = native_string(output); env->DeleteLocalRef(output); return result;
    };
    majiro::pixels::install(std::move(callbacks));
}
void debug(const std::string& text) {
    __android_log_print(ANDROID_LOG_INFO, "MajiroPlayer", "%s", text.c_str());
    if(env&&bridge){
        auto message=java_string(text);
        env->CallStaticVoidMethod(bridge,method("debugLog","(Ljava/lang/String;)V"),message);
        env->DeleteLocalRef(message);checked_java();
    }
    auto value = java_string(text);
    env->CallStaticVoidMethod(bridge, method("debug", "(Ljava/lang/String;)V"), value);
    env->DeleteLocalRef(value); checked_java();
}
std::string json_string(const std::string& text) {
    std::string out = "\"";
    constexpr char hex[] = "0123456789abcdef";
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') { out += '\\'; out += char(c); }
        else if (c < 32) { out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; }
        else out += char(c);
    }
    return out + '"';
}
std::string json_value(int index, unsigned depth = 0) {
    need(depth < 32, "debug result nesting limit");
    index = index > 0 ? index : lua_gettop(lua) + index + 1;
    int type = lua_type(lua, index);
    if (type == LUA_TNIL) return "null";
    if (type == LUA_TBOOLEAN) return lua_toboolean(lua, index) ? "true" : "false";
    if (type == LUA_TNUMBER) { std::ostringstream out; out.precision(17); out << lua_tonumber(lua, index); return out.str(); }
    if (type == LUA_TSTRING) return json_string(argument(lua, index));
    need(type == LUA_TTABLE, "unsupported debug value");
    size_t length = lua_objlen(lua, index);
    bool array = length != 0;
    if (array) {
        lua_pushnil(lua);
        while (lua_next(lua, index)) {
            if (!lua_isnumber(lua, -2) || lua_tonumber(lua, -2) < 1 || lua_tonumber(lua, -2) > length)
                array = false;
            lua_pop(lua, 1);
        }
    }
    std::string out = array ? "[" : "{";
    bool first = true;
    if (array) {
        for (size_t i = 1; i <= length; ++i) {
            if (!first) out += ','; first = false;
            lua_rawgeti(lua, index, int(i)); out += json_value(-1, depth + 1); lua_pop(lua, 1);
        }
    } else {
        lua_pushnil(lua);
        while (lua_next(lua, index)) {
            if (!first) out += ','; first = false;
            std::string name;
            if (lua_type(lua, -2) == LUA_TNUMBER) name = std::to_string(static_cast<long long>(lua_tonumber(lua, -2)));
            else name = argument(lua, -2);
            out += json_string(name) + ':' + json_value(-1, depth + 1); lua_pop(lua, 1);
        }
    }
    return out + (array ? "]" : "}");
}
void start_debug_server() {
#ifdef MAJIRO_DEBUG_BRIDGE
    debug_socket = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    need(debug_socket >= 0, "debug socket unavailable");
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    const auto name=majiro::android_host::socket_name(application_id);
    need(name.size()+1<=sizeof(address.sun_path),"debug socket name too long");
    std::memcpy(address.sun_path+1,name.data(),name.size());
    if(bind(debug_socket,reinterpret_cast<sockaddr*>(&address),
            offsetof(sockaddr_un,sun_path)+1+name.size())!=0||listen(debug_socket,4)!=0){
        auto message=std::string("debug socket bind/listen failed: ")+name+": "+std::strerror(errno);
        close(debug_socket);debug_socket=-1;throw std::runtime_error(message);
    }
    debug("debug_socket="+name+" application_id="+application_id);
#endif
}
void debug_poll(float& x, float& y, int& buttons, std::vector<int>& pressed) {
    if (debug_socket < 0) return;
    while (true) {
        int client = accept4(debug_socket, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (client < 0) break;
        ucred peer{}; socklen_t length = sizeof(peer);
        if (getsockopt(client, SOL_SOCKET, SO_PEERCRED, &peer, &length) != 0
                || (peer.uid != 0 && peer.uid != 2000 && peer.uid != getuid())) { close(client); continue; }
        debug_clients.push_back({client, {}});
    }
    for (size_t i = 0; i < debug_clients.size();) {
        auto& client = debug_clients[i]; char buffer[512];
        auto got = recv(client.descriptor, buffer, sizeof(buffer), MSG_DONTWAIT);
        if (got > 0) client.command.append(buffer, static_cast<size_t>(got));
        if (got == 0 || client.command.size() > 4096) {
            close(client.descriptor); debug_clients.erase(debug_clients.begin() + i); continue;
        }
        auto end = client.command.find('\n');
        if (end == std::string::npos) { ++i; continue; }
        std::istringstream in(client.command.substr(0, end)); std::string command; in >> command;
        std::string response;
        if(command=="RENDER_STATS"){
            response=render_stats();
        } else if (command == "STATUS") {
            lua_getglobal(lua, "PLAYER_STATUS");
            if (lua_pcall(lua, 0, 1, 0)) throw std::runtime_error(lua_tostring(lua, -1));
            response = json_value(-1); lua_pop(lua, 1);
        } else if (command == "CLICK") {
            int bit; need(bool(in >> x >> y >> bit) && bit >= 1 && bit <= 3, "invalid debug input");
            buttons |= bit; response = "{\"accepted\":true}";
        } else if (command == "MOVE") {
            need(bool(in >> x >> y), "invalid debug pointer"); response = "{\"accepted\":true}";
        } else if (command == "KEY") {
            int code, down; need(bool(in >> code >> down), "invalid debug key");
            if (down && !key_down[code]) pressed.push_back(code);
            key_down[code] = down != 0; response = "{\"accepted\":true}";
        } else response = "{\"error\":\"unsupported debug command\"}";
        response += '\n';
        fcntl(client.descriptor, F_SETFL, fcntl(client.descriptor, F_GETFL, 0) & ~O_NONBLOCK);
        timeval timeout{2, 0};
        setsockopt(client.descriptor, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        size_t sent = 0;
        while (sent < response.size()) {
            auto count = send(client.descriptor, response.data() + sent, response.size() - sent, MSG_NOSIGNAL);
            if (count <= 0) break;
            sent += size_t(count);
        }
        close(client.descriptor); debug_clients.erase(debug_clients.begin() + i);
    }
}
}

extern "C" __attribute__((visibility("default"))) int SDL_main(int argc, char** argv) {
    SDL_Window* window = nullptr;
    try {
        env = static_cast<JNIEnv*>(SDL_GetAndroidJNIEnv());
        auto activity = SDL_GetAndroidActivity();
        auto activity_class = env->GetObjectClass(static_cast<jobject>(activity));
        auto package_method=env->GetMethodID(activity_class,"getPackageName","()Ljava/lang/String;");
        checked_java();
        auto package=static_cast<jstring>(env->CallObjectMethod(static_cast<jobject>(activity),package_method));
        checked_java();application_id=native_string(package);env->DeleteLocalRef(package);
        need(!application_id.empty(),"Android application id unavailable");
        auto loader_method = env->GetMethodID(activity_class, "getClassLoader", "()Ljava/lang/ClassLoader;");
        auto loader = env->CallObjectMethod(static_cast<jobject>(activity), loader_method);
        auto loader_class = env->FindClass("java/lang/ClassLoader");
        auto load_class = env->GetMethodID(loader_class, "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;");
        auto class_name = env->NewStringUTF("org.majiro.player.GameBridge");
        bridge = static_cast<jclass>(env->NewGlobalRef(env->CallObjectMethod(loader, load_class, class_name)));
        checked_java();
        env->DeleteLocalRef(class_name); env->DeleteLocalRef(loader_class); env->DeleteLocalRef(loader);
        env->DeleteLocalRef(activity_class); env->DeleteLocalRef(static_cast<jobject>(activity));
        root_uri = argc > 1 ? argv[1] : "";
        if(argc>2&&argv[2]&&argv[2][0])root_uri+="\n"+std::string(argv[2]);
        SDL_SetHint(SDL_HINT_ANDROID_ALLOW_RECREATE_ACTIVITY,"1");
        need(SDL_Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO), SDL_GetError());
        window = SDL_CreateWindow("majiro模拟器", 1280, 720, SDL_WINDOW_FULLSCREEN);
        need(window != nullptr, SDL_GetError());
        renderer = SDL_CreateRenderer(window, nullptr); need(renderer != nullptr, SDL_GetError());
        SDL_SetRenderVSync(renderer, 1);
        lua = luaL_newstate(); need(lua != nullptr, "Lua allocation failed"); luaL_openlibs(lua);
        initialize_sources(); install_pixels(); host_functions();
        began = SDL_GetTicks(); clock_adjust = 0;
        auto boot = java_read("runtime/player_boot.lua", true);
        execute(std::string(boot.begin(), boot.end()), "player_boot", 0);
        start_debug_server();
        lua_getglobal(lua, "PLAYER_SIZE"); need(lua_pcall(lua, 0, 2, 0) == 0, "game dimensions");
        int logical_w = int(lua_tonumber(lua, -2)), logical_h = int(lua_tonumber(lua, -1)); lua_pop(lua, 2);
        SDL_SetRenderLogicalPresentation(renderer, logical_w, logical_h, SDL_LOGICAL_PRESENTATION_LETTERBOX);
        bool quit = false, paused = false; float x = float(logical_w / 2), y = float(logical_h / 2);
        uint64_t paused_at = 0;
        int held = 0; uint64_t last_debug = 0;
        while (!quit) {
            SDL_Event event; int buttons = 0,input_activity=0;
            std::vector<int> pressed;
            while (SDL_PollEvent(&event)) {
                if(event.type==SDL_EVENT_KEY_DOWN||event.type==SDL_EVENT_MOUSE_BUTTON_DOWN||
                   event.type==SDL_EVENT_MOUSE_WHEEL)++input_activity;
                if(event.type==SDL_EVENT_RENDER_DEVICE_RESET||event.type==SDL_EVENT_RENDER_TARGETS_RESET){
                    if(!event.render.windowID||event.render.windowID==SDL_GetWindowID(window)){
                        presentation.event(event.type);debug("render_reset="+presentation.last_event);
                    }
                }else if(event.type==SDL_EVENT_RENDER_DEVICE_LOST){
                    if(!event.render.windowID||event.render.windowID==SDL_GetWindowID(window))
                        throw std::runtime_error("SDL renderer device permanently lost; restart this game session");
                }else if(event.type==SDL_EVENT_WINDOW_EXPOSED||event.type==SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED){
                    if(event.window.windowID==SDL_GetWindowID(window))presentation.reupload();
                }
                SDL_ConvertEventToRenderCoordinates(renderer, &event);
                if (event.type == SDL_EVENT_QUIT) quit = true;
                else if (event.type == SDL_EVENT_WILL_ENTER_BACKGROUND) {
                    if (!paused) paused_at = SDL_GetTicks();
                    paused = true; render_paused=true; held = 0;
                } else if (event.type == SDL_EVENT_DID_ENTER_FOREGROUND) {
                    if (paused) began += SDL_GetTicks() - paused_at;
                    paused = false;render_paused=false;
                    presentation.event(SDL_EVENT_DID_ENTER_FOREGROUND);
                }
                else if (event.type == SDL_EVENT_MOUSE_MOTION) { x = event.motion.x; y = event.motion.y; }
                else if (event.type == SDL_EVENT_MOUSE_BUTTON_DOWN) {
                    x = event.button.x; y = event.button.y;
                    int bit = event.button.button == SDL_BUTTON_RIGHT ? 2 : 1;
                    buttons |= bit; held |= bit;
                } else if (event.type == SDL_EVENT_MOUSE_BUTTON_UP) {
                    int bit = event.button.button == SDL_BUTTON_RIGHT ? 2 : 1; held &= ~bit;
                } else if (event.type == SDL_EVENT_KEY_DOWN || event.type == SDL_EVENT_KEY_UP) {
                    bool down=event.type==SDL_EVENT_KEY_DOWN;
                    int code=0;
                    auto key=event.key.key;
                    if(key==SDLK_AC_BACK&&down)buttons|=2;
                    else if(key>='a'&&key<='z')code=int(key-'a'+'A');
                    else if(key>='0'&&key<='9')code=int(key);
                    else switch(key){
                        case SDLK_RETURN:code=13;break;case SDLK_ESCAPE:code=27;break;
                        case SDLK_SPACE:code=32;break;case SDLK_BACKSPACE:code=8;break;
                        case SDLK_TAB:code=9;break;case SDLK_UP:code=38;break;case SDLK_DOWN:code=40;break;
                        case SDLK_LEFT:code=37;break;case SDLK_RIGHT:code=39;break;
                        case SDLK_PAGEUP:code=33;break;case SDLK_PAGEDOWN:code=34;break;
                        case SDLK_LCTRL:case SDLK_RCTRL:code=17;break;
                        case SDLK_LSHIFT:case SDLK_RSHIFT:code=16;break;
                        case SDLK_LALT:case SDLK_RALT:code=18;break;
                        case SDLK_CAPSLOCK:code=20;break;
                        default:break;
                    }
                    if(code){if(down&&!key_down[code])pressed.push_back(code);key_down[code]=down;}
                }
            }
            if(quit)break;
            // Keep pending touchscreen actions queued until the game can poll.
            int window_command=0;
            if(!paused){
                int requested=env->CallStaticIntMethod(bridge,method("consumeGameButtons","()I"));
                checked_java();
                window_command=env->CallStaticIntMethod(bridge,method("consumeWindowMenu","()I"));
                checked_java();
                buttons|=requested;
                int keys=env->CallStaticIntMethod(bridge,method("consumeGameKeys","()I"));
                checked_java();
                const int virtual_keys[]={13,27,37,38,39,40,33,34};
                for(int index=0;index<8;++index)
                    if(keys&(1<<index))pressed.push_back(virtual_keys[index]);
                if(requested||keys||window_command)++input_activity;
            }
            if(cursor_warp_pending){x=cursor_warp_x;y=cursor_warp_y;cursor_warp_pending=false;}
            debug_poll(x, y, buttons, pressed);
            if (paused) { SDL_Delay(20); continue; }
            auto media_error = static_cast<jstring>(env->CallStaticObjectMethod(
                bridge, method("mediaError", "()Ljava/lang/String;"))); checked_java();
            if (media_error) {
                auto error = native_string(media_error); env->DeleteLocalRef(media_error); throw std::runtime_error(error);
            }
            lua_getglobal(lua, "PLAYER_TICK");
            lua_pushnumber(lua, double(SDL_GetTicks() - began + clock_adjust));
            lua_newtable(lua); number_field(lua, "x", x); number_field(lua, "y", y);
            number_field(lua, "buttons", buttons); number_field(lua, "held_buttons", held);
            number_field(lua,"input_activity",input_activity);
            number_field(lua,"window_command",window_command);
            lua_newtable(lua);
            for (const auto& row : key_down) {
                lua_pushboolean(lua, row.second); lua_rawseti(lua, -2, row.first);
            }
            lua_setfield(lua, -2, "down");
            lua_newtable(lua);
            for (auto code : pressed) { lua_pushboolean(lua, true); lua_rawseti(lua, -2, code); }
            lua_setfield(lua, -2, "pressed");
            if (lua_pcall(lua, 2, 4, 0)) throw std::runtime_error(lua_tostring(lua, -1));
            const char* status=lua_tostring(lua,-4);
            if(status&&std::strcmp(status,"finished")==0){
                debug("game finished; returning to library");
                lua_pop(lua,4);quit=true;break;
            }
            if (SDL_GetTicks() - last_debug > 1500) {
                std::ostringstream message;
                message << "status=" << lua_tostring(lua, -4) << " steps=" << lua_tonumber(lua, -3)
                        << " pages=" << lua_tonumber(lua, -2) << " script=" << lua_tostring(lua, -1);
                debug(message.str()+" render="+render_stats()); last_debug = SDL_GetTicks();
            }
            lua_pop(lua, 4);
            if(window_command){
                lua_getglobal(lua,"PLAYER_WINDOW_MENU_RESULT");
                if(lua_pcall(lua,0,1,0))throw std::runtime_error(lua_tostring(lua,-1));
                auto result=env->NewStringUTF(lua_tostring(lua,-1));
                env->CallStaticVoidMethod(bridge,method("reportWindowMenuResult","(Ljava/lang/String;)V"),result);
                env->DeleteLocalRef(result);checked_java();lua_pop(lua,1);
            }
            refresh_movie_frame();
            if(!presentation.draw(renderer,screen_frame(),movie_frame(),
                    !movie_surface_active||!movie_clear_background,movie_surface_active,movie_rect)){
                auto now=SDL_GetTicks();
                if(presentation.failures==1||now-last_render_error_log>=2000){
                    debug("render_failure="+presentation.last_error);
                    last_render_error_log=now;
                }
            }
            SDL_Delay(1);
        }
    } catch (const std::exception& error) {
        std::string message = error.what();
        __android_log_print(ANDROID_LOG_ERROR, "MajiroPlayer", "%s", message.c_str());
        try { if (env && bridge) debug("ERROR: " + message); } catch (...) {}
        SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, "majiro模拟器运行已停止", message.c_str(), window);
    }
    if(lua){
        lua_getglobal(lua,"PLAYER_SHUTDOWN");
        if(lua_isfunction(lua,-1)){
            if(lua_pcall(lua,0,0,0)!=0){
                const char* failure=lua_tostring(lua,-1);
                try{debug(std::string("exit persistence failed: ")+(failure?failure:"unknown"));}catch(...){}
                lua_pop(lua,1);
            }
        }else lua_pop(lua,1);
        lua_close(lua);
    }
    lua = nullptr;
    majiro::pixels::install({});
    for (const auto& source : sources) close(source.descriptor);
    sources.clear();source_order.clear(); members.clear();loose_names.clear(); image_cache.clear();png_transparency_cache.clear(); audio_cache.clear();
    password.clear();has_password=false;key_down.clear();cursor_warp_pending=false;
    for (auto& client : debug_clients) close(client.descriptor);
    debug_clients.clear();
    if (debug_socket >= 0) close(debug_socket); debug_socket = -1;
    presentation.clear();screen_rgba.clear();screen_rgba.shrink_to_fit();
    screen_cpu_updates=last_render_error_log=0;render_paused=false;application_id.clear();
    movie_rgba={};movie_page=0;movie_sequence=-1;
    movie_texture_mode=false;movie_surface_active=false;movie_clear_background=false;
    screen_width=screen_height=0;root_uri.clear();
    translation_mode="original";
    patch_layers.clear();
    if(script_cursor)SDL_DestroyCursor(script_cursor);script_cursor=nullptr;
    if (renderer) SDL_DestroyRenderer(renderer); renderer = nullptr;
    if (window) SDL_DestroyWindow(window);
    SDL_Quit();
    if(env&&bridge)env->DeleteGlobalRef(bridge);
    bridge=nullptr;env=nullptr;
    return 0;
}
