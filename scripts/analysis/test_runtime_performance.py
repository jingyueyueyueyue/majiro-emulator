"""Behavior guards and bounded benchmarks for lazy assets, video and idle frames."""
from pathlib import Path
import json
import struct
import subprocess
import sys
import time

from lua_bridge import create_lua
from test_screen_semantics import Portable
from audit_player_semantics import registered_providers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json


def run():
    folder = archive_dir("runtime_performance")
    scheduler_lua, _, _, system=registered_providers(overrides={"now":lambda:100})
    scheduler_lua.globals()["SYSTEM"]=system
    scheduler_lua.globals()["VM"]=scheduler_lua.execute(
        (ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
    scheduler_lua.globals()["OPS"]=scheduler_lua.execute(
        (ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
    scheduler_lua.execute("""
      local script={name='wake.mjo',main=0,functions={{55,100}},
        code={{0,0x191},{2,0x82c,0},{100,0x191},{102,0x82c,100}}}
      local vm=VM.new{opcodes=OPS,loader=function()return script end,
        syscall=SYSTEM.invoke,wall_deadline=SYSTEM.wall_deadline}
      vm:begin(script.name)
      vm.waiting={kind='time',until_ms=1100}
      assert(SYSTEM.next_wake(vm)==250)
      SYSTEM.state.animation_active=true
      assert(SYSTEM.next_wake(vm)==16,'long source wait starved sprite animation')
      SYSTEM.state.animation_active=false
      assert(vm:spawn(55,{},false)==2)
      vm:select_thread(2);vm.waiting={kind='time',until_ms=107};vm:select_thread(1)
      vm.waiting={kind='input'}
      assert(SYSTEM.next_wake(vm)==7,'background timer hidden by main input wait')
      vm:select_thread(2);vm.waiting=nil;vm:select_thread(1)
      assert(SYSTEM.next_wake(vm)==1,'runnable background context overslept')
      vm:select_thread(2);vm.waiting={kind='yield'};vm:select_thread(1)
      assert(SYSTEM.next_wake(vm)==1,'background yield overslept')
      vm.threads[2].finished=true
      assert(SYSTEM.next_wake(vm)==100,'idle main context busy polled')
      SYSTEM.providers['$time_accera'](vm,{2})
      vm.waiting={kind='time',until_ms=110}
      assert(SYSTEM.next_wake(vm)==5,'virtual deadline did not convert to wall time')
      SYSTEM.providers['$time_accera'](vm,{0})
      assert(SYSTEM.next_wake(vm)==100,'stopped virtual clock spun')
    """)
    lua = create_lua()
    lua.globals()["CONFIGURATION"] = lua.execute(
        (ROOT / "player/runtime/player_configuration.lua").read_text(encoding="utf8"))
    lua.execute("""
      local reads,movie_reads=0,0
      HOST={
        identity=function()return 'fixture'end,
        asset_text=function()return 'return {detect=function()return {}end}'end,
        engine_requirements=function()return {}end,
        member_names=function()return {'majiro.env','one.rct','two.rc8','three.bmp'}end,
        resource_text=function()return 'CLIENT_WIDTH 800\\nCLIENT_HEIGHT 600\\n'end,
        image_metadata=function(name)reads=reads+1;return {width=17,height=9}end,
        movies=function()error('eager video catalogue')end,
        movie_asset=function(name)movie_reads=movie_reads+1
          if name=='opening.mpg'then return {file='movie/'..name,duration_ms=12345}end end}
      local data=CONFIGURATION.make()
      assert(reads==0 and movie_reads==0 and data.width==800)
      assert(data.image_metadata['absent.rct']==nil and reads==0)
      assert(data.image_metadata['one.rct'].height==9 and reads==1)
      assert(data.image_metadata['one.rct'].width==17 and reads==1)
      assert(data.image_metadata['three.bmp'].height==9 and reads==2)
      assert(data.movies['opening.mpg'].duration_ms==12345 and movie_reads==1)
      assert(data.movies['opening.mpg'].duration_ms==12345 and movie_reads==1)
      local next_generation=CONFIGURATION.image_metadata({['one.rct']=true})
      assert(next_generation['one.rct'].height==9 and reads==3)
    """)
    api = Portable(folder)
    try:
        api("create 1 800 600 0")
        api("fill 1 0 0 800 600 255")
        api("create 2 10 10 0")
        api("alpha 2 3 0")
        api("fill 2 0 0 10 10 65280")
        request = "present 1 screen\n2 0 0 10 10 0 0 1 1 0 0 0"
        response, first = api(request)
        assert response == "OK changed" and first[:4] == bytes((0, 255, 0, 255))
        started = time.perf_counter()
        for _ in range(200):
            assert api(request) == ("OK unchanged", b"")
        unchanged_ms = (time.perf_counter() - started) * 1000
        api("fill 3 0 0 10 10 255")
        response, hidden = api(request)
        assert response == "OK changed" and hidden[:4] == bytes((255, 0, 0, 255))
        api("checkpoint_begin")
        api("fill 3 0 0 10 10 0")
        assert api(request)[1] == first
        api("checkpoint_rollback")
        assert api(request)[1] == hidden, "rollback reused a stale composed screen"
        api("fill 3 0 0 10 10 0")
        moved = request.replace("10 10 0 0 1", "10 10 20 0 1")
        response, frame = api(moved)
        assert response == "OK changed" and frame[:4] == bytes((255, 0, 0, 255))
        assert frame[20 * 4:21 * 4] == bytes((0, 255, 0, 255))
        # A mutation inside a batch must invalidate the retained composition.
        command = "fill 2 0 0 10 10 16711680"
        api(f"batch\n{len(command)}\n{command}")
        assert api(moved)[1][20 * 4:21 * 4] == bytes((0, 0, 255, 255))
    finally:
        api.close()
    java_source = r"""
package org.majiro.player;
import java.nio.*;
import java.util.*;
public class MovieFrameChecks {
 public static void main(String[] args) {
  ByteBuffer b=ByteBuffer.allocateDirect(16);
  byte[] raw={1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
  b.put(raw);b.position(5);
  int[] a=MovieFramePixels.pack(b,2,2,17);
  if(a[0]!=2||a[1]!=2||a[2]!=17||b.position()!=5)throw new AssertionError("header/owner position");
  ByteBuffer decoded=ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN);
  for(int i=3;i<a.length;i++)decoded.putInt(a[i]);
  if(!Arrays.equals(decoded.array(),new byte[]{9,10,11,12,13,14,15,16,1,2,3,4,5,6,7,8}))
   throw new AssertionError("RGBA order/vertical flip/alpha");
  b.put(0,(byte)99);
  if(a[5]!=0x04030201)throw new AssertionError("published frame mutated");
  try{MovieFramePixels.pack(b,3,2,0);throw new AssertionError("truncated buffer");}
  catch(IllegalArgumentException expected){}
  try{MovieFramePixels.pack(b,-1,2,0);throw new AssertionError("negative dimensions");}
  catch(IllegalArgumentException expected){}
  System.out.println("{\"status\":\"passed\",\"checks\":5}");
 }
}
"""
    source = folder / "MovieFrameChecks.java"
    source.write_text(java_source, encoding="utf8")
    java = Path(read_json(ROOT / "reports/android_toolchain.json")["java"]) / "bin"
    for name, command in (
        ("compile", [str(java / "javac.exe"), "-d", str(folder), str(source),
                     str(ROOT / "player/android/src/org/majiro/player/MovieFramePixels.java")]),
        ("run", [str(java / "java.exe"), "-cp", str(folder), "org.majiro.player.MovieFrameChecks"]),
    ):
        result = subprocess.run(command, capture_output=True, timeout=30)
        (folder / (name + ".txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(name + ": " + result.stderr.decode("utf8", "replace"))
    report = {
        "status": "passed", "timestamp": timestamp(), "lazy_asset_checks": 9,
        "scheduler_checks": 8,
        "video_transfer_checks": 5, "idle_present_calls": 200,
        "idle_present_ms": round(unchanged_ms, 3),
        "scope": "production Lua configuration, portable page composition and Java video byte packing",
        "device_accessed": False,
        "source_sha256": {name: sha256(ROOT / name) for name in (
            "player/runtime/player_configuration.lua", "player/runtime/majiro_syscalls.lua",
            "player/native/majiro_pixels.cpp",
            "player/core/portable_pixels_wrapper.inc",
            "player/android/src/org/majiro/player/MovieFramePixels.java")},
        "evidence": str(folder),
    }
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/runtime_performance_tests.json", report)
    print(json.dumps(report, ensure_ascii=True))
    return report


if __name__ == "__main__":
    run()
