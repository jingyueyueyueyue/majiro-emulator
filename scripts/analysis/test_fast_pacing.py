"""Present every complete fast-forward page without adding a timed dwell."""
from lua_bridge import create_lua, setup_runtime
from port_common import ROOT, write_json


def run():
    lua=create_lua()
    _,vm,system,clock=setup_runtime(lua,False)
    state=system["state"]
    settings=state["settings"]
    settings["fast_allowed"]=True
    settings["fast_mode"]=True
    state["reading_wait"]=True
    state["present_requested"]=17
    state["fast_page"]=lua.table_from({"generation":17})
    query=lambda:system["providers"]["$is_fast_mode"](vm,lua.table())
    assert query()==0
    clock["ms"]=10000
    assert query()==0, "time before the first presentation must not count"
    system["presented"](16)
    assert query()==0
    system["presented"](17)
    assert state["fast_page"]["started"]==10000 and query()==1
    system["presented"](18)
    assert state["fast_page"]["started"]==10000, "repeated draws must not restart dwell"
    assert query()==1
    settings["fast_mode"]=False
    assert query()==0, "release must stop without an extra paced page"
    settings["fast_read_mode"]=True
    state["current_text_read"]=False
    assert query()==0
    write_json(ROOT/"reports/fast_pacing_tests.json",{
        "status":"passed","visible_dwell_ms":0,
        "cases":["unpresented page stays pending","old presentation ignored",
                 "visible frame permits immediate advance","no timed dwell",
                 "repeat presentation keeps timestamp","release stops",
                 "unread text still stops read-only skip"]})
    print("fast pacing: complete presentation, no timed dwell, release and unread stop passed")


if __name__=="__main__":
    run()
