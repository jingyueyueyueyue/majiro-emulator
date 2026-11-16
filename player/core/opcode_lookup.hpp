#pragma once
#include "majiro_opcodes.hpp"
#include <algorithm>

namespace majiro {
// Every generated table is ordered by the unsigned opcode value.
static_assert([]{
    for(size_t i=1;i<kOpcodes.size();++i)
        if(kOpcodes[i-1].code>=kOpcodes[i].code)return false;
    return true;
}(),"generated opcodes must be unique and sorted");
inline auto opcode_definition(uint16_t code) {
    auto found=std::lower_bound(kOpcodes.begin(),kOpcodes.end(),code,
        [](const auto& definition,uint16_t value){return definition.code<value;});
    return found!=kOpcodes.end()&&found->code==code?found:kOpcodes.end();
}
}
