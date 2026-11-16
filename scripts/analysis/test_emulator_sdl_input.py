"""Compile and execute the production SDL event loop against deterministic events."""
from pathlib import Path
import json
import re
import subprocess
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/"convert"),
               str(Path(__file__).resolve().parents[1]/"build")]
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json
from build_player_core import msvc_environment


def original_wheel():
    from original_opcode import OpcodeOracle
    from unicorn import x86_const as reg
    import unicorn
    sample=next(s for s in read_json(ROOT/"reports/opcode_dispatch.json")["samples"]
                if s["sample"]=="magical_kanon2")
    assert sample["exe_sha256"]=="139d1559bd83da7fa54cc8c4d30ccd45401bfba5e542c4762a5cf0e1e3b539a0"
    oracle=OpcodeOracle(sample)
    uc=oracle.machine
    def timer(machine,address,size,unused):
        stack=machine.reg_read(reg.UC_X86_REG_ESP)
        machine.reg_write(reg.UC_X86_REG_EAX,100)
        machine.reg_write(reg.UC_X86_REG_EIP,oracle.read_u32(stack))
        machine.reg_write(reg.UC_X86_REG_ESP,stack+4)
    hook=uc.hook_add(unicorn.UC_HOOK_CODE,timer,begin=0x44f550,end=0x44f550)
    rows=[]
    try:
        for delta,expected in ((120,[33]),(-120,[34]),(0,[])):
            oracle.write_u32(0x548c0c,0);oracle.write_u32(0x548c10,0)
            uc.reg_write(reg.UC_X86_REG_ESP,oracle.STACK+0x8000)
            uc.reg_write(reg.UC_X86_REG_EDI,0x20a)
            uc.reg_write(reg.UC_X86_REG_ESI,(delta&0xffff)<<16)
            uc.emu_start(0x460d4f,0x460bac,count=1000)
            assert uc.reg_read(reg.UC_X86_REG_EIP)==0x460bac
            keys=[key for key in (33,34)if oracle.read_u32(0x548b88+key*4)==100]
            assert keys==expected,(delta,keys)
            rows.append({"wheel_delta":delta,"keys":keys})
    finally:uc.hook_del(hook)
    return {"exe_sha256":sample["exe_sha256"],"cases":rows}


