#pragma once
#include <algorithm>
#include <cstdint>
extern "C" {
#include "lua.h"
}

namespace majiro::android_host {

// The SDL owner repeatedly enters Lua from C, including large native-produced
// script tables and transient VM frames. Give collection an explicit tick budget.
class LuaMemory {
    int live_kib_ = 0;
    uint64_t last_collection_ = 0;
public:
    uint64_t collections = 0;
    void configure(lua_State* state) {
        lua_gc(state, LUA_GCSETPAUSE, 110);
        lua_gc(state, LUA_GCSETSTEPMUL, 400);
    }
    void settled(lua_State* state, uint64_t now) {
        lua_gc(state, LUA_GCCOLLECT, 0);
        live_kib_ = lua_gc(state, LUA_GCCOUNT, 0);
        last_collection_ = now;
        ++collections;
    }
    void tick(lua_State* state, uint64_t now) {
        lua_gc(state, LUA_GCSTEP, 128);
        const int threshold = std::max(128 * 1024, live_kib_ + live_kib_ / 2);
        if (lua_gc(state, LUA_GCCOUNT, 0) > threshold && now - last_collection_ >= 2000)
            settled(state, now);
    }
};

}
