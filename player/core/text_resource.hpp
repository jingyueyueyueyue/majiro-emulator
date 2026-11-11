#pragma once
#include <string>
#include <stdexcept>
#include <map>
#include <vector>
#include <cstdint>

namespace majiro {
// A decoding decision is attached to the byte owner, never inferred from bytes.
struct TextSource {
    std::string scope="original";
    std::string path;
    bool raw_patch() const { return scope=="pinned_raw_patch"; }
    const char* charset() const {
        if(scope=="original"||scope=="source_mjo"||scope=="cp932")return "windows-31j";
        if(scope=="cp936")return "GBK";
        if(scope=="utf-8")return "UTF-8";
        throw std::runtime_error("text resource has no declared decoder: "+path);
    }
};
inline bool text_resource_member(const std::string& name){
    return name.ends_with(".mjo")||name.ends_with(".cfg")||
        name.ends_with(".env")||name.ends_with(".txt");
}
inline std::string text_resource_stamp(const TextSource& source,const std::string& hash){
    return source.scope+"\n"+source.path+"\n"+hash;
}
class TextSnapshotCache {
    struct Item { TextSource source; std::vector<uint8_t> bytes; };
    std::map<std::string,Item> entries;
    size_t used=0;
public:
    size_t limit=16u*1024*1024;
    void clear(){entries.clear();used=0;}
    size_t bytes() const{return used;}
    bool get(const std::string& name,std::vector<uint8_t>& bytes,TextSource& source) const{
        auto found=entries.find(name);
        if(found==entries.end())return false;
        bytes=found->second.bytes;source=found->second.source;return true;
    }
    void put(const std::string& name,const std::vector<uint8_t>& bytes,const TextSource& source){
        if(entries.count(name)||bytes.size()>limit-used)return;
        entries.emplace(name,Item{source,bytes});used+=bytes.size();
    }
};
}
