"""Lossless PNG checks, reference-compositor comparisons and presentation fences."""
from __future__ import annotations
from pathlib import Path
import sys
from PIL import Image
from native_pixels import Pixels
from lua_bridge import create_lua
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,write_json


def run():
    api=Pixels();evidence=archive_dir("presenter_tests");cases=[]
    assert api("create 1 64 48 0")=="OK"
    assert api("create 2 32 24 1")=="OK"
    assert api("create 3 32 24 0 8")=="OK"
    assert api("fill 1 0 0 64 48 3427654")=="OK"
    for y in range(24):
        for x in range(32):
            color=(x*7)+(y*9)*256+((x*13+y*11)%256)*65536
            assert api(f"set 2 {x} {y} {color}")=="OK"
            assert api(f"set 3 {x} {y} {(x*7+y*11)%256}")=="OK"
    assert api("antidata 2 3")=="OK"
    rows=[
        "2 0 0 32 24 16 12 1 1 0 0 0",
        "2 3 2 27 19 1 1 1 1 0 90 0",
        "2 0 0 32 24 17 17 1 1 0 63 1",
        "2 0 0 32 24 -12 -8 1 1 0 1 0",
        "2 0 0 32 24 12 14 .75 1.2 17 12 1",
        "2 0 0 32 24 100000 100000 1 1 0 0 0",
    ]
    reference=evidence/(evidence.name[:8]+"_reference.png")
    fast=evidence/(evidence.name[:8]+"_fast.png")
    for row in rows:
        assert api(f"renderref 1 {reference}\n{row}")=="OK"
        assert api(f"renderfast 1 {fast}\n{row}")=="OK"
        with Image.open(reference)as a,Image.open(fast)as b:
            assert a.convert("RGBA").tobytes()==b.convert("RGBA").tobytes(),row
        with Image.open(fast)as image:image.verify()
    cases.append("optimized compositor and stored-DEFLATE PNG match reference pixels in six masked/clipped/transformed scenes")
    assert api(f"present 1 {fast}\n{rows[0]}")=="OK changed"
    saved=fast.stat().st_mtime_ns
    assert api(f"present 1 {fast}\n{rows[0]}")=="OK unchanged"
    assert fast.stat().st_mtime_ns==saved
    assert api("set 1 0 0 255")=="OK"
    assert api(f"present 1 {fast}\n{rows[0]}")=="OK changed"
    cases.append("unchanged composed pixels do not write or reload another image")
    baseline=read_json(ROOT/"reports/presenter_baseline.json")
    optimized=read_json(ROOT/"reports/presenter_optimized.json")
    with Image.open(baseline["screenshot"])as a,Image.open(optimized["screenshot"])as b:
        assert a.convert("RGBA").tobytes()==b.convert("RGBA").tobytes()
    cases.append("full-size title is pixel-identical to the pre-optimization baseline")
    lua=create_lua()
    lua.globals()["PRESENTER"]=lua.execute((ROOT/"runtime/artemis_presenter.lua").read_text(encoding="utf-8"))
    assert lua.execute("""
      local tags,calls={},0
      local e={tag=function(self,p)tags[#tags+1]=p end}
      local function native(request)
        if request=='session'then return 'OK fixture'end
        calls=calls+1;return calls==1 and 'OK changed'or'OK unchanged'
      end
      local p=PRESENTER.install(e,native,'fixture')
      local s={invalidate=false,frontbuffer=1,dirty=true,sprites={},sprite_order={},
        console={visible=false,text=''}}
      local system={state=s,sprites={update=function()end}}
      assert(not p.draw(system,0,true)and calls==0)
      s.invalidate=true;assert(p.draw(system,16,false)and calls==1)
      assert(not p.draw(system,32,false)and calls==1)
      s.dirty=true;assert(not p.draw(system,48,false)and calls==2)
      for _,tag in ipairs(tags)do assert(tag[1]~='lydel','live layer was deleted')end
      return p.state.frames==1 and p.state.unchanged==1 and p.state.suppressed==1
    """)
    cases.append("partial updates stay hidden, unchanged frames stay bound, live layer is never deleted")
    assert lua.execute("""
      local tags,loading={},true
      local e={tag=function(self,p)tags[#tags+1]=p end,
        bindSurfaceAsync=function()end,isLoadingSurface=function()return loading end,
        unbindSurface=function()end}
      local p=PRESENTER.install(e,function(request)
        return request=='session'and'OK asynchronous'or'OK changed'
      end,'fixture')
      local state={invalidate=true,frontbuffer=1,dirty=true,sprites={},sprite_order={},
        present_requested=1,console={visible=false,text=''}}
      local completed=0
      local system={state=state,sprites={update=function()end},
        presented=function(generation)completed=generation end}
      assert(not p.draw(system,0,false)and p.state.pending and completed==0)
      assert(not p.draw(system,17,false)and completed==0)
      for _,tag in ipairs(tags)do assert(tag[1]~='lyc','unfinished texture was displayed')end
      loading=false
      state.invalidate=false
      assert(not p.draw(system,25,false)and completed==0 and p.state.pending)
      for _,tag in ipairs(tags)do assert(tag[1]~='lyc','texture escaped redraw suspension')end
      state.invalidate=true
      assert(p.draw(system,34,false)and completed==1 and p.state.pending==nil)
      return p.state.frames==1
    """)
    cases.append("async image is committed only when loaded, then acknowledges the source draw fence")
    from lua_bridge import setup_runtime
    _,vm,system,_=setup_runtime(lua,allow_prepare=False)
    from original_x86 import Original
    import struct
    original=Original()
    for name,address in (("$invalidate_disable",0x471540),("$invalidate_disable",0x471540),
                         ("$invalidate_enable",0x471550),("$invalidate_enable",0x471550)):
        system["providers"][name](vm,lua.table())
        original.syscall(address,[])
        expected=struct.unpack("<i",original.read(0x5bab40,4))[0]
        assert system["state"]["invalidate_depth"]==expected
        assert system["state"]["invalidate"]==(expected==0)
    cases.append("nested redraw suspension matches the actual original x86 counter")
    report={"status":"passed","tests":len(cases),"cases":cases,"evidence":str(evidence)}
    write_json(ROOT/"reports/presenter_tests.json",report)
    print("presenter speed regressions:",len(cases),"passed")
    return report


if __name__=="__main__":
    run()
