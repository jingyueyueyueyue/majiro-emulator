#include "image_resource.hpp"
#include "majiro_image.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <set>
#include <stdexcept>

int main(int argc,char** argv) {
    int checks=0;
    auto check=[&](bool value){++checks;if(!value)throw std::runtime_error("PNG resource regression");};
    std::set<std::string> files={"folder/image.png","real_.rc8","real.png","rgb.png",
        "fallback.rct","priority.png","priority.rct","bmp-first.bmp","bmp-first.rct"};
    auto exists=[&](const std::string& value){return files.count(value);};
    auto direct=majiro::resolve_image_resource("FOLDER\\IMAGE",exists);
    check(majiro::resource_key("\xef\xbc\xa1" "BC\\\xef\xbc\xba" ".MJO")==
          "\xef\xbd\x81" "bc/\xef\xbd\x9a" ".mjo");
    for(const auto& name:{"picture.png","folder\\PIC.RCT","a.BMP","mask.rc8"})
        check(majiro::image_archive_member(name));
    for(const auto& name:{"scenario.mjo","majiro.env","data.txt","bgm.ogg","a.png.mjo","mask.rc8.exe"})
        check(!majiro::image_archive_member(name));
    check(direct.name=="folder/image.png"&&!direct.derived_mask());
    for(const auto& name:{"folder/image_","FOLDER/IMAGE_.RC8","folder/image_.png"}) {
        const auto mask=majiro::resolve_image_resource(name,exists);
        check(mask.name=="folder/image_.rc8"&&mask.alpha_source=="folder/image.png");
    }
    auto real=majiro::resolve_image_resource("real_",exists);
    check(real.name=="real_.rc8"&&!real.derived_mask());
    check(majiro::resolve_image_resource("fallback.png",exists).name=="fallback.rct");
    check(majiro::resolve_image_resource("priority.rct",exists).name=="priority.png");
    check(majiro::resolve_image_resource("bmp-first.png",exists).name=="bmp-first.bmp");
    bool failed=false;try{majiro::resolve_image_resource("missing_",exists);}catch(const std::exception&){failed=true;}
    check(failed);
    std::vector<uint8_t> png={137,80,78,71,13,10,26,10,0,0,0,13,'I','H','D','R',
        0,0,0,1,0,0,0,1,8,6,0,0,0,0,0,0,0};
    check(majiro::png_has_transparency(png));
    png[25]=4;check(majiro::png_has_transparency(png));
    png[25]=2;
    png.insert(png.end(),{0,0,0,0,'I','D','A','T',0,0,0,0});
    check(!majiro::png_has_transparency(png));
    png.resize(33);png.insert(png.end(),{0,0,0,2,'t','R','N','S',0,0,0,0,0,0});
    check(majiro::png_has_transparency(png));
    failed=false;try{majiro::png_has_transparency({1,2,3});}catch(const std::exception&){failed=true;}check(failed);
    majiro::Image color;color.width=6;color.height=1;
    for(unsigned alpha:{0u,1u,127u,128u,254u,255u})
        color.rgba.insert(color.rgba.end(),{73,104,11,uint8_t(alpha)});
    auto mask=majiro::transparency_mask(color);
    check(mask.indices==std::vector<uint8_t>({255,254,128,127,1,0}));
    check(mask.width==6&&mask.height==1&&mask.rgba.size()==24);
    auto restored=color;majiro::apply_mask(restored,mask);check(restored.rgba==color.rgba);
    for(size_t i=0;i<256;++i)check(mask.palette[i*3]==i&&mask.palette[i*3+1]==i&&mask.palette[i*3+2]==i);
    if(argc==2) {
        std::ifstream input(argv[1],std::ios::binary);
        std::vector<uint8_t> raw{std::istreambuf_iterator<char>(input),std::istreambuf_iterator<char>()};
        check(raw.size()==150u*260*4);
        color.width=150;color.height=260;color.rgba=raw;
        mask=majiro::transparency_mask(color);
        for(size_t i=0;i<mask.indices.size();++i)check(mask.indices[i]==255-color.rgba[i*4+3]);
        restored=color;majiro::apply_mask(restored,mask);check(restored.rgba==color.rgba);
    }
    std::cout<<"{\"status\":\"passed\",\"checks\":"<<checks<<"}\n";
}
