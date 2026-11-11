"""Compile and execute the production SDL event loop against deterministic events."""
from pathlib import Path
import json
import re
import subprocess
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/"convert"),
               str(Path(__file__).resolve().parents[1]/"build")]
from port_common import ROOT, sha256, timestamp, write_json
from build_player_core import msvc_environment


def run():
    source = ROOT/"player/android/game_host.cpp"
    text = source.read_text("utf8")
    start = text.index("            while (SDL_PollEvent(&event))")
    block = text[start:text.index("            if(quit)break;", start)]
    constants = sorted(set(re.findall(r"\bSDL(?:K)?_[A-Z_0-9]+\b", block)))
    stub = r"""
#include <cassert>
#include <cstdint>
#include <map>
#include <string>
#include <vector>
#include <stdexcept>
#include <iostream>
// CONSTANTS
struct SDL_Event{
 int type=0;
 struct{int windowID=1;}render,window;
 struct{float x=0,y=0;}motion;
 struct{float x=0,y=0;int button=0;}button;
 struct{int key=0;}key;
};
std::vector<SDL_Event> queue;size_t cursor=0;
bool SDL_PollEvent(SDL_Event* e){if(cursor==queue.size())return false;*e=queue[cursor++];return true;}
int SDL_GetWindowID(int){return 1;}
void SDL_ConvertEventToRenderCoordinates(int,SDL_Event*){}
uint64_t SDL_GetTicks(){return 100;}
struct{std::string last_event;void event(int){}void reupload(){}}presentation;
void debug(std::string){}
int renderer=0,window=0,held=0,buttons=0,input_activity=0;
bool quit=false,paused=false,render_paused=false;uint64_t paused_at=0,began=0;
float x=0,y=0;std::vector<int> pressed;std::map<int,bool> key_down;
void process(){SDL_Event event;cursor=0;
// LOOP
queue.clear();
}
SDL_Event event(int type,int key=0){SDL_Event e;e.type=type;e.key.key=key;return e;}
int main(){
 queue={event(SDL_EVENT_KEY_DOWN,SDLK_LCTRL)};process();
 assert(key_down[17]&&pressed.size()==1);
 queue={event(SDL_EVENT_WILL_ENTER_BACKGROUND)};process();
 assert(paused&&key_down.empty()&&pressed.empty()&&buttons==0&&held==0);
 auto click=event(SDL_EVENT_MOUSE_BUTTON_DOWN);click.button.button=SDL_BUTTON_RIGHT;
 queue={event(SDL_EVENT_KEY_DOWN,SDLK_LCTRL),click};process();
 assert(key_down.empty()&&pressed.empty()&&buttons==0&&held==0);
 queue={event(SDL_EVENT_DID_ENTER_FOREGROUND)};process();
 assert(!paused&&key_down.empty());
 queue={event(SDL_EVENT_KEY_DOWN,SDLK_LCTRL)};process();
 assert(key_down[17]&&pressed.size()==1);
 queue={event(SDL_EVENT_WINDOW_FOCUS_LOST)};process();
 assert(key_down.empty()&&pressed.empty()&&buttons==0&&held==0);
 queue={event(SDL_EVENT_KEY_DOWN,SDLK_PAGEUP),event(SDL_EVENT_KEY_DOWN,SDLK_PAGEDOWN)};process();
 assert(pressed==std::vector<int>({33,34}));
 std::cout<<"{\"status\":\"passed\",\"checks\":7}";
}
"""
    definitions = "\n".join(f"constexpr int {name}={1000+i};" for i, name in enumerate(constants))
    folder = ROOT.parent/"archive/2026-11/20261111_emulator_audit_host/sdl_input"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder/"20261111_sdl_input.cpp"
    target.write_text(stub.replace("// CONSTANTS", definitions).replace("// LOOP", block), encoding="utf8")
    env = msvc_environment()
    executable = folder/"20261111_sdl_input.exe"
    compiler=Path(env["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    command = [str(compiler), "/nologo", "/std:c++20", "/EHsc", "/utf-8", str(target),
               "/Fe:"+str(executable), "/Fo:"+str(folder/"20261111_sdl_input.obj")]
    result = subprocess.run(command, cwd=folder, env=env, capture_output=True, timeout=60)
    (folder/"20261111_compile.txt").write_bytes(result.stdout+result.stderr)
    assert result.returncode == 0, result.stdout.decode("utf8", errors="replace")
    result = subprocess.run([str(executable)], capture_output=True, timeout=15)
    (folder/"20261111_run.txt").write_bytes(result.stdout+result.stderr)
    assert result.returncode == 0, result.stderr.decode("utf8", errors="replace")
    report = json.loads(result.stdout)
    report.update(timestamp=timestamp(), source_sha256=sha256(source), device_accessed=False,
                  scope="unchanged production SDL event dispatch block, deterministic SDL boundary stubs")
    write_json(folder/"20261111_result.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    run()
