// Standalone shared-provider copy benchmark and complete output digest.
#include "portable_pixels.hpp"
#include <chrono>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

int main(){
    using Clock=std::chrono::steady_clock;
    std::vector<uint8_t> result;
    majiro::pixels::Host host;
    host.present=[&](int,int,const auto& pixels){result=pixels;};
    majiro::pixels::install(host);
    auto command=[](const std::string& text){
        const auto answer=majiro::pixels::command(text);
        if(answer.rfind("OK",0))throw std::runtime_error(answer);
    };
    std::cout<<"[";
    bool first=true;
    for(const auto& label:{"self_copy","separate_extcopy"}){
        command("reset");
        command("create 1 1920 1080 0");command("create 2 1920 1080 0");
        for(int x=0;x<1920;++x)
            command("fill 1 "+std::to_string(x)+" 0 1 1080 "+std::to_string(uint32_t(x)*7297%0x1000000));
        command("fill 2 0 0 1920 1080 1193046");
        const std::string request=std::string(label)=="self_copy"?
            "copy 1 0 0 1919 1079 1 1 1":"extcopy 1 0 0 1920 1080 2 0 0 64";
        std::vector<double> times;
        for(int trial=0;trial<5;++trial){
            const auto start=Clock::now();
            for(int i=0;i<20;++i)command(request);
            times.push_back(std::chrono::duration<double,std::milli>(Clock::now()-start).count()/20);
        }
        command(std::string(label)=="self_copy"?"png 1 screen":"png 2 screen");
        uint64_t hash=14695981039346656037ull;
        for(auto b:result){hash^=b;hash*=1099511628211ull;}
        if(!first)std::cout<<",";first=false;
        std::cout<<"{\"case\":\""<<label<<"\",\"rgba_fnv1a64\":\""<<std::hex<<hash<<std::dec
                 <<"\",\"milliseconds_per_command\":[";
        for(size_t i=0;i<times.size();++i){if(i)std::cout<<",";std::cout<<times[i];}
        std::cout<<"]}";
    }
    std::cout<<"]\n";
}
