"""Navigate source menus from real story input and retain isolated diagnostics."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from model_session import Session
from test_settings import click_id
from test_title_render import render_state
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,write_json


def run(scene="history",verify_only=False):
    evidence=archive_dir("ui_"+scene)
    session=Session(not verify_only,save_root=evidence/"saves")
    assert session.advance()=="ui"and session.click(1)=="text"
    for _ in range(4):assert session.page()=="text",session.summary()
    original_text=session.system["state"]["console"]["text"]
    session.system["state"]["reading_wait"]=False
    session.event(x=1000,y=650,buttons=2)
    assert session.advance(stop_at_text=False)=="ui",session.summary()
    identifier={"history":2,"config":3}[scene]
    status=click_id(session,identifier)
    opened=session.summary()
    render_state(session,evidence/("20261109_"+scene+".png"))
    write_json(evidence/"20261109_opened.json",opened)
    if status=="blocked":raise AssertionError(opened["problem"])
    if scene=="config":
        assert status=="ui"
        assert click_id(session,999)=="text",session.summary()
        assert session.system["state"]["console"]["text"]==original_text
        assert session.page()=="text"
        assert session.system["state"]["console"]["text"]!=original_text
    else:
        assert status=="ui",opened
        for _ in range(20):
            session.clock["ms"]+=25;session.event()
            assert session.vm["run"](session.vm,10000)!="blocked",session.summary()
        session.event(x=1100,y=650,buttons=2)
        try:
            assert session.advance()=="text",session.summary()
        except Exception:
            from test_saves import diagnostic
            diagnostic(session,evidence)
            raise
        assert session.system["state"]["console"]["text"]==original_text,session.summary()
        assert session.page()=="text",session.summary()
        assert session.system["state"]["console"]["text"]!=original_text
    report={"status":"passed","scene":scene,"opened":opened,"final":session.summary(),
            "evidence":str(evidence),"scope":"source menu navigation from fourth dialogue page"}
    write_json(ROOT/"reports"/("ui_"+scene+".json"),report)
    write_json(evidence/"20261109_result.json",report)
    print(json.dumps({k:report[k]for k in("status","scene","evidence")},ensure_ascii=True))
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--scene",choices=("history","config"),default="history")
    parser.add_argument("--verify-only",action="store_true")
    args=parser.parse_args();run(args.scene,args.verify_only)
