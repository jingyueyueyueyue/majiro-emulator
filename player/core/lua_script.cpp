#include "lua_script.hpp"
#include "majiro_core.hpp"
#include "majiro_opcodes.hpp"
extern "C" {
#include "lua.h"
}
#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <set>
#include <stdexcept>

namespace majiro {
namespace {
uint16_t u16(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(p[1]) << 8; }
uint32_t u32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
void number(lua_State* lua, const char* field, double value) {
    lua_pushnumber(lua, value); lua_setfield(lua, -2, field);
}
void text(lua_State* lua, const char* field, const std::string& value) {
    lua_pushlstring(lua, value.data(), value.size()); lua_setfield(lua, -2, field);
}
void crypt(std::vector<uint8_t>& code) {
    static const auto key = [] {
        std::array<uint8_t, 1024> bytes{};
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t value = i;
            for (unsigned n = 0; n < 8; ++n) value = (value >> 1) ^ ((value & 1) ? 0xEDB88320u : 0);
            for (unsigned n = 0; n < 4; ++n) bytes[i * 4 + n] = uint8_t(value >> (n * 8));
        }
        return bytes;
    }();
    for (size_t i = 0; i < code.size(); ++i) code[i] ^= key[i & 1023];
}
}
std::vector<uint8_t> repair_script_jumps(const std::vector<uint8_t>& data,
                                       const std::vector<ScriptRelocation>& repairs) {
    auto require = [](bool ok, const char* message) { if (!ok) throw std::runtime_error(message); };
    require(data.size() >= 32, "truncated patch MJO header");
    bool encrypted = std::memcmp(data.data(), "MajiroObjX1.000", 16) == 0;
    require(encrypted || std::memcmp(data.data(), "MajiroObjV1.000", 16) == 0
                      || std::memcmp(data.data(), "MjPlainBytecode", 16) == 0,
            "unsupported patch MJO signature");
    const uint64_t code_at = 32ull + uint64_t(u32(data.data() + 24)) * 8;
    require(code_at <= data.size(), "truncated patch MJO function table");
    const uint32_t size = u32(data.data() + code_at - 4);
    require(code_at + size == data.size(), "invalid patch MJO code size");
    require(repairs.size() <= 4096, "patch jump repair count limit");
    std::vector<uint8_t> code(data.begin() + size_t(code_at), data.end());
    if (encrypted) crypt(code);
    std::set<uint32_t> boundaries;
    size_t cursor=0;
    auto skip=[&](size_t count){
        require(cursor<=code.size()&&count<=code.size()-cursor,"truncated patch MJO instruction");
        cursor+=count;
    };
    auto word=[&](){skip(2);return u16(code.data()+cursor-2);};
    while(cursor<code.size()){
        boundaries.insert(uint32_t(cursor));
        uint16_t opcode=word();
        auto definition=std::find_if(kOpcodes.begin(),kOpcodes.end(),
            [opcode](const auto& item){return item.code==opcode;});
        require(definition!=kOpcodes.end(),"unknown patch MJO opcode");
        for(const char* field=definition->encoding;*field;++field){
            if(*field=='s'||*field=='t'){auto count=word();skip(count);}
            else if(*field=='c'){auto count=word();skip(size_t(count)*4);}
            else if(*field=='i'||*field=='h'||*field=='r'||*field=='j'||*field=='0')skip(4);
            else skip(2);
        }
    }
    std::set<uint32_t> used;
    for (const auto& repair : repairs) {
        require(used.insert(repair.offset).second, "duplicate patch jump repair");
        require(boundaries.count(repair.offset)!=0,"patch jump repair is not an instruction boundary");
        require(repair.offset <= code.size() && code.size() - repair.offset >= 6,
                "patch jump repair outside bytecode");
        auto p = code.data() + repair.offset;
        require(u16(p) == 0x82c, "patch jump opcode anchor changed");
        const int64_t next = int64_t(repair.offset) + 6;
        require(next + static_cast<int32_t>(u32(p + 2)) == repair.old_target,
                "patch jump target anchor changed");
        const int64_t relative = int64_t(repair.new_target) - next;
        require(repair.new_target < code.size() && relative >= std::numeric_limits<int32_t>::min()
                    && relative <= std::numeric_limits<int32_t>::max(), "invalid repaired jump target");
        for (unsigned n = 0; n < 4; ++n) p[2 + n] = uint8_t(uint32_t(relative) >> (8 * n));
    }
    if (encrypted) crypt(code);
    std::vector<uint8_t> result(data.begin(), data.begin() + size_t(code_at));
    result.insert(result.end(), code.begin(), code.end());
    // Strict parsing also checks that repaired targets are real instruction boundaries.
    read_script(result);
    return result;
}
void push_script(lua_State* lua, const std::string& name, const std::vector<uint8_t>& data,
                 const std::string& sha256,
                 const std::function<std::string(const std::vector<uint8_t>&)>& decode) {
    auto checked = read_script(data);
    size_t code_at = 32 + size_t(checked.functions) * 8;
    std::vector<uint8_t> code(data.begin() + code_at, data.end());
    if (std::memcmp(data.data(), "MajiroObjX1.000", 16) == 0) crypt(code);
    lua_newtable(lua);
    number(lua, "schema", 1); text(lua, "name", name); text(lua, "source_sha256", sha256);
    number(lua, "main", checked.main); number(lua, "line_count", u32(data.data() + 20));
    number(lua, "code_size", code.size());
    lua_newtable(lua);
    for (uint32_t i = 0; i < checked.functions; ++i) {
        lua_newtable(lua);
        lua_pushnumber(lua, u32(data.data() + 28 + i * 8)); lua_rawseti(lua, -2, 1);
        lua_pushnumber(lua, u32(data.data() + 32 + i * 8)); lua_rawseti(lua, -2, 2);
        lua_rawseti(lua, -2, int(i + 1));
    }
    lua_setfield(lua, -2, "functions");
    lua_newtable(lua);
    size_t cursor = 0;
    int instruction = 1;
    while (cursor < code.size()) {
        size_t offset = cursor;
        uint16_t opcode = u16(code.data() + cursor); cursor += 2;
        auto definition = std::find_if(kOpcodes.begin(), kOpcodes.end(),
                                      [opcode](const auto& item) { return item.code == opcode; });
        if (definition == kOpcodes.end()) throw std::runtime_error("missing opcode definition after validation");
        lua_newtable(lua);
        lua_pushnumber(lua, offset); lua_rawseti(lua, -2, 1);
        lua_pushnumber(lua, opcode); lua_rawseti(lua, -2, 2);
        int operand = 3;
        for (const char* field = definition->encoding; *field; ++field, ++operand) {
            if (*field == 's' || *field == 't') {
                uint16_t length = u16(code.data() + cursor); cursor += 2;
                if (*field == 's') {
                    auto value = decode(std::vector<uint8_t>(code.begin() + cursor,
                                                             code.begin() + cursor + length - 1));
                    lua_pushlstring(lua, value.data(), value.size());
                } else {
                    lua_newtable(lua);
                    for (uint32_t i = 0; i < length; ++i) {
                        lua_pushnumber(lua, code[cursor + i]); lua_rawseti(lua, -2, int(i + 1));
                    }
                }
                cursor += length;
            } else if (*field == 'c') {
                uint16_t count = u16(code.data() + cursor); cursor += 2;
                lua_newtable(lua);
                for (uint32_t i = 0; i < count; ++i) {
                    int32_t relative = static_cast<int32_t>(u32(code.data() + cursor)); cursor += 4;
                    lua_pushnumber(lua, double(int64_t(cursor) + relative));
                    lua_rawseti(lua, -2, int(i + 1));
                }
            } else if (*field == 'r') {
                uint32_t bits = u32(code.data() + cursor); cursor += 4;
                float value; std::memcpy(&value, &bits, 4); lua_pushnumber(lua, value);
            } else if (*field == 'j') {
                int32_t relative = static_cast<int32_t>(u32(code.data() + cursor)); cursor += 4;
                lua_pushnumber(lua, double(int64_t(cursor) + relative));
            } else if (*field == 'i') {
                lua_pushnumber(lua, static_cast<int32_t>(u32(code.data() + cursor))); cursor += 4;
            } else if (*field == 'h' || *field == '0') {
                lua_pushnumber(lua, u32(code.data() + cursor)); cursor += 4;
            } else if (*field == 'o') {
                lua_pushnumber(lua, static_cast<int16_t>(u16(code.data() + cursor))); cursor += 2;
            } else {
                lua_pushnumber(lua, u16(code.data() + cursor)); cursor += 2;
            }
            lua_rawseti(lua, -2, operand);
        }
        lua_rawseti(lua, -2, instruction++);
    }
    lua_setfield(lua, -2, "code");
}
}
