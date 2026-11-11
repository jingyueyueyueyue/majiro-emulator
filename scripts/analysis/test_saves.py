"""Exercise data integrity and real translated-script save/restart boundaries."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import time

from lua_bridge import create_lua, table_value
from model_session import Session
from test_title_render import render_state
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, sha256, write_json


def codec_checks():
    lua = create_lua()
    lua.globals()["codec"] = lua.execute(
        (ROOT / "runtime/majiro_codec.lua").read_text(encoding="utf-8"))
    return table_value(lua.execute(r'''
      local shared={value=42}
      local source={a=shared,b=shared,bytes=string.char(0,10,13,255),
        bool=false,number=1/3}
      source.self=source
      local encoded=codec.encode(source)
      local decoded=codec.decode(encoded)
      assert(decoded.a==decoded.b and decoded.self==decoded)
      assert(decoded.bytes==source.bytes and decoded.bool==false and decoded.number==1/3)
      assert(encoded==codec.encode(decoded))
      local rejected=0
      for _,bad in ipairs{'','MJS1\nR1\n','MJS1\nS2\nab','MJS1\nNnan\n',
        'MJS1\nZ\ntrailing','MJS1\nT2,0\n','MJS1\nS1\nzz'}do
        assert(not pcall(codec.decode,bad));rejected=rejected+1
      end
      return {bytes=#encoded,rejected=rejected}
    '''))


def state_fingerprint(session):
    state = session.system["state"]
    frame = session.vm["frame"](session.vm)
    return {
        "text": state["console"]["text"],
        "script": frame["script"]["name"],
        "offset": frame["script"]["code"][frame["index"]][1],
        "x": state["console"]["x"], "y": state["console"]["y"],
    }


def invoke(session, name, *args):
    return session.system["providers"][name](
        session.vm, session.lua.table_from(args))

def assert_frame(before,after,fingerprint):
    from PIL import Image,ImageChops,ImageDraw
    with Image.open(before)as a,Image.open(after)as b:
        difference=ImageChops.difference(a.convert("RGB"),b.convert("RGB"))
        # Both the restored source position and cursor coordinates are checked
        # separately. Its continuously looping 32px sprite can have a new phase.
        x,y=fingerprint["x"],fingerprint["y"]
        ImageDraw.Draw(difference).rectangle((x,y-6,x+31,y+25),fill=0)
        assert difference.getbbox()is None,"restored scene differs outside animated cursor"

def diagnostic(session,evidence):
    failure=session.summary()
    failure["last_saved_slot"]=session.system["state"]["last_saved_slot"]
    failure["threads"]={str(i):{
        "finished":t["finished"],"waiting":table_value(t["waiting"]),
        "frames":[{"script":f["script"]["name"],
                   "offset":f["script"]["code"][f["index"]][1]}
                  for f in t["frames"].values()]}
        for i,t in session.vm["threads"].items()}
    write_json(evidence / "20261109_failed.json", failure)
    print("save evidence:", evidence)


def run(verify_only=False):
    evidence = archive_dir("save_roundtrip")
    save_root = evidence / "saves"
    began = time.monotonic()
    codec = codec_checks()
    session = Session(not verify_only, save_root=save_root)
    assert session.advance() == "ui" and session.click(1) == "text", session.summary()
    session.clock["ms"] += 500
    session.event()
    assert session.advance() == "text"
    first = state_fingerprint(session)
    before = evidence / "20261109_before.png"
    render_state(session, before)
    session.event(x=1000, y=650, buttons=2)
    status = session.advance(stop_at_text=False)
    render_state(session, evidence / "20261109_save_menu.png")
    write_json(evidence / "20261109_save_menu.json", session.summary())
    assert status == "ui", (str(evidence), session.summary())
    assert session.click(1) == "ui", session.summary()
    render_state(session, evidence / "20261109_slots.png")
    write_json(evidence / "20261109_slots.json", session.summary())
    try:
        status = session.click(1)
    except Exception:
        diagnostic(session,evidence)
        raise
    write_json(evidence / "20261109_saved.json", session.summary())
    render_state(session, evidence / "20261109_saved.png")
    assert session.system["state"]["last_saved_slot"] == "slot_00101", (str(evidence), session.summary())
    assert status == "input", session.summary()
    session.clock["ms"] += 500
    session.event(x=1000,y=650,buttons=1)
    status=session.advance()
    assert status == "ui", session.summary()
    return_index=next(i for i,a in session.system["state"]["ui_areas"].items()
                      if a["args"][1]==1003)
    try:
        assert session.click(return_index) == "text", session.summary()
    except Exception:
        diagnostic(session,evidence)
        raise
    assert invoke(session, "$copy_save", 101, -1) == 0
    files_after_quick = set(save_root.iterdir())
    quick_bytes = sum(p.stat().st_size for p in files_after_quick)
    # Copying a slot shares its checkpoint and thumbnail; only the index changes.
    assert invoke(session, "$copy_save", -1, 3) == 0
    assert invoke(session, "$get_save_time$", 3)
    assert invoke(session, "$get_save_info$", 3) == invoke(session, "$get_save_info$", -1)
    head=session.system["state"]["registry"]["quicknum"]or 0
    invoke(session,"$copy_save",-1,10002)
    assert invoke(session,"$get_save_info$",10002)==invoke(session,"$get_save_info$",-1)
    session.system["state"]["registry"]["quicknum"]=head+1
    assert invoke(session,"$get_save_info$",10003)==invoke(session,"$get_save_info$",-1)
    session.system["state"]["registry"]["quicknum"]=head
    first_next = session.page()
    assert first_next == "text", session.summary()
    second = state_fingerprint(session)
    assert second["text"] != first["text"]
    invoke(session, "$do_load", 3)
    assert session.advance() == "text", session.summary()
    restored = state_fingerprint(session)
    assert restored == first, (restored, first)
    after = evidence / "20261109_after.png"
    render_state(session, after)
    assert_frame(before,after,first)
    assert session.page() == "text"
    assert state_fingerprint(session) == second
    del session
    # A fresh Lua VM and native page set must restore without prior live state.
    restarted = Session(not verify_only, save_root=save_root)
    assert restarted.advance() == "ui"
    assert restarted.click(2) == "ui", restarted.summary()
    slot=next(i for i,a in restarted.system["state"]["ui_areas"].items()
              if a["args"][1]==0)
    status=restarted.click(slot)
    assert status=="ui",restarted.summary()
    yes=next(i for i,a in restarted.system["state"]["ui_areas"].items()
             if a["args"][1]==1)
    restarted.clock["ms"]+=500
    restarted.event()
    assert restarted.advance()=="ui"
    status=restarted.click(yes)
    if status!="text":
        diagnostic(restarted,evidence)
        render_state(restarted,evidence/"20261109_load_confirm.png")
    assert status == "text", restarted.summary()
    assert state_fingerprint(restarted) == first
    restart_image = evidence / "20261109_restart.png"
    render_state(restarted, restart_image)
    assert_frame(before,restart_image,first)
    assert restarted.page() == "text"
    assert state_fingerprint(restarted) == second
    assert invoke(restarted, "$delete_save", 3) == 0
    assert invoke(restarted, "$get_save_time$", 3) == ""
    assert invoke(restarted, "$get_save_time$", -1)
    from test_settings import click_id
    restarted.system["state"]["reading_wait"]=False
    restarted.event(x=1000,y=650,buttons=2)
    assert restarted.advance(stop_at_text=False)=="ui",restarted.summary()
    assert click_id(restarted,5)=="ui",restarted.summary()
    assert click_id(restarted,1)=="ui",restarted.summary()
    assert len(restarted.system["state"]["ui_areas"])==4
    assert restarted.system["state"]["ui_areas"][1]["script"]=="buttonmenu.mjo"
    assert click_id(restarted,4)=="ui",restarted.summary()
    assert click_id(restarted,1)=="finished",restarted.summary()
    assert restarted.system["state"]["shutdown_requested"]
    report = {"status": "passed", "codec": codec, "first": first, "next": second,
              "quick_save_bytes": quick_bytes,
              "remaining_bytes": sum(p.stat().st_size for p in save_root.iterdir()),
              "seconds": round(time.monotonic() - began, 3), "evidence": str(evidence),
              "scope": "original save/load menus after restart; copy/delete sharing; scene restore; return to title and normal EXIT"}
    write_json(ROOT / "reports/save_tests.json", report)
    write_json(evidence / "20261109_result.json", report)
    print(json.dumps(report, ensure_ascii=True))
    return report


if __name__ == "__main__":
    run("--verify-only"in sys.argv)
