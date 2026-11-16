#pragma once
#include <algorithm>
#include <cstdint>
#include <utility>
#include <vector>

namespace majiro {
struct TextShadow { int x,y; uint32_t color; };
// The high byte selects an effect, not a dilation radius (original 0x414301).
inline std::vector<TextShadow> text_shadow(int height,uint32_t value){
    if(value==0xffffffffu)return {};
    const unsigned mode=(value>>24)&127;
    const uint32_t color=value&0xffffff;
    const int x=std::max(1,height/9),r=std::max(1,height/18);
    std::vector<TextShadow> result;
    auto outline=[&]{
        for(const auto& at:{std::pair<int,int>{r,r},{-r,r},{r,-r},{-r,-r},
                           {r,0},{-r,0},{0,-r},{0,r}})
            result.push_back({at.first,at.second,color});
    };
    auto dropped=[&](uint32_t rgb){
        result.push_back({2*r,2*r,rgb});
        result.push_back({2*r,r,rgb});
    };
    if(mode==0)result.push_back({x,r,color});
    else if(mode==1)outline();
    else if(mode==2){outline();dropped(color);}
    else if(mode==3){dropped(color|0x808080);outline();}
    return result;
}
}
