#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>
struct lua_State;
namespace majiro {
struct ScriptRelocation {
    uint32_t offset, old_target, new_target;
};
// The caller must first verify the original bytes against pinned patch metadata.
// Every instruction anchor is checked and the result is validated before return.
std::vector<uint8_t> repair_script_jumps(const std::vector<uint8_t>& bytes,
                                       const std::vector<ScriptRelocation>& repairs);
void push_script(lua_State* lua, const std::string& name, const std::vector<uint8_t>& bytes,
                 const std::string& sha256,
                 const std::function<std::string(const std::vector<uint8_t>&)>& decode);
}
