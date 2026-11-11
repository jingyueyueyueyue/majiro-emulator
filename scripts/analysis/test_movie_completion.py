"""Minimal new movie-family argument checks with original Ruri wrappers."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

from audit_player_semantics import registered_providers
from original_movie_completion import MovieCompletionOracle

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def main():
    original = MovieCompletionOracle()
    events, ref = [], {}
    page = {"id": 700, "active": True}
    def movie(action, spec):
        events.append({"action": action, "spec": {key: value for key, value in spec.items()}})
        if action == "texture":return page["id"] if page["active"] else 0
        if action == "texture_width":return 640
        if action == "texture_height":return 360
        if action == "status":return 1
        if action == "texture_stop":page["active"]=False
        if action == "texture_play":page["active"]=True
        return True
    lua,_,_,system = registered_providers(overrides={
        "movie": movie, "movies": {"intro.wmv": {"file": "movie/intro.wmv", "duration_ms": 10000}},
        "now": lambda: 0})
    vm=lua.table()
    media=system["state"]["media"]
    cases=[]
    for name,args in (
        ("$movie_texture_play",["intro.wmv"]), ("$movie_texture_play",["intro.wmv",0,123,456]),
        ("$movie_play_repeat",["intro.wmv"]), ("$movie_play_repeat",["intro.wmv",0,11,22,640,360]),
        ("$movie_play_fromto",["intro.wmv",123,456]),
        ("$movie_play_fromto",["intro.wmv",123,456,0,11,22,640,360]),
    ):
        expected=original.invoke(name,args)
        events.clear()
        actual=system["providers"][name](vm,lua.table_from(args))
        assert actual==0
        request=next(row for row in events if row["action"] in ("texture_play","play_repeat","play_fromto"))
        spec=request["spec"]
        values = ([int(bool(spec["repeat_play"])),spec["range_from"],spec["range_to"]]
                  if name=="$movie_texture_play" else
                  [spec["x"],spec["y"],spec["width"],spec["height"]]+
                  ([spec["range_from"],spec["range_to"]]if name=="$movie_play_fromto"else[]))
        native=expected["events"][0]["arguments"]
        values=[int(value)&0xffffffff for value in values]
        assert values==native,(name,values,native)
        cases.append({"name":name,"arguments":args,"original_transport_arguments":native,"status":"passed"})
    assert system["providers"]["$movie_texture"](vm,lua.table())==700
    assert system["state"]["pages"][700]["width"]==640
    for volume in (-1,0,413,1000,2147483647):
        expected=original.invoke("$movie_texture_volume",[volume])
        system["providers"]["$movie_texture_volume"](vm,lua.table_from([volume]))
        assert media["movie_texture_volume"]==(expected["texture_volume"]if volume>=0 else -1)
    system["providers"]["$movie_texture_stop"](vm,lua.table())
    assert system["state"]["pages"][700]is None
    assert system["providers"]["$movie_texture"](vm,lua.table())==0
    # Host incapable of dynamic pixels must fail, not register a fake page.
    fail_lua,_,_,fail_system=registered_providers(overrides={
        "movie":lambda action,spec:0,
        "movies":{"intro.wmv":{"file":"movie/intro.wmv","duration_ms":10000}}})
    failed=False
    fail_vm=fail_lua.table()
    def fail(current,reason):raise RuntimeError(reason)
    fail_vm["fail"]=fail
    try:fail_system["providers"]["$movie_texture_play"](fail_vm,fail_lua.table_from(["intro.wmv"]))
    except Exception:failed=True
    assert failed
    report={"schema":1,"timestamp":timestamp(),"status":"passed","cases":cases,
            "volume_cases":5,"texture_page_and_release_checks":True,"missing_texture_host_rejected":True,
            "original_exe_sha256":original.IDENTITY,
            "test_sources":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                           for name in("runtime/majiro_media.lua","scripts/analysis/original_movie_completion.py",
                                        "scripts/analysis/test_movie_completion.py")},
            "android_host_integrated":False,"physical_video_decode_verified":False,
            "limits":["Original wrapper boundary arguments only; backend time units remain unverified.",
                      "Real decoded drawable pages require platform extension; fake zero page is rejected."]}
    folder=archive_dir("movie_completion_tests")
    write_json(folder/"20261110_movie_completion_tests.json",report)
    write_json(ROOT/"reports/movie_completion_tests.json",report)
    print("new movie family: 6 original argument cases, 5 volume cases and page lifecycle checks passed")


if __name__=="__main__":
    main()
