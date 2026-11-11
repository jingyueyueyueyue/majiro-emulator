#include "portable_pixels.hpp"
#include <iostream>
#include <stdexcept>
int main(){
    int checks=0;
    auto check=[&](bool value){++checks;if(!value)throw std::runtime_error("pixel reentry regression");};
    using namespace majiro::pixels;
    for(int i=0;i<20;++i){
        install({});
        check(command("create 1 2 2 0 32").starts_with("OK"));
        check(command("checkpoint_begin")=="OK");
        check(command("create 2 2 2 0 32").starts_with("OK"));
        install({});
        check(command("pixel 1 0 0").starts_with("ERR"));
        check(command("pixel 2 0 0").starts_with("ERR"));
        check(command("checkpoint_begin")=="OK");
        check(command("checkpoint_rollback")=="OK");
        check(command("pixel 1 0 0").starts_with("ERR"));
    }
    std::cout<<"{\"status\":\"passed\",\"checks\":"<<checks<<"}\n";
}
