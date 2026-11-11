// Shared image/portable boundary checks; compiled only in final validation.
#include "majiro_image.hpp"
#include "image_resource.hpp"
#include "portable_pixels.hpp"
#include <iostream>
#include <stdexcept>
#include <string>

static void need(bool value,const char* message) {
    if(!value)throw std::runtime_error(message);
}
template<class F> static void rejects(F function) {
    bool rejected=false;
    try { function(); } catch(const std::exception&) { rejected=true; }
    need(rejected,"invalid buffer was accepted");
}
int main() {
    majiro::Image good;good.width=good.height=1;good.rgba={10,20,30,255};
    auto bad=good;bad.rgba.clear();
    rejects([&]{majiro::combine_base(good,bad);});
    rejects([&]{majiro::apply_mask(good,bad);});
    rejects([&]{majiro::apply_mask(bad,good);});
    auto mask=good;mask.indices={128};mask.rgba={255,7,12,255};
    majiro::apply_mask(good,mask);
    need(good.rgba[3]==127,"mask palette changed its raw transparency");
    majiro::pixels::Host host;
    host.load=[&](const std::string& name,bool){
        if(name=="short")return bad;
        auto image=good;
        if(name=="long_indices")image.indices={0,1};
        return image;
    };
    host.glyphs=[](const auto&,int,const auto&,const auto&,bool){
        majiro::pixels::GlyphRaster raster;
        raster.width=raster.height=2;raster.alpha={1};return raster;
    };
    majiro::pixels::install(host);
    for(const auto& command:{"load 1 0 short","load8 1 0 long_indices","load8 1 0 missing_indices"})
        need(majiro::pixels::command(command).starts_with("ERR"),"invalid host image was accepted");
    need(majiro::pixels::command("stats")=="OK 0 0","invalid host image left a page");
    need(majiro::pixels::command("create 1 2 2 0")=="OK","page creation");
    need(majiro::pixels::command("glyphs 1 0 0 20 10 0 0\nfont\n10\nA").starts_with("ERR"),
         "invalid host glyph raster was accepted");
    std::cout<<"{\"status\":\"passed\",\"image_contract_cases\":6,\"glyph_contract_cases\":1}\n";
}
