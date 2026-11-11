#include "../../player/core/majiro_native_import.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>

static std::vector<uint8_t> read(const std::filesystem::path& path){
    std::ifstream stream(path,std::ios::binary);
    if(!stream)throw std::runtime_error("native import probe input missing");
    return{std::istreambuf_iterator<char>(stream),std::istreambuf_iterator<char>()};
}
int wmain(int argc,wchar_t** argv){
    try{
        if(argc!=5)throw std::runtime_error("usage: probe save script raw-name pc");
        auto bytes=read(argv[1]),script=read(argv[2]);
        std::wstring wide=argv[3];std::string name;
        for(wchar_t value:wide){
            if(value>127)throw std::runtime_error("probe script name must be ASCII");
            name.push_back(static_cast<char>(value));
        }
        majiro::NativeImportManifest manifest;
        manifest.game_identity=std::string(64,'a');manifest.original_exe_sha256=std::string(64,'b');
        manifest.save_sha256=majiro::native_import_sha256(bytes);
        manifest.verified_same_script_version=true;
        majiro::NativeImportScriptBinding binding;
        binding.raw_name=name;binding.sha256=majiro::native_import_sha256(script);
        binding.verified_idle_root_offsets.insert(static_cast<uint32_t>(std::stoul(argv[4])));
        manifest.scripts.push_back(binding);
        auto plan=majiro::plan_native_story_import(bytes,manifest,[&](const auto& requested){
            if(requested!=name)throw std::runtime_error("unbound native import script");
            return script;});
        if(!plan.identity_verified)throw std::runtime_error("unverified import plan");
        std::cout<<plan.offset<<" "<<plan.script.sha256<<" "<<plan.save_sha256<<"\n";
        return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 1;}
}
