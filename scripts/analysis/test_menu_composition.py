"""Reported header clipping, save-page digits and transient black-band checks."""
from __future__ import annotations
import numpy as np
from PIL import Image,ImageDraw
from model_session import Session
from test_settings import click_id
from test_title_render import render_state
from port_common import ROOT,archive_dir,read_json,write_json


def settle(session,milliseconds=800):
    for _ in range(milliseconds//20):
        session.clock["ms"]+=20;session.event()
        assert session.vm["run"](session.vm,10000)!="blocked",session.summary()


def run(verify_only=False):
    evidence=archive_dir("menu_composition")
    session=Session(not verify_only,save_root=evidence/"saves")
    assert session.advance()=="ui"and session.click(2)=="ui"
    assets=read_json(ROOT/"resources/image_catalogue.json")
    variants={key:np.asarray(Image.open(ROOT/"resources"/assets["sl_load_"+key+".rct"]["file"]).convert("RGBA"))
              for key in("f","p")}
    working=evidence/"20261109_working.png"
    rows=[];cases=[]
    for selected in(0,1,2,5,9,0):
        if selected!=0 or cases:
            assert click_id(session,600+selected)=="ui",session.summary()
        settle(session)
        render_state(session,working)
        with Image.open(working)as picture:
            actual=np.asarray(picture.convert("RGB"))
            rows.append(picture.crop((620,15,1155,110)).convert("RGB"))
        for digit in range(10):
            rectangle=(632+52*digit,60,671+52*digit,93)
            x0,y0,x1,y1=rectangle
            source=variants["p"if digit==selected else"f"][y0:y1,x0:x1]
            opaque=source[:,:,3]==255
            difference=np.abs(actual[y0:y1,x0:x1].astype(int)-source[:,:,:3].astype(int))
            assert opaque.sum()>30,("no opaque pager glyph pixels",digit)
            assert difference[opaque].max()<=2,("pager digit displaced or duplicated",selected,digit,
                int(difference[opaque].max()))
        cases.append({"selected_page":selected+1,"source_digits":10,"status":"passed"})
    sheet=Image.new("RGB",(535,len(rows)*95))
    for i,row in enumerate(rows):sheet.paste(row,(0,i*95))
    sheet.save(evidence/"20261109_pager_contact.png")
    # The selected pause-menu stripe used to become opaque across the full
    # screen when copied from an RGB-only temporary image.
    session=Session(not verify_only,save_root=evidence/"story_saves")
    assert session.advance()=="ui"and session.click(1)=="text"
    for _ in range(4):assert session.page()=="text"
    session.clock["ms"]+=500;session.event()
    session.system["state"]["reading_wait"]=False
    session.event(x=1000,y=650,buttons=2)
    assert session.advance(stop_at_text=False)=="ui",session.summary()
    assert click_id(session,3)=="ui",session.summary()
    settle(session)
    config=evidence/"20261109_story_config.png";render_state(session,config)
    with Image.open(config)as picture:
        values=np.asarray(picture.convert("RGB"))
        # These are outside the settings panel and the source's letterbox.
        # No menu action may turn an entire scanline into a black band.
        for y in range(70,600):
            assert values[y,20:110].max()>20,("settings black band",y)
        original=np.asarray(Image.open(ROOT/"resources"/assets["config_off.rct"]["file"]).convert("RGBA"))
        header=original[40:78,760:1110]
        opaque=(header[:,:,3]==255)&(header[:,:,:3].max(axis=2)>100)
        difference=np.abs(values[40:78,760:1110].astype(int)-header[:,:,:3].astype(int))
        assert opaque.sum()>100,"source header glyph pixels missing"
        assert difference[opaque].max()<=2,("settings header clipped",int(difference[opaque].max()))
    first=Session(not verify_only,save_root=evidence/"first_history_saves")
    assert first.advance()=="ui"and first.click(1)=="text"
    original_text=first.system["state"]["console"]["text"]
    first.clock["ms"]+=500;first.event();first.vm["run"](first.vm,10000)
    first.system["state"]["reading_wait"]=False;first.event(x=1000,y=650,buttons=2)
    assert first.advance(stop_at_text=False)=="ui",first.summary()
    assert click_id(first,2)=="ui",first.summary()
    assert len(first.system["state"]["ui_areas"])==0,"fixture unexpectedly contains voice controls"
    assert first.system["state"]["active_ui_script"]=="history.mjo"
    settle(first)
    first.event(x=1100,y=650,buttons=2)
    assert first.advance()=="text",first.summary()
    assert first.system["state"]["console"]["text"]==original_text
    report={"status":"passed","pager_states":cases,"settings_black_band":False,
            "first_page_history_without_voice_controls":"opened_and_returned",
            "settings_header_source_pixels":int(opaque.sum()),
            "screenshot":str(config),"pager_contact":str(evidence/"20261109_pager_contact.png"),
            "evidence":str(evidence),"scope":"source pager glyph pixels and story settings after pause menu"}
    write_json(ROOT/"reports/menu_composition_tests.json",report)
    working.unlink(missing_ok=True)
    print("menu composition: passed; 60 page digits and settings black-band scan")
    return report


if __name__=="__main__":
    import sys
    run("--verify-only"in sys.argv)
