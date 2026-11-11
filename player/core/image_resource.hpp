#pragma once
#include "majiro_image.hpp"
#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <string>
#include <vector>
#include <cstdint>

namespace majiro {
struct ImageResource {
    std::string name;
    std::string alpha_source;
    bool missing_page = false;
    bool derived_mask() const { return !alpha_source.empty(); }
};
struct MissingImageResource : std::runtime_error {
    explicit MissingImageResource(const std::string& name)
        : std::runtime_error("resource missing: "+name) {}
};
inline std::string resource_key(std::string value) {
    for(auto& c:value) {
        if(c>='A'&&c<='Z')c=char(c-'A'+'a');
        if(c=='\\')c='/';
    }
    for(size_t i=0;i+2<value.size();++i){
        if(uint8_t(value[i])==0xef&&uint8_t(value[i+1])==0xbc&&
           uint8_t(value[i+2])>=0xa1&&uint8_t(value[i+2])<=0xba){
            value[i+1]=char(0xbd);value[i+2]=char(uint8_t(value[i+2])-0x20);
            i+=2;
        }
    }
    return value;
}
inline bool image_archive_member(const std::string& name){
    const auto normalized=resource_key(name);
    return normalized.ends_with(".png")||normalized.ends_with(".bmp")||
           normalized.ends_with(".rct")||normalized.ends_with(".rc8");
}
template<class Exists>
ImageResource resolve_image_resource(const std::string& requested,Exists exists) {
    const auto name=resource_key(requested);
    std::string stem=name;
    for(const auto& extension:{".png",".bmp",".rct",".rc8"})
        if(stem.ends_with(extension)){stem.resize(stem.size()-4);break;}
    // The image API receives a logical stem, including when the script
    // supplies a different image suffix. Original lookup prefers PNG/BMP.
    for(const auto& candidate:std::vector<std::string>{stem+".png",stem+".bmp",stem+".rct",stem+".rc8",name})
        if(exists(candidate))return{candidate,{}};
    // Majiro exposes a PNG transparency channel as an indexed "name_" page,
    // including the conventional explicit RC8 alias. Real files win above.
    if(stem.ends_with("_")) {
        stem.pop_back();
        const auto png=stem+".png";
        if(exists(png))return{stem+"_.rc8",png};
    }
    throw MissingImageResource(requested);
}
template<class Exists>
ImageResource resolve_page_image_resource(const std::string& requested,Exists exists) {
    try { return resolve_image_resource(requested,exists); }
    catch(const MissingImageResource&) {
        // Original page_create_file returns an empty 1x1 page on a missing
        // optional image. A trailing underscore requests an indexed page.
        auto name=resource_key(requested);
        for(const auto& extension:{".png",".bmp",".rct",".rc8"})
            if(name.ends_with(extension)){name.resize(name.size()-4);break;}
        return{name+(name.ends_with("_")?".rc8":".rct"),{},true};
    }
}
inline Image missing_page_image(const ImageResource& resource) {
    Image image;image.width=image.height=1;image.rgba={0,0,0,255};
    if(resource.name.ends_with(".rc8")){
        image.indices={0};
        for(size_t i=0;i<256;++i)image.palette[i*3]=image.palette[i*3+1]=image.palette[i*3+2]=uint8_t(i);
    }
    return image;
}
template<class Exists,class Load>
Image image_for_page(Image image,const std::string& name,bool alpha,Exists exists,Load load) {
    if(alpha&&name.ends_with(".rct")){
        const auto mask=name.substr(0,name.size()-4)+"_.rc8";
        if(exists(mask))apply_mask(image,load(mask));
    }
    return image;
}
inline bool png_has_transparency(const std::vector<uint8_t>& bytes) {
    const std::vector<uint8_t> signature={137,80,78,71,13,10,26,10};
    if(bytes.size()<29||!std::equal(signature.begin(),signature.end(),bytes.begin()))
        throw std::runtime_error("invalid PNG transparency source");
    if(bytes[25]==4||bytes[25]==6)return true;
    size_t at=8;
    while(at+12<=bytes.size()) {
        const uint32_t length=uint32_t(bytes[at])<<24|uint32_t(bytes[at+1])<<16|
            uint32_t(bytes[at+2])<<8|uint32_t(bytes[at+3]);
        const std::string kind(reinterpret_cast<const char*>(bytes.data()+at+4),4);
        if(kind=="IDAT"||kind=="IEND")return false;
        if(length>bytes.size()-at-12)throw std::runtime_error("truncated PNG transparency chunk");
        if(kind=="tRNS")return length>0;
        at+=size_t(length)+12;
    }
    throw std::runtime_error("PNG transparency metadata incomplete");
}
}
