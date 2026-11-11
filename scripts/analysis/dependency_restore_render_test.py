"""Compile/run actual SDL presentation logic against deterministic failure injection."""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]

FAKE_SDL = r"""
#pragma once
#include <cstdint>
#include <string>
#include <vector>
using Uint32=uint32_t;
constexpr Uint32 SDL_EVENT_RENDER_DEVICE_RESET=0x2001,SDL_EVENT_RENDER_TARGETS_RESET=0x2000;
constexpr Uint32 SDL_EVENT_DID_ENTER_FOREGROUND=0x106,SDL_EVENT_QUIT=0x100;
constexpr int SDL_PIXELFORMAT_RGBA32=1,SDL_TEXTUREACCESS_STREAMING=1,SDL_SCALEMODE_LINEAR=1;
struct SDL_Renderer{};
struct SDL_FRect{float x,y,w,h;};
struct SDL_Texture{int w,h;std::vector<uint8_t> pixels;};
namespace fake{
enum Failure{NONE,CREATE,SCALE,UPDATE,COLOR,CLEAR,DRAW,PRESENT};
inline Failure failure=NONE;
inline int allocations=0,destroyed=0,updates=0,draws=0,presents=0;
inline std::string error;
inline std::vector<uint8_t> drawn;
inline bool fail(Failure point){if(failure!=point)return false;failure=NONE;error="injected failure";return true;}
}
inline const char* SDL_GetError(){return fake::error.c_str();}
inline SDL_Texture* SDL_CreateTexture(SDL_Renderer*,int,int,int w,int h){
 if(fake::fail(fake::CREATE))return nullptr;++fake::allocations;return new SDL_Texture{w,h,{}};}
inline void SDL_DestroyTexture(SDL_Texture* t){if(t){++fake::destroyed;delete t;}}
inline bool SDL_SetTextureScaleMode(SDL_Texture*,int){return !fake::fail(fake::SCALE);}
inline bool SDL_UpdateTexture(SDL_Texture* t,const void*,const void* pixels,int pitch){
 if(fake::fail(fake::UPDATE))return false;
 if(pitch!=t->w*4){fake::error="bad pitch";return false;}
 auto p=static_cast<const uint8_t*>(pixels);t->pixels.assign(p,p+size_t(t->w)*t->h*4);++fake::updates;return true;}
inline bool SDL_SetRenderDrawColor(SDL_Renderer*,int,int,int,int){return !fake::fail(fake::COLOR);}
inline bool SDL_RenderClear(SDL_Renderer*){return !fake::fail(fake::CLEAR);}
inline bool SDL_RenderTexture(SDL_Renderer*,SDL_Texture* t,const void*,const SDL_FRect*){
 if(fake::fail(fake::DRAW))return false;if(!t){fake::error="null texture";return false;}
 fake::drawn=t->pixels;++fake::draws;return true;}
inline bool SDL_RenderPresent(SDL_Renderer*){if(fake::fail(fake::PRESENT))return false;++fake::presents;return true;}
"""

