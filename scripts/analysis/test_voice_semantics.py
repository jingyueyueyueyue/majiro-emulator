"""Original voice deferral, fast suppression and force-audition semantics."""
import struct
import unicorn
from original_x86 import Original
from lua_bridge import create_lua
from port_common import ROOT, write_json


def original(fast=False, force=0, skip=False, available=True):
    o = Original()
    request = o.memory(b"voice\0")
    o.machine.mem_write(0x5b73f8, struct.pack("<I", 0x10203040))
    o.machine.mem_write(0x5b45e8, struct.pack("<I", 1000))
    o.machine.mem_write(0x4c9cbc, struct.pack("<I", 1000))
    actions = []
    def boundary(uc, at, size, data):
        esp = uc.reg_read(o.reg.UC_X86_REG_ESP)
        target = struct.unpack("<I", o.read(esp, 4))[0]
        result = 0
        if at == 0x47c550:
            result = 1  # Fixture filename is not the ##nw## protocol marker.
        elif at == 0x46c330:
            output = struct.unpack("<I", o.read(esp+4, 4))[0]
            uc.mem_write(output, b"voice.ogg\0"); result = int(available)
        elif at == 0x43a950:
            result = int(fast)
        elif at == 0x4853e0:
            actions.append("stop")
        elif at == 0x4851a0:
            file, gain = struct.unpack("<2I", o.read(esp+4, 8))
            assert o.read(file, 32).split(b"\0", 1)[0] == b"voice.ogg"
            actions.append({"play": True, "gain": gain}); result = 0x7654
        uc.reg_write(o.reg.UC_X86_REG_EAX, result)
        uc.reg_write(o.reg.UC_X86_REG_ESP, esp+4)
        uc.reg_write(o.reg.UC_X86_REG_EIP, target)
    for at in (0x47c550, 0x46c330, 0x43a950, 0x4853e0, 0x4851a0,
               0x457080, 0x457130, 0x4570d0, 0x48a437):
        o.machine.hook_add(unicorn.UC_HOOK_CODE, boundary, begin=at, end=at)
    o.call(0x46ce90, [request, force, 1, 0, int(skip)])
    return actions


def run():
    lua = create_lua()
    state = lua.table_from({"settings": {}, "media_providers": {}}, recursive=True)
    options = lua.table_from({
        "environment": {"VOICEVOL": 1000},
        "resource_names": {"voice.ogg": True}}, recursive=True)
    options["now"] = lambda: 0
    options["media_asset"] = lambda name: lua.table_from({"file": name, "duration_ms": 1000})
    actions = []
    options["audio"] = lambda action, spec: actions.append(
        {"action": action, "channel": spec["id"], "loop": bool(spec["loop"]),
         "force": bool(spec["force"])}) or True
    active = {"fast": False}
    options["fast_mode"] = lambda vm: active["fast"]
    providers = {}
    def define(name, function):
        providers[name] = function
        state["media_providers"][name] = function
    lua.execute((ROOT / "runtime/majiro_settings.lua").read_text(encoding="utf-8"))["install"](
        define, state, options)
    lua.execute((ROOT / "runtime/majiro_media.lua").read_text(encoding="utf-8"))["install"](
        define, state, options)
    vm = lua.table()
    def call(name, *args):
        return providers[name](vm, lua.table_from(args))
    cases = []
    for fast in (False, True):
        for force in (0, 1, 1000):
            for skip in (False, True):
                expected = original(fast, force, skip)
                active["fast"] = fast
                state["media"]["channels"]["voice"] = None
                actions.clear()
                if skip:
                    call("$set_voice_skip", 1)
                    call("$voice", "voice", force)
                else:
                    call("$voice_raw", "voice", force)
                plays = [a for a in actions if a["action"] == "play"]
                should_play = any(isinstance(a, dict) and a.get("play") for a in expected)
                assert len(plays) == int(should_play), (fast, force, skip, expected, actions)
                assert all(not a["loop"] for a in plays)
                cases.append({"fast": fast, "force": force, "deferred_tag": skip,
                              "original_and_runtime_plays": len(plays)})
    active["fast"] = False
    actions.clear()
    call("$set_voice_skip", 1)
    call("$voice", "voice")
    assert not any(a["action"] == "play" for a in actions)
    assert state["voice_for_history"] == "voice"
    assert call("$voice_get_fn$") == "voice"
    call("$voice_raw", "voice")
    assert sum(a["action"] == "play" for a in actions) == 1
    assert not state["pending_voice_skip"]
    call("$voice_stop")
    assert call("$voice_get_fn$") == "voice"
    assert state["media"]["voice_volume"] == 1000
    active["fast"] = True
    actions.clear()
    call("$voice_ch", 3, "voice", 1)
    assert actions[-1]["channel"] == "voice_ch_3" and not actions[-1]["loop"]
    call("$voice_ch", 3, "voice")
    assert state["media"]["channels"]["voice_ch_3"] is None
    absent=original(available=False)
    assert absent==["stop"]
    active["fast"]=False
    call("$voice_raw","voice")
    actions.clear()
    call("$voice_raw","missing_source_voice")
    assert [a["action"]for a in actions]==["stop"]
    assert state["media"]["channels"]["voice"] is None
    # Normal source text and NAME_DISP hooks must issue one physical play.
    from model_session import Session
    session=Session(False)
    session.vm["trace_limit"]=0
    assert session.advance()=="ui"and session.click(1)=="text"
    source_providers=session.system["providers"]
    original_tag,original_raw=source_providers["$voice"],source_providers["$voice_raw"]
    source_state=session.system["state"]
    flow=[]
    def tag(current,args,*extra):
        before=source_state["media"]["generation"]
        result=original_tag(current,args,*extra)
        assert source_state["media"]["generation"]==before,("tag played early",args[1])
        flow.append({"tag":args[1]})
        return result
    def raw(current,args,*extra):
        before=source_state["media"]["generation"]
        result=original_raw(current,args,*extra)
        assert source_state["media"]["generation"]==before+1,("raw voice did not play",args[1])
        assert flow and flow[-1].get("tag")==args[1],("raw/tag order",args[1],flow[-2:])
        flow[-1]["physical_plays"]=1
        return result
    wrap=session.lua.eval("function(fn)return function(...)return fn(...)end end")
    source_providers["$voice"]=wrap(tag)
    source_providers["$voice_raw"]=wrap(raw)
    for _ in range(80):assert session.page()=="text",session.summary()
    assert flow and all(item.get("physical_plays")==1 for item in flow),flow
    report = {"status": "passed", "source_exe_sha256": Original.IDENTITY,
              "cases": cases, "deferred_tag_then_raw_plays_once": True,
              "metadata_survives_stop_and_fast_skip": True, "forced_channel_one_shot": True,
              "source_dialogue_voice_flow":flow,
              "scope": "actual original 0x46CE90 control flow with resource/transport boundaries stubbed"}
    write_json(ROOT / "reports/voice_semantics_tests.json", report)
    print("voice semantics:", len(cases), "original cases; deferred/raw, force and metadata passed")


if __name__ == "__main__":
    run()
