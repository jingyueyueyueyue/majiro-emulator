"""Exercise original chapter cancel and sustained title rendering."""
import argparse

from model_session import Session
from lua_bridge import table_value
from test_title_render import render_state, assert_title
from port_common import ROOT, archive_dir, write_json


def state_summary(session):
    state=session.system["state"]
    return {"status":session.vm["status"],"steps":session.vm["steps"],
            "problem":table_value(session.vm["problem"]),
            "clock":session.clock["ms"],"invalidate":state["invalidate"],
            "invalidate_depth":state["invalidate_depth"],
            "ui":state["ui_waiting"],"frontbuffer":state["frontbuffer"],
            "sprites":len(state["sprite_order"]),
            "threads":{str(i):{"finished":t["finished"],"waiting":table_value(t["waiting"]),
                "frames":[{"script":f["script"]["name"],
                           "offset":f["script"]["code"][f["index"]][1]}
                          for f in t["frames"].values()]}
                for i,t in session.vm["threads"].items()}}


def run(chapter="sub_title_yura.mjo",via="button"):
    evidence=archive_dir("chapter_return")
    stamp=evidence.name[:8]
    output=lambda name:evidence/(stamp+"_"+name)
    session=Session(False,save_root=evidence/"saves")
    assert session.advance()=="ui"
    assert session.click(1)=="text"
    session.system["providers"]["$exec"](session.vm,session.lua.table_from([chapter]))
    status=session.advance()
    for _ in range(30):
        session.clock["ms"]+=20;session.event(x=640,y=660)
        assert session.vm["run"](session.vm,10000)!="blocked"
    write_json(output("chapter.json"),state_summary(session))
    assert status=="ui",status
    render_state(session,output("chapter.png"))
    session.clock["ms"]+=500
    session.event(x=640,y=660,buttons=2)
    session.system["state"]["ui_waiting"]=False
    status=session.advance()
    write_json(output("right_menu.json"),state_summary(session))
    render_state(session,output("right_menu.png"))
    assert status=="ui"and session.vm["frames"][1]["script"]["name"]==chapter,(
        "right click left the chapter screen",status)
    assert any(a["script"]=="menu.mjo"for a in session.system["state"]["ui_areas"].values())
    # The source pause menu's Return control resumes the suspended chapter.
    areas=session.system["state"]["ui_areas"]
    back=next(i for i,a in areas.items()if a["args"][1]==4)
    assert session.click(back)=="ui"
    assert session.vm["frames"][1]["script"]["name"]==chapter
    assert not session.system["state"]["chapter_menu_active"]
    for _ in range(30):
        session.clock["ms"]+=20;session.event()
        assert session.vm["run"](session.vm,10000)!="blocked"
    render_state(session,output("restored_chapter.png"))
    assert not any(s["source_script"]=="menu.mjo"for s in
                   session.system["state"]["sprites"].values()),"pause menu overlay stayed visible"
    if via=="menu":
        session.clock["ms"]+=500;session.event(x=640,y=660,buttons=2)
        session.system["state"]["ui_waiting"]=False
        assert session.advance()=="ui"
        title=next(i for i,a in session.system["state"]["ui_areas"].items()if a["args"][1]==5)
        assert session.click(title)=="ui"
        no=next(i for i,a in session.system["state"]["ui_areas"].items()if a["args"][1]==0)
        session.clock["ms"]+=500;session.event();session.advance()
        assert session.click(no)=="ui"
        for _ in range(40):
            session.clock["ms"]+=20;session.event()
            assert session.vm["run"](session.vm,10000)!="blocked"
        write_json(output("after_no.json"),{
            **state_summary(session),"areas":table_value(session.system["state"]["ui_areas"])})
        assert session.vm["frames"][1]["script"]["name"]==chapter
        assert not session.system["state"]["chapter_menu_active"]
        session.clock["ms"]+=500;session.event(x=640,y=660,buttons=2)
        session.system["state"]["ui_waiting"]=False
        assert session.advance()=="ui"
        title=next(i for i,a in session.system["state"]["ui_areas"].items()if a["args"][1]==5)
        assert session.click(title)=="ui"
        yes=next(i for i,a in session.system["state"]["ui_areas"].items()if a["args"][1]==1)
        session.clock["ms"]+=500;session.event();session.advance()
        status=session.click(yes)
    else:
        exit_button=next(i for i,a in session.system["state"]["ui_areas"].items()if a["args"][1]==99)
        status=session.click(exit_button)
    assert status=="ui",state_summary(session)
    snapshots=[{"phase":"cancel","navigation":status,**state_summary(session)}]
    write_json(output("render_state.json"),{
        "sprites":table_value(session.system["state"]["sprites"]),
        "order":table_value(session.system["state"]["sprite_order"]),
        "pages":table_value(session.system["state"]["pages"]),
        "events":table_value(session.system["state"]["events"]),
        "journal":table_value(session.system["state"]["graphics_journal"])})
    render_state(session,output("return.png"))
    for elapsed in range(0,6001,20):
        session.clock["ms"]+=20
        session.event(x=640,y=660,buttons=0)
        session.vm["run"](session.vm,10000)
        if elapsed in (0,200,500,1000,2000,4000,6000):
            shot=output("title_%04d.png"%elapsed)
            render_state(session,shot)
            try:
                comparison=assert_title(shot)
            except AssertionError as error:
                comparison={"status":"failed","error":str(error)}
            snapshots.append({"elapsed":elapsed,"comparison":comparison,**state_summary(session)})
    repeats=[]
    for cycle in range(3):
        assert session.click(1)=="text",state_summary(session)
        session.system["providers"]["$exec"](session.vm,session.lua.table_from([chapter]))
        assert session.advance()=="ui"
        back=next(i for i,a in session.system["state"]["ui_areas"].items()if a["args"][1]==99)
        assert session.click(back)=="ui",state_summary(session)
        image=output("repeat_%d.png"%cycle)
        render_state(session,image)
        repeats.append(assert_title(image))
    assert session.click(1)=="text",state_summary(session)
    session.system["providers"]["$exec"](session.vm,session.lua.table_from([chapter]))
    assert session.advance()=="ui"
    first=next(i for i,a in session.system["state"]["ui_areas"].items()if a["args"][1]==1)
    assert session.click(first)=="text",state_summary(session)
    assert session.vm["frames"][1]["script"]["name"]!=chapter
    report={"chapter":chapter,"via":via,"evidence":str(evidence),"samples":snapshots,
            "repeat_returns":repeats,"chapter_selection_enters_story":True,
            "status":"passed"if all(s.get("comparison",{}).get("status")!="failed"
                                   for s in snapshots)else"failed"}
    write_json(output("result.json"),report)
    write_json(ROOT/"reports/chapter_return_tests.json",report)
    print({k:report[k]for k in("chapter","status","evidence")},flush=True)
    print([(s.get("elapsed"),s.get("comparison",{}).get("status"),s["steps"],s["invalidate"])
           for s in snapshots],flush=True)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--chapter",default="sub_title_yura.mjo")
    parser.add_argument("--via",choices=("button","menu"),default="button")
    parser.add_argument("--all",action="store_true")
    args=parser.parse_args()
    if args.all:
        reports=[run(chapter,via)for chapter in(
            "sub_title_yura.mjo","sub_title_iichiko.mjo",
            "sub_title_ruri_akarui.mjo","sub_title_ruri_sibou.mjo")
            for via in("button","menu")]
        report={"status":"passed"if all(r["status"]=="passed"for r in reports)else"failed",
                "cases":reports}
        write_json(ROOT/"reports/chapter_return_tests.json",report)
    else:
        report=run(args.chapter,args.via)
    raise SystemExit(report["status"]!="passed")
