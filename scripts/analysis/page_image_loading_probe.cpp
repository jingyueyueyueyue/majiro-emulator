#include "image_resource.hpp"
#include "portable_pixels.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <set>
#include <stdexcept>

static std::vector<uint8_t> read(const std::string& path){
    std::ifstream input(path,std::ios::binary);
    if(!input)throw std::runtime_error("image fixture absent");
    return {std::istreambuf_iterator<char>(input),std::istreambuf_iterator<char>()};
}
int main(){
    int checks=0,mask_reads=0;
    auto check=[&](bool value){++checks;if(!value)throw std::runtime_error("page image regression");};
    const auto raw=majiro::read_image(read("color.rct"));
    const auto mask=majiro::read_image(read("mask.rc8"));
    check(raw.width==150&&raw.height==300&&mask.width==150&&mask.height==200);
    std::set<std::string> files={"button.rct","button_.rc8","equal.rct","equal_.rc8"};
    auto exists=[&](const auto& name){return files.count(name);};
    auto load=[&](const std::string& name,bool alpha){
        const auto resource=majiro::resolve_page_image_resource(name,exists);
        if(resource.missing_page)return majiro::missing_page_image(resource);
        auto color=resource.name.ends_with(".rc8")?mask:raw;
        if(resource.name=="equal.rct")color.height=200,color.rgba.resize(150*200*4);
        return majiro::image_for_page(std::move(color),resource.name,alpha,exists,[&](const auto&){
            ++mask_reads;return mask;
        });
    };
    auto plain=load("button",false);
    check(plain.rgba==raw.rgba&&mask_reads==0);
    auto indexed=load("button_",false);
    check(indexed.indices==mask.indices&&indexed.height==200&&mask_reads==0);
    auto alpha=load("equal",true);
    check(mask_reads==1);
    for(size_t i=0;i<mask.indices.size();++i)check(alpha.rgba[i*4+3]==255-mask.rgba[i*4]);
    const auto missing=load("absent_on",false),missing_mask=load("absent_",false);
    check(missing.width==1&&missing.height==1&&missing.rgba.size()==4&&missing.indices.empty());
    check(missing_mask.width==1&&missing_mask.height==1&&missing_mask.indices.size()==1);
    majiro::pixels::Host host;
    host.load=load;
    majiro::pixels::install(std::move(host));
    check(majiro::pixels::command("load 1 0 button")=="OK");
    check(majiro::pixels::command("load8 2 0 button_")=="OK");
    check(majiro::pixels::command("load 3 1 equal")=="OK");
    check(majiro::pixels::command("alpha 3 4 1")=="OK");
    check(majiro::pixels::command("load 5 0 absent_on")=="OK");
    check(majiro::pixels::command("load8 6 0 absent_")=="OK");
    check(majiro::pixels::command("pixel 1 0 299").starts_with("OK "));
    // Existing malformed data still fails; an absent image alone gets a page.
    bool failed=false;try{majiro::read_image({0,1,2});}catch(const std::exception&){failed=true;}
    check(failed);
    std::cout<<"{\"status\":\"passed\",\"checks\":"<<checks<<",\"mask_reads\":"<<mask_reads<<"}\n";
}
