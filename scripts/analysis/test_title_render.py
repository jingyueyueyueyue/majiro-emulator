"""Verify the title's unobscured backdrop against decoded original source pixels."""
from __future__ import annotations
from pathlib import Path
import sys
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from majiro_image import Decoder
from port_common import ROOT,archive_dir,write_json,native_pixels_library
from model_session import Session
from native_pixels import Pixels


def assert_title(path):
    expected,_=Decoder().pixels("title_base.rct")
    samples=[]
    with Image.open(path) as actual:
        for x,y in ((400,120),(800,180),(1100,200),(1200,350),(50,500),(1100,550)):
            want=expected.convert("RGB").getpixel((x,y))
            got=actual.convert("RGB").getpixel((x,y))
            if max(abs(a-b)for a,b in zip(want,got))>2:
                raise AssertionError(f"title source backdrop mismatch at {x},{y}: {got} vs {want}")
            samples.append({"point":[x,y],"source_rgb":want,"actual_rgb":got})
    return {"status":"passed","samples":samples,"source":"title_base.rct"}


def render_state(session,path):
    state=session.system["state"]
    session.system["sprites"]["update"]()
    rows=[]
    for id in state["sprite_order"].values():
        s=state["sprites"][id]
        if s and s["destination"]==state["frontbuffer"] and s["anti"]<255 and (s["hidden"]or 0)==0:
            row=[s[k]for k in ("page","sx","sy","width","height","x","y","xscale","yscale","rotate","anti")]
            rows.append(" ".join(map(str,row))+(" 1"if s["extended"]else" 0"))
    # Do not construct Pixels() here: its constructor resets live model pages.
    import ctypes
    api=Pixels.__new__(Pixels)
    api.dll=ctypes.CDLL(str(native_pixels_library()))
    api.dll.RuriPixels.argtypes=[ctypes.c_char_p];api.dll.RuriPixels.restype=ctypes.c_void_p
    api.dll.Delete.argtypes=[ctypes.c_void_p];api.dll.Delete.restype=None
    reply=api("render %d %s\n%s"%(state["frontbuffer"],path,"\n".join(rows)))
    assert reply=="OK",reply


def run():
    session=Session(allow_prepare=False)
    assert session.advance()=="ui",session.summary()
    evidence=archive_dir("title_render")
    picture=evidence/"20261109_title.png"
    render_state(session,picture)
    report=assert_title(picture)
    report["screenshot"]=str(picture)
    write_json(ROOT/"reports/title_render.json",report)
    print("title source backdrop: passed",len(report["samples"]),"points")
    return report


if __name__=="__main__":
    run()
