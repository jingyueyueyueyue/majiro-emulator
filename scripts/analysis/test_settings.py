"""Public CONFIG controls, drag behavior, modal return, and restart preferences."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from model_session import Session
from lua_bridge import table_value
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,write_json


def click_id(session,identifier):
    for _ in range(20):
        session.clock["ms"]+=25;session.event()
        assert session.vm["run"](session.vm,10000)!="blocked",session.summary()
    assert session.advance()=="ui",session.summary()
    number=next(i for i,a in session.system["state"]["ui_areas"].items()
                if a["args"][1]==identifier)
    return session.click(number)


def run(verify_only=False):
    evidence=archive_dir("settings_controls")
    session=Session(not verify_only,save_root=evidence/"saves")
    assert session.advance()=="ui"and session.click(3)=="ui",session.summary()
    cases=[]
    for identifier,fullscreen in((110,True),(111,False)):
        assert click_id(session,identifier)=="ui",session.summary()
        assert bool(session.system["state"]["settings"]["fullscreen"])==fullscreen,(
            identifier,fullscreen,session.system["state"]["settings"]["fullscreen"])
        cases.append({"area":identifier,"fullscreen":fullscreen})
    for identifier in(130,131,210,211,121):
        assert click_id(session,identifier)=="ui",session.summary()
        cases.append({"area":identifier,"status":"returned_to_config"})
    for identifier in(170,180,190,512,522,532,542):
        assert click_id(session,identifier)=="ui",session.summary()
        cases.append({"audio_preview":identifier,"status":"returned_to_config"})
    # The source polls the track directly; area 170 is the speaker preview.
    before=session.system["state"]["settings"]["musicvolume"]
    x,y=493+int(511*before/1000),426
    session.clock["ms"]+=500
    session.event(x=x,y=y,buttons=1,held_buttons=1)
    for at in range(25):
        session.clock["ms"]+=20
        session.event(x=x+at*8,y=y,held_buttons=1)
        assert session.vm["run"](session.vm,10000)!="blocked",session.summary()
    session.event(x=x+192,y=y,held_buttons=0)
    assert session.advance()=="ui",session.summary()
    after=session.system["state"]["settings"]["musicvolume"]
    assert after!=before,(before,after)
    cases.append({"slider":"musicvolume","before":before,"after":after})
    assert click_id(session,999)=="ui",session.summary()
    assert len(session.system["state"]["ui_areas"])==4,session.summary()
    session.system["saves"]["flush"](session.vm)
    registry=table_value(session.system["state"]["registry"])
    restarted=Session(not verify_only,save_root=evidence/"saves")
    assert restarted.advance()=="ui"and restarted.click(3)=="ui",restarted.summary()
    restored=restarted.system["state"]["settings"]["musicvolume"]
    assert restored==after,(restored,after,registry)
    assert click_id(restarted,998)=="ui",restarted.summary()
    areas=restarted.system["state"]["ui_areas"]
    if areas[1]["script"]=="message_box.mjo":
        assert click_id(restarted,1)=="ui",restarted.summary()
    restarted.system["state"]["ui_waiting"]=False
    assert restarted.advance()=="ui",restarted.summary()
    reset=restarted.system["state"]["settings"]["musicvolume"]
    assert reset==before,("reset defaults retained old user profile",reset,before)
    cases.append({"control":"reset_defaults","musicvolume":reset})
    report={"status":"passed","cases":cases,"registry_entries":len(registry),
            "volume_after_restart":restored,"evidence":str(evidence),
            "scope":"model source controls and persistence; native fullscreen/font dialogs not covered"}
    write_json(ROOT/"reports/settings_tests.json",report)
    write_json(evidence/"20261109_result.json",report)
    print(json.dumps(report,ensure_ascii=True))
    return report


if __name__=="__main__":
    run("--verify-only"in sys.argv)
