"""Independent full-screen mask, transformed paste and native audio-unit fixtures."""
from __future__ import annotations
from pathlib import Path
import sys
from lua_bridge import create_lua,setup_runtime,table_value
from native_pixels import Pixels
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,write_json,archive_dir,native_pixels_library


def run():
    lua=create_lua()
    _,vm,system,clock=setup_runtime(lua,allow_prepare=False)
    providers=system["providers"]
    def call(name,*args):
        return providers[name](vm,lua.table_from(args))
    # Use the existing library handle, without resetting the fixture's pages.
    import ctypes
    pixels=Pixels.__new__(Pixels)
    pixels.dll=ctypes.CDLL(str(native_pixels_library()))
    pixels.dll.RuriPixels.argtypes=[ctypes.c_char_p];pixels.dll.RuriPixels.restype=ctypes.c_void_p
    pixels.dll.Delete.argtypes=[ctypes.c_void_p];pixels.dll.Delete.restype=None
    dest=call("$page_create",1280,720)
    page=call("$page_create",1280,720)
    call("$grp_boxfill",page,0,0,1280,720,0xffffff)
    sprite=call("$sprite_create_ext",page)
    call("$sprite_move",sprite,640,360)
    call("$sprite_paste",sprite,dest)
    for x,y in ((0,0),(1279,0),(0,719),(1279,719),(639,359)):
        assert pixels(f"get {dest} {x} {y}")=="OK 16777215",(x,y)
    cases=["centered full-screen sprite paste covers all four corners"]
    call("$grp_boxfill",dest,0,0,1280,720,0)
    call("$sprite_paste",sprite,dest,640,360)
    assert pixels(f"get {dest} 0 0")=="OK 16777215"
    cases.append("explicit sprite paste coordinates also denote the center")
    call("$grp_boxfill",dest,0,0,1280,720,0)
    call("$sprite_xmodify_set",sprite,0.5)
    call("$sprite_ymodify_set",sprite,0.5)
    call("$sprite_paste",sprite,dest)
    for x,y,expected in ((319,180,0),(320,180,0xffffff),(959,539,0xffffff),(960,540,0)):
        assert pixels(f"get {dest} {x} {y}")=="OK "+str(expected),(x,y)
    cases.append("transformed paste preserves centered scale and crop")
    call("$sprite_push",sprite)
    call("$grp_boxfill",dest,0,0,1280,720,255)
    call("$sprite_paste",sprite,dest)
    assert pixels(f"get {dest} 640 360")=="OK 255"
    cases.append("hidden sprites do not modify captured transitions")
    call("$sprite_pop",sprite)
    call("$sprite_alfa_set",sprite,255,1000)
    clock["ms"]=500
    call("$sprite_paste",sprite,dest)
    assert pixels(f"pixel {dest} 640 360")=="OK 255 128 128 255"
    clock["ms"]=1000
    call("$grp_boxfill",dest,0,0,1280,720,255)
    call("$sprite_paste",sprite,dest)
    assert pixels(f"get {dest} 640 360")=="OK 255"
    cases.append("timed transition fade has an intermediate frame and completes")
    small=call("$page_create",4,4)
    call("$grp_boxfill",small,0,0,4,4,65280)
    call("$grp_modify_copy",small,0,0,4,4,dest,10,20,2,2,0,0.5,0.5)
    for x,y,want in ((9,20,255),(10,20,65280),(11,21,65280),(12,21,255)):
        assert pixels(f"get {dest} {x} {y}")=="OK "+str(want),(x,y)
    cases.append("source face thumbnail transform copies into the requested rectangle")
    call("$set_musicvolume",272)
    call("$music","pl_op_inst")
    assert system["state"]["media"]["channels"]["music"]["volume"]==272
    call("$music_vol",500)
    assert system["state"]["media"]["channels"]["music"]["volume"]==136
    call("$set_musicvolume",600)
    assert system["state"]["media"]["channels"]["music"]["volume"]==300
    cases.append("source music settings combine with channel volume and update active playback")
    masked=call("$page_create_withalfa",1,1)
    call("$grp_boxfill",masked,0,0,1,1,65280)
    alpha=call("$get_alfapage",masked)
    call("$grp_point_set",alpha,0,0,128)
    evidence=archive_dir("presentation")
    path=evidence/"20261109_opaque_frame.png"
    assert pixels(f"render {dest} {path}\n{masked} 0 0 1 1 0 0 1 1 0 0 0")=="OK"
    from PIL import Image
    with Image.open(path)as image:
        assert image.convert("RGBA").getextrema()[3]==(255,255)
    cases.append("presented screen is opaque after masked layers have been composited")
    # Independent tag contract: 1000 = full volume, BGM has no SE id.
    lua.globals()["AUDIO"]=lua.execute((ROOT/"runtime/artemis_audio.lua").read_text(encoding="utf-8"))
    tags=table_value(lua.execute("""
      local e={tags={},tag=function(self,t)self.tags[#self.tags+1]=t end,
        now=function()return 0 end}
      local a=AUDIO.install(e)
      local s={id='music',generation=1,file='audio/test.ogg',volume=1000,
        duration_ms=10000,loop=true}
      a.command('play',s);s.volume=650;s.fade_ms=800;a.command('volume',s)
      s.id='sound';s.volume=250;a.command('play',s)
      return e.tags
    """))
    music=next(t for t in tags if isinstance(t,dict)and t.get("1")=="splay")
    fade=next(t for t in tags if isinstance(t,dict)and t.get("1")=="sfade")
    sound=next(t for t in tags if isinstance(t,dict)and t.get("1")=="seplay")
    assert music["gain"]==1000 and "id"not in music,music
    assert fade["gain"]==650 and fade["time"]==800 and "id"not in fade,fade
    assert sound["gain"]==250 and sound["id"]=="ruri_audio_sound",sound
    cases.append("Artemis BGM and SE retain 0..1000 gain and correct channel addressing")
    report={"status":"passed","tests":len(cases),"cases":cases}
    write_json(ROOT/"reports/presentation_tests.json",report)
    print("presentation:",len(cases),"passed")
    return report


if __name__=="__main__":
    run()
