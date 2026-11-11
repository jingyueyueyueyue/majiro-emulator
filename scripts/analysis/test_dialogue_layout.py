"""Reported shrine pages, speaker/portrait layout and actual AUTO toolbar input."""
from __future__ import annotations
import argparse
from model_session import Session
from lua_bridge import table_value
from test_title_render import render_state
from port_common import ROOT,archive_dir,write_json


def run(verify_only=False):
    evidence=archive_dir("dialogue_layout")
    session=Session(not verify_only,save_root=evidence/"saves")
    assert session.advance()=="ui"and session.click(1)=="text"
    state=session.system["state"]
    captured=[]
    for number in range(71):
        if number in(1,52,70):
            console=table_value(state["console"])
            console.pop("history",None)
            assert console["raster_only"],"live text bypasses source page composition"
            if number==52:
                assert "100" in console["text"] and not console["name"]
                assert len(console["page"])==2
                assert all(entry["y"]<180 for entry in console["page"])
                assert state["sprites"][268435456]["y"]==530
            elif number==70:
                assert console["name"]=="ちびっこ"
                assert console["text"].startswith("「") and not console["text"].startswith(console["name"])
                assert state["sprites"][268435457]["y"]==534
                assert state["sprites"][268435459]["anti"]==0
                requests=[r for r in state["graphics_journal"].values()if r.startswith("modify ")]
                # Original face_ofs.cfg selects sana_* = (491,235). The
                # reported crop used fallback (640,430), cutting the head.
                assert " 258 45 466 300 33 "in requests[-1],requests[-1]
            picture=evidence/("20261109_page_%03d.png"%number)
            render_state(session,picture)
            captured.append({"page":number,"console":console,"screenshot":str(picture)})
        if number<70:assert session.page()=="text",session.summary()
    # Release the previous page click and respect the original 350ms
    # suppression interval before testing the toolbar.
    session.clock["ms"]+=500;session.event()
    assert session.vm["run"](session.vm,10000)!="blocked",session.summary()
    # Source console.mjo 0xB168 defines AUTO as toolbar id 2.
    areas=state["ui_areas"]
    index=next(i for i,a in areas.items()if a["args"][1]==2)
    generation=state["reading_generation"]
    before=session.clock["ms"]
    status=session.click(index)
    assert status!="blocked",session.summary()
    # The source toolbar returns a selection before its PAUSE caller
    # dispatches the mode command on the next scheduler turn.
    for _ in range(100):
        if state["settings"]["auto_mode"]:break
        session.clock["ms"]+=10;session.event()
        assert session.vm["run"](session.vm,10000)!="blocked",session.summary()
    assert state["settings"]["auto_mode"],"AUTO toolbar did not enable automatic reading"
    next_status=session.advance(previous_generation=generation)
    assert next_status=="text"and state["reading_generation"]>generation,session.summary()
    assert session.clock["ms"]>before,"AUTO advanced without a reading interval"
    assert not state["settings"]["ultra_fast_mode"]
    # Original input command 0 cancels AUTO on right click. Left click
    # deliberately keeps AUTO enabled, as command 8 only clears skip.
    session.clock["ms"]+=500;state["reading_wait"]=False
    session.event(x=1000,y=650,buttons=2)
    assert session.advance()in("text","ui"),session.summary()
    assert not state["settings"]["auto_mode"],"right click did not cancel AUTO"
    report={"status":"passed","pages":captured,
            "auto":{"elapsed_ms":session.clock["ms"]-before,
                    "generation_before":generation,"generation_after":state["reading_generation"],
                    "manual_cancel":"right_click"},"evidence":str(evidence)}
    write_json(ROOT/"reports/dialogue_layout_tests.json",report)
    print("dialogue layout and public AUTO: passed")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--verify-only",action="store_true")
    run(parser.parse_args().verify_only)
