"""Capture dialogue labels and every toolbar hover with their native source pages."""
from model_session import Session
from lua_bridge import table_value
from native_pixels import Pixels
from test_title_render import render_state
from port_common import ROOT, archive_dir, write_json


def run():
    evidence = archive_dir("text_feedback")
    session = Session(False)
    assert session.advance() == "ui" and session.click(1) == "text"
    for _ in range(70):
        assert session.page() == "text", session.summary()
    state = session.system["state"]
    session.clock["ms"] += 600
    session.event()
    session.vm["run"](session.vm, 10000)
    render_state(session, evidence / "20261109_dialogue.png")
    cases = []
    api = Pixels.__new__(Pixels)
    import ctypes
    api.dll = ctypes.CDLL(str(ROOT / "resources/native/majiro_pixels.dll"))
    api.dll.RuriPixels.argtypes = [ctypes.c_char_p]
    api.dll.RuriPixels.restype = ctypes.c_void_p
    api.dll.Delete.argtypes = [ctypes.c_void_p]
    api.dll.Delete.restype = None
    for i, area in state["ui_areas"].items():
        a = area["args"]
        if not (700 < a[2] < 1120 and a[3] < 600):
            continue
        session.event(x=a[2] + a[4]//2, y=a[3] + a[5]//2)
        for _ in range(30):
            assert session.vm["run"](session.vm, 10000) != "blocked"
            session.clock["ms"] += 20
            session.event()
        name = "20261109_hover_%d.png" % a[1]
        render_state(session, evidence / name)
        cases.append({"id": a[1], "image": name, "area": table_value(a)})
    pages = []
    for ident, page in state["pages"].items():
        asset = page["asset"]
        if asset and (asset["name"].startswith("console") or asset["height"] == 260):
            name = "20261109_page_%d.png" % ident
            assert api("png %d %s" % (ident, evidence / name)) == "OK"
            pages.append({"id": ident, "asset": table_value(asset), "image": name,
                          "antidata": page["antidata"]})
    write_json(evidence / "20261109_state.json",
               {"cases": cases, "pages": pages, "sprites": table_value(state["sprites"]),
                "journal": table_value(state["graphics_journal"])})
    print(evidence, flush=True)


if __name__ == "__main__":
    run()
