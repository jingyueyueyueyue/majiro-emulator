"""Exercise production event wake ownership, sentinels and bounded idle waits."""
from pathlib import Path
import json
import os
import subprocess
import sys

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/part)for part in("build","convert")]
from build_player_core import msvc_environment
from port_common import ROOT,archive_dir,sha256,timestamp,write_json


def run():
    folder=archive_dir("android_event_wait")
    include=folder/"SDL3";include.mkdir()
    (include/"SDL.h").write_text(r'''
#pragma once
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <mutex>
#define SDLCALL
constexpr int SDL_EVENT_POLL_SENTINEL=1;
struct SDL_Event{int type;};
struct SDL_Semaphore{std::mutex mutex;std::condition_variable ready;int count=0;};
using Watch=bool(*)(void*,SDL_Event*);
inline Watch watch=nullptr;inline void* owner=nullptr;
inline int last_timeout=0;inline bool last_result=false;
inline SDL_Semaphore* SDL_CreateSemaphore(int){return new SDL_Semaphore;}
inline bool SDL_AddEventWatch(Watch w,void* o){watch=w;owner=o;return true;}
inline void SDL_RemoveEventWatch(Watch w,void* o){assert(watch==w&&owner==o);watch=nullptr;}
inline void SDL_DestroySemaphore(SDL_Semaphore* s){assert(!watch);delete s;}
inline bool SDL_SignalSemaphore(SDL_Semaphore* s){
 std::lock_guard lock(s->mutex);++s->count;s->ready.notify_one();return true;
}
inline bool SDL_TryWaitSemaphore(SDL_Semaphore* s){
 std::lock_guard lock(s->mutex);if(!s->count)return false;--s->count;return true;
}
inline bool SDL_WaitSemaphoreTimeout(SDL_Semaphore* s,int ms){
 last_timeout=ms;std::unique_lock lock(s->mutex);
 last_result=s->ready.wait_for(lock,std::chrono::milliseconds(ms),[&]{return s->count>0;});
 if(last_result)--s->count;return last_result;
}
''',encoding="ascii")
    source=folder/"event_wait.cpp"
    source.write_text(r'''
#include "event_wait.hpp"
#include <thread>
#include <iostream>
int main(){
 using namespace std::chrono;
 {
  majiro::android_host::EventWait wait;
  auto emit=[](int kind){SDL_Event e{kind};assert(watch(owner,&e));};
  emit(SDL_EVENT_POLL_SENTINEL);auto began=steady_clock::now();
  wait.wait(10);assert(duration_cast<milliseconds>(steady_clock::now()-began).count()>=7);
  assert(last_timeout==10&&!last_result); // sentinel must not wake the next tick.
  emit(2);wait.wait(250);assert(last_timeout==16&&last_result);
  for(int i=0;i<100;++i)emit(3);
  wait.before_poll();began=steady_clock::now();wait.wait(10);
  assert(!last_result&&duration_cast<milliseconds>(steady_clock::now()-began).count()>=7);
  wait.before_poll();
  // Event arriving after queue draining remains signalled across the wait.
  std::thread input([&]{std::this_thread::sleep_for(milliseconds(2));emit(4);});
  began=steady_clock::now();wait.wait(16);input.join();
  assert(last_result);
 }
 assert(!watch);
 std::cout<<"{\"checks\":6}";
}
''',encoding="ascii")
    env=msvc_environment()
    compiler=Path(env["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    binary=folder/"event_wait.exe"
    for label,command in(
        ("compile",[str(compiler),"/nologo","/O2","/EHsc","/std:c++20",
                    "/I"+str(folder),"/I"+str(ROOT/"player/android"),str(source),
                    "/Fo"+str(folder)+os.sep,"/Fe"+str(binary)]),
        ("run",[str(binary)])):
        result=subprocess.run(command,cwd=folder,env=env,capture_output=True,timeout=60)
        (folder/(label+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(label+" failed: "+str(folder))
    report=json.loads(result.stdout)
    report.update(status="passed",timestamp=timestamp(),evidence=str(folder.relative_to(ROOT)),
                  source_sha256=sha256(ROOT/"player/android/event_wait.hpp"))
    write_json(ROOT/"reports/android_event_wait_tests.json",report)
    write_json(folder/"report.json",report)
    print(json.dumps(report))
    return 0


if __name__=="__main__":raise SystemExit(run())
