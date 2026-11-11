"""Original CONFIG pixels, first-page cursor, letterbox, and source-driven coast transition."""
from __future__ import annotations
from pathlib import Path
import struct
import sys
from PIL import Image,ImageDraw,ImageChops,ImageStat
from model_session import Session
from test_title_render import render_state
from lua_bridge import table_value
from original_x86 import Original
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,write_json,sha256


def assert_config(path,tolerance=0):
    reference=read_json(ROOT/"config/display_reference.json")
    with Image.open(path)as image:
        rgb=image.convert("RGB")
        for item in reference["config_samples"]:
            got=rgb.getpixel(tuple(item["point"]))
            assert max(abs(a-b)for a,b in zip(got,item["rgb"]))<=tolerance,(item,got)
    return {"status":"passed","samples":len(reference["config_samples"]),
            "reference":"config/display_reference.json","per_channel_tolerance":tolerance}


def run(verify_only=False):
    evidence=archive_dir("display_fidelity")
    session=Session(allow_prepare=not verify_only)
    assert session.advance()=="ui"and session.click(3)=="ui",session.summary()
    config=evidence/(evidence.name[:8]+"_config.png")
    render_state(session,config);assert_config(config)
    # Follow START and the two reported pages, with ordinary public clicks.
    session=Session(allow_prepare=not verify_only)
    assert session.advance()=="ui"and session.click(1)=="text",session.summary()
    state=session.system["state"];console=state["console"]
    area=console["configuration"];font=console["font_spec"]
    assert console["x"]==area[2]+len(list(console["page"].values())[-1]["text"])*font[3]*2
    assert console["y"]==area[3]+font[4]
    cursor=next(s for s in state["sprites"].values()if s["page"]==43)
    assert cursor["x"]==console["x"]and cursor["y"]==console["y"]-6
    window=state["sprites"][268435456]
    assert window["anti"]==0 and window["x"]==window["y"]==0
    first=evidence/(evidence.name[:8]+"_first.png");render_state(session,first)
    with Image.open(first)as image:
        assert max(ImageStat.Stat(image.crop((0,0,1280,45))).extrema[0])==0
        assert image.convert("RGB").getpixel((640,710))==(0,0,0)
    # Check the actual original alpha-sequence updater at the same timepoints.
    original=Original();pointer=original.memory(bytes(0x630))
    original.machine.mem_write(pointer+0x2c,struct.pack("<I",255))
    original.machine.mem_write(pointer+0x38,struct.pack("<I",1))
    original.machine.mem_write(0x5bab28,struct.pack("<I",pointer))
    original.machine.mem_write(0x4dc36c,struct.pack("<I",1000))
    original.syscall(0x4715f0,[pointer,255,0,0,250,0,-1])
    providers=session.system["providers"];sprite_id=window["id"]
    session.clock["ms"]=1000
    providers["$sprite_alfa_define"](session.vm,session.lua.table_from([sprite_id,255,0,0,250,0,-1]))
    alpha=[]
    for elapsed in(0,50,125,249,250,251):
        original.machine.mem_write(0x4dc36c,struct.pack("<I",1000+elapsed))
        original.call(0x4701c0,[pointer])
        expected=struct.unpack("<I",original.read(pointer+0x2c,4))[0]
        session.clock["ms"]=1000+elapsed;session.system["sprites"]["update"]()
        assert window["anti"]==expected,(elapsed,window["anti"],expected)
        alpha.append({"elapsed_ms":elapsed,"anti":expected})
    image_page=original.memory(struct.pack("<I",256)+bytes(60))
    animation_pointer=original.memory(bytes(0x630))
    for offset,value in((0,image_page),(0xc,32),(0x10,32),(0x38,1)):
        original.machine.mem_write(animation_pointer+offset,struct.pack("<I",value))
    original.machine.mem_write(0x5bab28,struct.pack("<I",animation_pointer))
    original.machine.mem_write(0x4dc36c,struct.pack("<I",2000))
    sequence=[n for frame in range(8)for n in(frame,100)]
    original.syscall(0x471ae0,[animation_pointer,*sequence])
    session.clock["ms"]=2000
    providers["$sprite_animate_define"](session.vm,session.lua.table_from([cursor["id"],*sequence]))
    animation=[]
    for elapsed in(0,100,799,800,900,1600):
        original.machine.mem_write(0x4dc36c,struct.pack("<I",2000+elapsed))
        original.call(0x4703c0,[animation_pointer])
        sx,sy=struct.unpack("<II",original.read(animation_pointer+4,8))
        session.clock["ms"]=2000+elapsed;session.system["sprites"]["update"]()
        assert(cursor["sx"],cursor["sy"])==(sx,sy),(elapsed,cursor["sx"],sx)
        animation.append({"elapsed_ms":elapsed,"sx":sx,"sy":sy})
    # Fresh timeline; the alpha fixture above intentionally used an independent clock.
    session=Session(allow_prepare=not verify_only)
    assert session.advance()=="ui"and session.click(1)=="text"
    session.clock["ms"]+=500;session.event()
    assert session.page()=="text",session.summary()
    second=table_value(session.system["state"]["console"])["text"]
    assert second.startswith("\u63e1"),second
    session.clock["ms"]+=500;session.event()
    session.system["state"]["reading_wait"]=False
    session.event(x=1000,y=650,buttons=1)
    began=session.clock["ms"];frames=[];thumbs=[]
    working=evidence/(evidence.name[:8]+"_working.png")
    for elapsed in range(0,8001,10):
        session.clock["ms"]=began+elapsed;session.event()
        status=session.vm["run"](session.vm,10000)
        if status=="blocked":raise AssertionError(session.summary())
        if elapsed%400==0:
            render_state(session,working)
            with Image.open(working)as image:
                thumb=image.convert("RGB");thumb.thumbnail((320,180));thumbs.append((elapsed,thumb.copy()))
            frames.append({"elapsed_ms":elapsed,"status":status,
                "reading_wait":bool(session.system["state"]["reading_wait"]),
                "console":session.system["state"]["console"]["text"],
                "sha256":sha256(working)})
    assert len({x["sha256"]for x in frames})>=3,"transition has no intermediate image states"
    assert session.system["state"]["reading_wait"],session.summary()
    final=evidence/(evidence.name[:8]+"_after_coast.png")
    working.replace(final)
    sheet=Image.new("RGB",(1280,210*((len(thumbs)+3)//4)),(25,25,25));draw=ImageDraw.Draw(sheet)
    for i,(elapsed,thumb)in enumerate(thumbs):
        x=i%4*320;y=i//4*210;sheet.paste(thumb,(x,y+24));draw.text((x+8,y+5),str(elapsed)+" ms",fill="white")
    contact=evidence/(evidence.name[:8]+"_transition.png");sheet.save(contact)
    report={"status":"passed","config_original_pixel_samples":7,"cursor":table_value(cursor),
        "alpha_original_x86":alpha,"cursor_animation_original_x86":animation,
        "transition_frames":frames,"contact_sheet":str(contact),
        "config_screenshot":str(config),"first_screenshot":str(first),"final_screenshot":str(final),
        "final_console":session.system["state"]["console"]["text"],
        "scope":"source-driven opening transition, original CONFIG pixels and alpha sequence; full-game fidelity incomplete"}
    write_json(ROOT/"reports/display_fidelity.json",report)
    print("display fidelity: passed; CONFIG pixels 7; original alpha 6; coast frames",len(frames))
    return report


if __name__=="__main__":
    run("--verify-only"in sys.argv)