TEST = r"""
#include "render_recovery.hpp"
#include <iostream>
#include <functional>
using namespace majiro::android_host;
int checks=0;
void require(bool ok,const char* name){if(!ok)throw std::runtime_error(name);++checks;}
bool contains(const std::string& value,const std::string& expected){return value.find(expected)!=std::string::npos;}
int main(){
 try{
  SDL_Renderer renderer;SDL_FRect rect{0,0,2,1};
  std::vector<uint8_t> scene{0,0,0,255,16,24,32,255};
  std::vector<uint8_t> video{90,80,70,255,60,50,40,255};
  FrameView sf{2,1,&scene},vf{2,1,&video};
  Presentation p;
  auto draw=[&](bool movie=false,bool screen=true){return p.draw(&renderer,sf,vf,screen,movie,rect);};
  require(draw(),"initial frame draws");
  require(p.screen.uploads==1&&fake::drawn==scene,"initial CPU bytes reach texture");
  require(draw()&&p.screen.uploads==1,"unchanged VM frame reuses uploaded texture");
  auto create=p.screen.creations;
  require(!p.event(SDL_EVENT_QUIT)&&p.screen.get(),"unrelated event preserves texture");
  for(auto type:{SDL_EVENT_RENDER_DEVICE_RESET,SDL_EVENT_RENDER_TARGETS_RESET,SDL_EVENT_DID_ENTER_FOREGROUND}){
   auto uploads=p.screen.uploads;
   require(p.event(type)&&!p.screen.get(),"reset event invalidates old texture");
   require(draw()&&p.screen.uploads==uploads+1&&fake::drawn==scene,"unchanged CPU frame restored after reset");
  }
  require(p.device_resets==1&&p.target_resets==1&&p.foregrounds==1,"reset counters identify event type");
  require(p.screen.creations==create+3,"resets recreate exactly once each");
  create=p.screen.creations;auto uploads=p.screen.uploads;p.reupload();
  require(draw()&&p.screen.creations==create&&p.screen.uploads==uploads+1,"expose reuploads without recreation");
  scene[0]=44;p.screen.changed();
  require(draw()&&fake::drawn[0]==44,"newest CPU frame replaces retained content");
  std::vector<uint8_t> larger(4*4*4,137);FrameView big{4,4,&larger};
  create=p.screen.creations;
  require(p.draw(&renderer,big,{},true,false,rect)&&p.screen.creations==create+1,"dimension change recreates texture");
  require(fake::drawn==larger,"resized frame upload uses new stride");
  require(draw(true)&&fake::drawn==video,"movie frame drawn over scene");
  auto movie_uploads=p.movie.uploads;
  p.event(SDL_EVENT_RENDER_DEVICE_RESET);
  require(draw(true)&&p.movie.uploads==movie_uploads+1&&fake::drawn==video,"same-sequence movie restored after reset");
  movie_uploads=p.movie.uploads;
  require(draw(true)&&p.movie.uploads==movie_uploads,"same-sequence movie does not upload every tick");
  auto screen_uploads=p.screen.uploads;
  p.screen.changed();
  require(draw(true,false)&&p.screen.uploads==screen_uploads,"movie clear mode leaves hidden scene pending");
  require(draw(false,true)&&p.screen.uploads==screen_uploads+1,"scene restored when movie ends");
  for(auto point:{fake::CREATE,fake::SCALE,fake::UPDATE,fake::COLOR,fake::CLEAR,fake::DRAW,fake::PRESENT}){
   p.event(SDL_EVENT_RENDER_DEVICE_RESET);
   auto count=fake::presents;auto failures=p.failures;
   fake::failure=point;
   require(!draw(),"injected SDL failure reported");
   require(p.failures==failures+1&&contains(p.last_error,"injected failure"),"exact failure retained in diagnostics");
   require(fake::presents==count,"partial/failed frame not presented");
   require(!p.screen.get()&&p.screen.dirty(),"failed texture invalidated for retry");
   require(draw()&&fake::drawn==scene,"retry restores retained CPU frame after failure");
  }
  std::vector<uint8_t> transparent{255,0,0,0,0,0,0,255};
  auto stats=pixel_stats({2,1,&transparent});
  require(contains(stats,"\"nonblack_pixels\":1"),"statistics count RGB independently of alpha");
  require(contains(stats,"\"visible_nonblack_pixels\":0"),"statistics identify invisible colored pixels");
  require(contains(stats,"\"alpha_zero_pixels\":1")&&contains(stats,"\"alpha_opaque_pixels\":1"),"statistics distinguish transparency");
  require(stats==pixel_stats({2,1,&transparent}),"statistics hash deterministic");
  transparent[0]=254;
  require(stats!=pixel_stats({2,1,&transparent}),"statistics hash changes with CPU content");
  std::vector<uint8_t> black{0,0,0,255};
  require(contains(pixel_stats({1,1,&black}),"\"visible_nonblack_pixels\":0"),"opaque black frame identified");
  require(pixel_stats({})=="{\"available\":false}","no frame differs from a black frame");
  bool rejected=false;try{validate_rgba(2,2,black);}catch(const std::runtime_error&){rejected=true;}
  require(rejected,"truncated buffer rejected before SDL access");
  rejected=false;try{validate_rgba(-1,1,black);}catch(const std::runtime_error&){rejected=true;}
  require(rejected,"negative dimensions rejected");
  rejected=false;try{validate_rgba(2147483647,2147483647,black);}catch(const std::runtime_error&){rejected=true;}
  require(rejected,"oversized dimensions rejected without overflow");
  require(socket_name("org.majiro.player")=="majiro-debug","legacy socket remains compatible");
  require(socket_name("org.majiro.player.recovery")=="majiro-debug-recovery","recovery socket isolated");
  require(socket_name("org.majiro.player.other")!=socket_name("org.majiro.player.recovery"),"other package socket isolated");
  p.clear();
  require(!p.screen.get()&&!p.movie.get()&&p.presented==0&&p.last_error.empty(),"session cleanup clears textures and diagnostics");
  require(fake::allocations==fake::destroyed,"all allocated textures destroyed");
  std::cout<<"{\"checks\":"<<checks<<",\"status\":\"passed\"}\n";return 0;
 }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 1;}
}
"""