def run():
    source = ROOT/"player/android/game_host.cpp"
    text = source.read_text("utf8")
    start = text.index("            while (SDL_PollEvent(&event))")
    block = text[start:text.index("            if(quit)break;", start)]
    virtual_start = text.index("                const int virtual_keys[]=")
    virtual_block = text[virtual_start:text.index("                if(requested||keys||window_command)", virtual_start)]
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
 struct{float y=0;int direction=0;}wheel;
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
float x=0,y=0;std::vector<int> pressed;std::map<int,bool> key_down;std::map<int,int> physical_keys;
void process(){SDL_Event event;cursor=0;
// LOOP
queue.clear();
}
SDL_Event event(int type,int key=0){SDL_Event e;e.type=type;e.key.key=key;return e;}
void virtual_process(int keys){
// TOUCH KEYS
}
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
 pressed.clear();
 queue={event(SDL_EVENT_KEY_DOWN,SDLK_LCTRL),event(SDL_EVENT_KEY_DOWN,SDLK_RCTRL),
        event(SDL_EVENT_KEY_UP,SDLK_LCTRL)};process();
 assert(key_down[17]&&pressed==std::vector<int>{17});
 queue={event(SDL_EVENT_KEY_UP,SDLK_RCTRL)};process();assert(!key_down[17]);
 pressed.clear();
 auto wheel=event(SDL_EVENT_MOUSE_WHEEL);wheel.wheel.y=1;
 queue={wheel};process();assert(pressed==std::vector<int>{33});
 pressed.clear();wheel.wheel.direction=SDL_MOUSEWHEEL_FLIPPED;
 queue={wheel};process();assert(pressed==std::vector<int>{34});
 pressed.clear();wheel.wheel.y=0;queue={wheel};process();assert(pressed.empty());
 auto middle=event(SDL_EVENT_MOUSE_BUTTON_DOWN);middle.button.button=-123;
 buttons=held=0;queue={middle};process();assert(buttons==0&&held==0);
 int checks=13;
 const int mapped[][2]={{SDLK_RETURN,13},{SDLK_ESCAPE,27},
  {SDLK_LEFT,37},{SDLK_UP,38},{SDLK_RIGHT,39},{SDLK_DOWN,40},{SDLK_PAGEUP,33},{SDLK_PAGEDOWN,34},
  {SDLK_SPACE,32},{SDLK_BACKSPACE,8},{SDLK_TAB,9},{SDLK_LCTRL,17},{SDLK_RCTRL,17},
  {SDLK_LSHIFT,16},{SDLK_RSHIFT,16},{SDLK_LALT,18},{SDLK_RALT,18},{SDLK_CAPSLOCK,20},
  {'a','A'},{'z','Z'},{'0','0'},{'9','9'}};
 for(const auto& pair:mapped){
  key_down.clear();physical_keys.clear();pressed.clear();
  queue={event(SDL_EVENT_KEY_DOWN,pair[0]),event(SDL_EVENT_KEY_DOWN,pair[0])};process();
  assert(pressed==std::vector<int>{pair[1]}&&key_down[pair[1]]);++checks;
  pressed.clear();queue={event(SDL_EVENT_KEY_UP,pair[0])};process();
  assert(pressed.empty()&&!key_down[pair[1]]&&physical_keys.empty());++checks;
 }
 for(int index=0;index<8;++index){
  pressed.clear();key_down.clear();physical_keys.clear();virtual_process(1<<index);
  auto touch_pressed=pressed;
  assert(key_down.empty()&&physical_keys.empty());++checks;
  pressed.clear();
  queue={event(SDL_EVENT_KEY_DOWN,mapped[index][0]),event(SDL_EVENT_KEY_UP,mapped[index][0])};process();
  assert(pressed==touch_pressed&&!key_down[mapped[index][1]]);++checks;
 }
 for(const auto& pair:std::vector<std::vector<int>>{{SDLK_LCTRL,SDLK_RCTRL,17},
       {SDLK_LSHIFT,SDLK_RSHIFT,16},{SDLK_LALT,SDLK_RALT,18}}){
  key_down.clear();physical_keys.clear();pressed.clear();
  queue={event(SDL_EVENT_KEY_DOWN,pair[0]),event(SDL_EVENT_KEY_DOWN,pair[1]),
         event(SDL_EVENT_KEY_UP,pair[0])};process();
  assert(key_down[pair[2]]&&pressed==std::vector<int>{pair[2]});++checks;
  pressed.clear();queue={event(SDL_EVENT_KEY_UP,pair[1])};process();
  assert(!key_down[pair[2]]&&pressed.empty());++checks;
 }
 for(int type:{SDL_EVENT_WINDOW_FOCUS_LOST,SDL_EVENT_WILL_ENTER_BACKGROUND}){
  key_down.clear();physical_keys.clear();pressed.clear();
  queue={event(SDL_EVENT_KEY_DOWN,SDLK_LCTRL),event(SDL_EVENT_KEY_DOWN,SDLK_LSHIFT),
         event(SDL_EVENT_KEY_DOWN,SDLK_SPACE),event(type)};process();
  assert(key_down.empty()&&physical_keys.empty()&&pressed.empty()&&held==0);++checks;
  queue={event(SDL_EVENT_DID_ENTER_FOREGROUND)};process();
 }
 std::cout<<"{\"status\":\"passed\",\"checks\":"<<checks<<"}";
}
"""
    definitions = "\n".join(f"constexpr int {name}={1000+i};" for i, name in enumerate(constants))
    folder = archive_dir("sdl_input")
    target = folder/"20261111_sdl_input.cpp"
    target.write_text(stub.replace("// CONSTANTS", definitions).replace("// LOOP", block)
                      .replace("// TOUCH KEYS", virtual_block), encoding="utf8")
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
    report["original_wheel"]=original_wheel()
    report.update(timestamp=timestamp(), source_sha256=sha256(source), device_accessed=False,
                  scope="unchanged production SDL event dispatch block, deterministic SDL boundary stubs")
    write_json(folder/"20261111_result.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    run()
