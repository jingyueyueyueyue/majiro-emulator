"""Run source NAME_DISP hooks; preserve metadata and independent image keys."""
from __future__ import annotations
from model_session import Session
from lua_bridge import table_value
from port_common import ROOT,archive_dir,write_json,sha256


def run():
    session=Session(False)
    assert session.advance()=="ui"and session.click(1)=="text"
    for _ in range(70):assert session.page()=="text",session.summary()
    lua,vm,system=session.lua,session.vm,session.system
    state=system["state"]
    opcodes={spec[1]:number for number,spec in vm["opcodes"].items()}
    fixture_scripts={}
    loader=vm["loader"]
    vm["loader"]=lambda name:fixture_scripts[name]if name in fixture_scripts else loader(name)
    drawn=[]
    original_fontout=system["providers"]["$fontout"]

    def capture(current,args,*extra):
        fontout=current["threads"][current["thread_id"]]["fontout"]
        if fontout and fontout["page"]==25:drawn.append(args[1])
        return original_fontout(current,args,*extra)
    system["providers"]["$fontout"]=lua.eval(
        "function(fn)return function(...)return fn(...)end end")(capture)
    cases=[]
    fixture_hash=0x76510000
    inputs=(
        ("依知子＠収録済み","依知子"),
        ("依知子（心の声）","依知子"),
        ("依知子／別収録","依知子"),
        ("依知子：注記","依知子"),
        ("女の子＠依知子","女の子"),
        ("＠依知子","依知子"),
        ("依知子＠使い回し（注）／別：追加","依知子"),
        ("ルリ＠収録済み","ル　リ"),
        ("琉璃＠収録済み","琉　璃"),
        ("健志（回想）","健　志"),
        ("【依知子】＠収録済み","依知子"),
    )
    image_calls=("$page_create_file","$page_create_file_withalfa","$pic_unpack",
                 "$sprite_create_file","$sprite_create_file_ext")
    initial_sources={name:sha256(ROOT/name)for name in(
        "resources/source_manifest.json","localization/character_map.json",
        "localization/proofread_overrides.json")}
    for index,(raw,expected)in enumerate(inputs):
        system["providers"]["$console_cls"](vm,lua.table())
        text='「確認」'
        name="__speaker_metadata_%02d.mjo"%index
        key=fixture_hash+index
        fixture_scripts[name]=lua.table_from({
            "name":name,"id":name,"main":0,"functions":[[key,0]],
            "code":[[0,opcodes["text"],raw+text],[10,opcodes["proc"]],
                    [20,opcodes["ldc.i"],0],[30,opcodes["ret"]]]},recursive=True)
        vm["load"](vm,name)
        before_draws=len(drawn)
        sprite=state["sprites"][268435459]
        portrait_before={field:sprite[field]for field in("page","sx","sy","width","height","x","y")}
        before_calls={call:state["calls"][call]or 0 for call in image_calls}
        thread=vm["spawn"](vm,key,lua.table(),False)
        for _ in range(4000):
            assert vm["run"](vm,10000)!="blocked",session.summary()
            if vm["threads"][thread]["finished"]:break
            deadline=vm["next_deadline"](vm)
            session.clock["ms"]=max(session.clock["ms"]+10,deadline or 0)
            session.event()
        else:raise AssertionError("speaker fixture did not finish: "+raw)
        actual=drawn[before_draws:]
        assert actual==[expected],(raw,actual,expected)
        assert state["console"]["name"]==raw,("raw speaker metadata was changed",raw,
            state["console"]["name"],state["console"]["text"],state["reading_generation"])
        assert vm["globals"][1][0x19c49324]==raw,"source speaker variable lost metadata"
        assert vm["globals"][1][0x251a80a5]==expected,"source display-name normalization was bypassed"
        assert state["console"]["text"]==text,"speaker or annotation leaked into body"
        assert all((state["calls"][call]or 0)==count for call,count in before_calls.items()),raw
        assert all(sprite[field]==value for field,value in portrait_before.items()),raw
        cases.append({"raw_name":raw,"nameplate":actual[0],"metadata_preserved":True,
                      "new_image_lookups":0,"portrait_unchanged":True})
    assert all(sha256(ROOT/name)==value for name,value in initial_sources.items())
    evidence=archive_dir("speaker_metadata")
    report={"status":"passed","cases":cases,"source_inputs_unchanged":True,
            "source_name_handler":{"script":"console.mjo","offset":16414},
            "scope":"original NAME_DISP hooks via isolated in-memory text fixtures at real shrine scene",
            "evidence":str(evidence)}
    write_json(ROOT/"reports/speaker_metadata_tests.json",report)
    write_json(evidence/"20261109_result.json",report)
    print("source speaker metadata: passed",len(cases),"cases; no image key changes")
    return report


if __name__=="__main__":run()