def main():
    source_root = ROOT if (ROOT / "player/android/render_recovery.hpp").is_file() else ROOT / "dependency-restoration/source"
    header = source_root / "player/android/render_recovery.hpp"
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    folder = ROOT / "archive/2026-11/20261110_dependency_restore" / (stamp + "_render_regression")
    fake_header = folder / "include/SDL3/SDL.h"
    fake_header.parent.mkdir(parents=True)
    fake_header.write_text(FAKE_SDL, encoding="ascii")
    test = folder / "20261110_render_test.cpp"
    test.write_text(TEST, encoding="ascii")
    vswhere = Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")
    installations = json.loads(subprocess.check_output(
        [str(vswhere), "-all", "-prerelease", "-products", "*", "-format", "json", "-utf8"], encoding="utf-8-sig", cwd=ROOT))
    vcvars = next(Path(row["installationPath"]) / "VC/Auxiliary/Build/vcvars64.bat"
                  for row in installations if (Path(row["installationPath"]) / "VC/Auxiliary/Build/vcvars64.bat").is_file())
    process = subprocess.run(f'cmd.exe /d /s /c ""{vcvars}" >nul && set"', capture_output=True, check=True, cwd=ROOT)
    env = {key.upper(): value for key, value in os.environ.items()}
    for line in process.stdout.decode("mbcs").splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith("="):
            env[key.upper()] = value
    compiler = Path(env["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    binary = folder / "20261110_render_test.exe"
    command = [str(compiler), "/nologo", "/EHsc", "/std:c++20", "/W4", "/WX", "/utf-8",
               "/I" + str(folder / "include"), "/I" + str(header.parent), str(test),
               "/Fe:" + str(binary), "/Fo:" + str(folder / "20261110_render_test.obj")]
    compile_result = subprocess.run(command, capture_output=True, cwd=folder, env=env)
    (folder / "20261110_compile.txt").write_bytes(compile_result.stdout + compile_result.stderr)
    if compile_result.returncode:
        print((compile_result.stdout + compile_result.stderr).decode("mbcs", "replace"))
        return 1
    run = subprocess.run([str(binary)], capture_output=True, cwd=folder, timeout=30)
    (folder / "20261110_test.txt").write_bytes(run.stdout + run.stderr)
    print((run.stdout + run.stderr).decode("utf-8", "replace"))
    result = json.loads(run.stdout) if run.returncode == 0 else {"status": "failed"}
    result.update(header=str(header), evidence=str(folder), device_accessed=False,
                  scope="actual production presentation helper with injected SDL failure/reset semantics")
    (ROOT / "reports/android_render_recovery_tests.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
