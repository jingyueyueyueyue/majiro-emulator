"""Public pause-menu transitions: timing, complete frames and clean backdrops."""
from __future__ import annotations
from PIL import Image, ImageDraw
from model_session import Session
from test_title_render import render_state
from lua_bridge import table_value
from port_common import ROOT, archive_dir, sha256, write_json


def run(verify_only=False):
    evidence=archive_dir("menu_transitions")
    cases=[]
    for identifier,scene in((3,"config"),(0,"save"),(1,"load")):
        session=Session(not verify_only,save_root=evidence/(scene+"_saves"))
        assert session.advance()=="ui"and session.click(1)=="text"
        for _ in range(4):assert session.page()=="text"
        state=session.system["state"]
        session.clock["ms"]+=500;session.event()
        state["reading_wait"]=False
        session.event(x=1000,y=650,buttons=2)
        assert session.advance(stop_at_text=False)=="ui",session.summary()
        for _ in range(40):
            session.clock["ms"]+=20;session.event()
            assert session.vm["run"](session.vm,10000)!="blocked",session.summary()
        timers=[]
        provider=session.system["providers"]["$timer_progress"]
        def progress(vm,args,*extra):
            if vm["frame"](vm)["script"]["name"]=="transit.mjo":
                timers.append({"time":session.clock["ms"],"args":table_value(args)})
            return provider(vm,args,*extra)
        session.system["providers"]["$timer_progress"]=progress
        area=next(a["args"]for a in state["ui_areas"].values()if a["args"][1]==identifier)
        x,y=area[2]+area[4]//2,area[3]+area[5]//2
        state["ui_waiting"]=False;session.event(x=x,y=y)
        assert session.advance()=="ui"
        state["ui_waiting"]=False;session.event(x=x,y=y,buttons=1)
        began=session.clock["ms"]
        frames=[];hashes=set();suppressed=0
        working=evidence/("20261109_"+scene+"_working.png")
        for elapsed in range(0,1401,20):
            session.clock["ms"]=began+elapsed;session.event()
            assert session.vm["run"](session.vm,10000)!="blocked",session.summary()
            if state["invalidate"]:
                render_state(session,working)
                hashes.add(sha256(working))
                with Image.open(working)as picture:
                    # A partial settings frame used to contain only sliders
                    # on black. Every submitted frame must retain its scene.
                    assert picture.convert("RGB").getpixel((20,300))!=(0,0,0),(scene,elapsed)
            else:
                suppressed+=1
            if elapsed%100==0 and working.exists():
                with Image.open(working)as picture:
                    thumb=picture.convert("RGB");thumb.thumbnail((320,180))
                    frames.append((elapsed,thumb.copy()))
        assert len(hashes)>=6,(scene,"no intermediate transition frames",len(hashes))
        assert timers and min(row["args"][1]for row in timers)>=180,(
            scene,"transition duration corrupted",timers)
        sheet=Image.new("RGB",(1280,205*((len(frames)+3)//4)),"#242424")
        draw=ImageDraw.Draw(sheet)
        for i,(elapsed,picture)in enumerate(frames):
            xx,yy=i%4*320,i//4*205
            sheet.paste(picture,(xx,yy+25))
            draw.text((xx+5,yy+5),str(elapsed)+" ms",fill="white")
        contact=evidence/("20261109_"+scene+"_contact.png");sheet.save(contact)
        final=evidence/("20261109_"+scene+"_final.png")
        render_state(session,final)
        assert state["active_ui_script"]==("config.mjo"if scene=="config"else"loadsave.mjo")
        assert not any(s["source_script"]=="menu.mjo"and(s["hidden"]or 0)==0
                       for s in state["sprites"].values()),"outgoing menu stayed visible"
        cases.append({"scene":scene,"status":"passed","distinct_frames":len(hashes),
                      "suppressed_partial_frames":suppressed,
                      "transfer_duration_ms":sorted({row["args"][1]for row in timers}),
                      "contact_sheet":str(contact),"screenshot":str(final)})
        working.unlink(missing_ok=True)
    report={"status":"passed","cases":cases,"evidence":str(evidence),
            "scope":"public pause-menu settings/save/load entry at 20 ms intervals"}
    write_json(ROOT/"reports/menu_transition_tests.json",report)
    print("menu transitions: passed; settings, save and load")
    return report


if __name__=="__main__":
    import sys
    run("--verify-only"in sys.argv)
