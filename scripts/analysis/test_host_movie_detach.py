"""Execute production JNI movie command routing without a device or decoder."""
from pathlib import Path
import json
import subprocess
import sys

from test_audio_preparation_cleanup import method
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert"),
             str(Path(__file__).resolve().parents[1]/"build")]
from port_common import ROOT,archive_dir,sha256,timestamp,write_json
from build_player_core import msvc_environment


def main():
    folder=archive_dir("host_movie_detach")
    production=ROOT/"player/android/game_host.cpp"
    body=method(production.read_text("utf8"),"int movie(lua_State* state)")
    source=r"""
#include <cassert>
#include <map>
#include <string>
#include <stdexcept>
#include <vector>
using jlong=long long;
struct SDL_FRect{float x,y,w,h;};
struct lua_State{
 std::string action;std::map<std::string,double> numbers;double result=0;
};
std::string argument(lua_State*s,int){return s->action;}
std::string field(lua_State*,int,const char*){return "fixture.avi";}
double numeric(lua_State*s,int,const char*k,double fallback=0){
 auto found=s->numbers.find(k);return found==s->numbers.end()?fallback:found->second;}
bool boolean(lua_State*,int,const char*){return false;}
void lua_pushnumber(lua_State*s,double value){s->result=value;}
void need(bool good,const char*why){if(!good)throw std::runtime_error(why);}
int movie_page=0,movie_sequence=-1,screen_width=800,screen_height=600;
bool movie_texture_mode=false,movie_surface_active=false,movie_clear_background=false,fail_stop=false;
struct{unsigned width=0,height=0;std::vector<int>rgba;}movie_rgba;
struct{struct{int invalidations=0;void invalidate(){++invalidations;}}movie;}presentation;
SDL_FRect movie_rect;
std::vector<std::string>calls;
namespace majiro::pixels{std::string command(const std::string&request){calls.push_back(request);return "OK";}}
int movie_command(const std::string&op,const std::string&,int,bool,jlong,jlong){
 calls.push_back("java:"+op);if(op=="stop"&&fail_stop)throw std::runtime_error("decoder cleanup failed");
 return op=="stop"?0:1;
}
void refresh_movie_frame(){calls.push_back("frame");}
// METHOD
void active(){
 movie_page=55;movie_sequence=7;movie_texture_mode=true;movie_surface_active=false;
 movie_rgba.width=2;movie_rgba.height=2;movie_rgba.rgba={77};calls.clear();
}
int main(){
 lua_State state;active();state.action="checkpoint_detach";movie(&state);
 assert(calls==std::vector<std::string>{"java:stop"});
 assert(movie_page==0&&!movie_texture_mode&&!movie_surface_active&&movie_rgba.rgba.empty());
 state.action="texture_play";state.numbers["page_hint"]=55;calls.clear();movie(&state);
 assert(calls==std::vector<std::string>({"java:texture_play","frame"}));
 assert(movie_page==55&&movie_texture_mode);
 active();state.action="texture_stop";movie(&state);
 assert(calls==std::vector<std::string>({"java:texture_stop","release 55"}));
 active();state.action="checkpoint_detach";fail_stop=true;
 try{movie(&state);assert(false);}catch(const std::runtime_error&){}
 assert(movie_page==55&&movie_texture_mode&&calls==std::vector<std::string>{"java:stop"});
 return 0;
}
"""
    file=folder/"20261111_movie_detach.cpp"
    file.write_text(source.replace("// METHOD",body),encoding="utf8")
    env=msvc_environment()
    compiler=Path(env["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    executable=folder/"20261111_movie_detach.exe"
    for name,command in(
        ("compile",[str(compiler),"/nologo","/std:c++20","/EHsc","/utf-8",str(file),
                    "/Fe:"+str(executable),"/Fo:"+str(folder/"20261111_movie_detach.obj")]),
        ("run",[str(executable)])):
        result=subprocess.run(command,cwd=folder,env=env,capture_output=True,timeout=45)
        (folder/("20261111_"+name+".txt")).write_bytes(result.stdout+result.stderr)
        assert result.returncode==0,result.stdout.decode("utf8","replace")+result.stderr.decode("utf8","replace")
    report={"status":"passed","timestamp":timestamp(),"checks":5,
        "source_sha256":sha256(production),"device_accessed":False,
        "scope":"actual production movie() body: transactional detach, normal release, restart ID, cleanup exception"}
    write_json(folder/"20261111_movie_detach.json",report)
    write_json(ROOT/"reports/host_movie_detach_tests.json",report)
    print("Production movie detach: 5 routing/ownership checks passed")
    return report


if __name__=="__main__":
    main()
