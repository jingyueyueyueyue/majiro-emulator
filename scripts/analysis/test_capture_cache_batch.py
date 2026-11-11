"""Focused callback and persistent bitmap-cache entry checks."""
from __future__ import annotations
import io
from pathlib import Path
import sys
from PIL import Image
from audit_player_semantics import registered_providers
from native_pixels import Pixels

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    folder=archive_dir("capture_cache_batch");pixels=Pixels();written={};host=[]
    def write(name,data):written[name]=data;return True
    lua,_,_,system=registered_providers(overrides={"graphics":pixels,"file_write":write,
        "wallpaper":lambda *args:host.append(("wallpaper",args))or 1,
        "change_cursor":lambda *args:host.append(("cursor",args))or True,
        "environment":{"CACHEFOLDER":"pic_cache"}})
    vm=lua.eval("""function()
      return{thread_id=1,frame=function()return{script={name='capture'}}end,
        resolve=function(self,hash)return{script={name='capture'},offset=hash}end,
        resume=function(self,action)self.last=action end}
    end""")()
    call=lambda name,*args:system["providers"][name](vm,lua.table_from(args),False)
    call("$mouse_capture_init",1,2,3)
    call("$set_mouse_capture_left",4,5,6,7,8,9,10)
    system["input"]["sample"](lua.table_from({"x":12,"y":34,"held_buttons":1}))
    call("$do_mouse_capture")
    calls=list(vm["last"]["calls"].values())
    assert [row["offset"]for row in calls]==[4,2,1]
    system["input"]["sample"](lua.table_from({"x":12,"y":34,"held_buttons":0}))
    call("$do_mouse_capture")
    assert [row["offset"]for row in vm["last"]["calls"].values()]==[5,1]
    call("$set_mouse_capture_mid",5);call("$mouse_capture_end")
    page=call("$page_create",2,2)
    call("$grp_boxfill",page,0,0,2,2,0x123456)
    call("$write_pic_cache",page,"fixture.bmp")
    data=written["pic_cache/fixture.bmp"]
    bitmap=Image.open(io.BytesIO(data.encode("latin1")if isinstance(data,str)else data))
    assert bitmap.size==(2,2)and bitmap.convert("RGB").getpixel((0,0))==(0x56,0x34,0x12)
    assert call("$set_wallpaper","fixture",2)==1
    call("$change_cursor","cursor.cur")
    assert [entry[0]for entry in host]==["wallpaper","cursor"]
    report={"schema":1,"timestamp":timestamp(),"status":"passed",
        "scope":"mouse capture callback dispatch and button transitions, BMP cache storage, platform command wiring",
        "limits":["physical pointer/double-click timing, cursor file variants and Android WallpaperManager require final device checks"],
        "entries":9}
    write_json(folder/(folder.name[:8]+"_capture_cache.json"),report)
    write_json(ROOT/"reports/capture_cache_tests.json",report)
    print("Capture/cache batch entry checks passed")


if __name__=="__main__":run()
