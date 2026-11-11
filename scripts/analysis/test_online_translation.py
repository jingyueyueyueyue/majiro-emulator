"""Verify original-console translation and read-only lookahead on real Japanese MJO."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_arc import Catalogue
from majiro_mjo import Script
from port_common import ROOT, archive_dir, read_json, sha256, timestamp, write_json
from lua_bridge import create_lua


def main():
    folder = archive_dir("online_translation_tests")
    lua = create_lua()
    def module(name):
        return lua.execute((ROOT / "player/runtime" / name).read_text(encoding="utf8"))
    transport, production, codec = (module(name) for name in
        ("majiro_json.lua", "majiro_translation.lua", "majiro_codec.lua"))
    opcodes = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf8"))
    names = lua.execute((ROOT / "player/tables/syscall_names.lua").read_text(encoding="utf8"))
    checks = []
    def check(label, condition=True):
        assert condition, label
        checks.append(label)
    for value in (False, True, "", "日文と中文🙂\n\t\\\"\x00", -123.5,
                  {"mode": "online", "ready": True, "context": 20,
                   "lines": [{"id": "a", "text": "「テスト」"}]}):
        encoded = transport["encode"](lua.table_from(value, recursive=True) if isinstance(value, dict) else value)
        assert json.loads(encoded) == value
        actual = transport["decode"](json.dumps(value, ensure_ascii=True))
        assert json.loads(transport["encode"](actual)) == value
    for invalid in ('{"a":1,"a":2}', '{"a":}', '{"a":null}', '["\\ud800"]', '["\\udc00"]',
                    '["\\ud800\\u1234"]', '"\x01"', '1e999', '01', '1.', '[] trailing',
                    '[' * 40 + '0' + ']' * 40):
        ok, _ = lua.eval("function(fn,value)return pcall(fn,value)end")(transport["decode"], invalid)
        assert not ok, repr(invalid)
    check("bounded JSON round-trips Unicode, controls, arrays, objects and rejects malformed replies",
          transport["encode"](transport["array"]()) == "[]")
    state = {"time": 0, "config": {"mode": "online", "ready": True, "context": 20, "generation": "a"},
             "result": {}, "reply": "queued", "fail": False}
    calls = []
    def host(action, payload):
        if state["fail"]: raise RuntimeError("mock unavailable")
        if action == "config": return json.dumps(state["config"])
        if action=="waiting":return"ok"
        query = json.loads(payload); calls.append((action, query))
        if action == "submit": return state["reply"]
        if action == "lookup": return state["result"].get(query["id"], "")
        if action == "display_status":return"ok"
        raise AssertionError(action)
    def new(mode="online",wait_ms=0):
        options = lua.table_from({"mode": mode, "game": "game-original"}, recursive=True)
        for key, value in {"opcodes": opcodes, "names": names, "json": transport, "host": host,
                           "now": lambda: state["time"],
                           "wait_ms":wait_ms,
                           "speaker_popup":True,
                           "hash": lambda value: hashlib.sha256(value.encode("utf8")).hexdigest()}.items():
            options[key] = value
        return production["new"](options)
    script = lua.table_from({"name": "story.mjo", "source_sha256": "a" * 64, "functions": [],
                             "code": [[i * 4, 0x840, f"原文{i:02d}"] for i in range(40)] +
                                     [[200, 0x82b]]}, recursive=True)
    vm = lua.eval("""function(script)
      local frame={script=script,index=2};return{context_generation=0,current_offset=0,functions={},
        frame=function()return frame end,
        step=function()error('lookahead ran VM')end,
        call=function()error('lookahead called code')end,
        loader=function()error('lookahead loaded script')end}
    end""")(script)
    adapter = new(); before = codec["encode"](script["code"])
    adapter["instruction"](vm, "text", "原文00")
    first = next(query for action, query in calls if action == "submit")
    check("first consumed sentence queues twenty sequential originals without loading, evaluating or mutating VM",
          len(first["lines"]) == 20 and codec["encode"](script["code"]) == before and vm["current_offset"] == 0)
    check("cache ID includes original MJO SHA, source offset and exact text SHA",
          first["lines"][0]["id"] == "dialogue-v3-original:"+"a" * 64 + ":0:" + hashlib.sha256("原文00".encode()).hexdigest())
    state["time"] = 300; frame = vm["frame"]()
    for i in range(1, 10):
        frame["index"], vm["current_offset"] = i + 2, i * 4
        adapter["instruction"](vm, "text", f"原文{i:02d}")
    submitted = [query for action, query in calls if action == "submit"]
    check("reading ten sentences refills the next ten",
          len(submitted) == 2 and len(submitted[1]["lines"]) == 10 and submitted[1]["lines"][0]["text"] == "原文20")
    named = {spec[1]: code for code, spec in opcodes.items()}
    for name in ("syscall", "syscallp", "call", "callp", "brtrue", "brfalse", "switch",
                 "ret", "br.case", "bsel.jmp.4"):
        branch = lua.table_from({"source_sha256": "b" * 64, "functions": [],
            "code": [[0, 0x840, "before"], [8, named[name], 0, 0, 0], [16, 0x840, "unreached"]]}, recursive=True)
        assert len(adapter["scan"](branch, 1, 20, vm)) == 1, name
    for command in ("d", "x", "o", "g", "f"):
        branch = lua.table_from({"source_sha256": "b" * 64,
            "code": [[0, 0x840, "before"], [8, 0x842, command], [16, 0x840, "after"]]}, recursive=True)
        assert len(adapter["scan"](branch, 1, 20, vm)) == 1
    check("conditional, choice, dynamic text and unresolved control flow stop before unproved future dialogue")
    harmless_voice = next(hash_ for hash_, name in names.items() if name == "$voice")
    branch = lua.table_from({"name": "cfg.mjo", "source_sha256": "c" * 64,
        "functions": [[1234, 40]], "code": [
        [0, 0x840, "before"], [8, 0x835, harmless_voice, 1], [16, 0x810, 1234, 0, 0],
        [24, 0x82c, 32], [28, 0x840, "not reached"], [32, 0x840, "after"], [36, 0x82b],
        [40, 0x800, 0], [46, 0x82e, 56], [52, 0x800, 0], [54, 0x82b], [56, 0x800, 1], [62, 0x82b]]},
        recursive=True)
    rows = adapter["scan"](branch, 1, 20, vm)
    check("lookahead crosses voice transport, a proved no-text callee and deterministic branch without running them",
          len(rows) == 2 and rows[2]["text"] == "after")
    branch["code"][10][2] = 0x840; branch["code"][10][3] = "conditional callee text"
    check("conditional callee containing any possible text prevents speculative continuation",
          len(adapter["scan"](branch, 1, 20, vm)) == 1)
    frame["index"], vm["current_offset"] = 2, 0
    adapter = new(); adapter["instruction"](vm, "text", "原文00")
    check("cache miss preserves the original current buffer", adapter["display"](vm, "原文00") == "原文00")
    state["result"][first["lines"][0]["id"]] = "译文00"
    check("late response never retroactively replaces or replays a consumed page",
          adapter["display"](vm, "原文00") == "原文00")
    adapter["instruction"](vm, "text", "原文00")
    check("next rendering uses cached Chinese through the source console buffer",
          adapter["display"](vm, "原文00") == "译文00")
    check("original MJO literal and offsets remain untouched",
          script["code"][1][3] == "原文00" and script["code"][1][1] == 0)
    vm["context_generation"] = 1; adapter["poll"](vm, lua.table())
    check("reset/import context invalidates pending source ownership", adapter["display"](vm, "原文00") == "原文00")
    def cp932(value):
        return value.encode("cp932")
    width = adapter["display_bytes"]("A汉🙂", cp932)
    check("Chinese and supplementary Unicode receive two display cells without changing VM CP932 bytes",
          len(width) == 5 and width[:1] == "A")
    replacement = adapter["display_bytes"]("A汉?", lambda value: value.encode("cp932", errors="replace"))
    check("replacement-question-mark encoders retain Chinese full width and literal question-mark half width",
          len(replacement) == 4 and replacement[:1] == "A" and replacement[-1:] == "?")
    for mode in ("original", "local"):
        state["config"]["mode"] = mode
        offline = new(mode); old = len(calls)
        offline["instruction"](vm, "text", "never leave device")
        offline["poll"](vm, lua.table())
        assert offline["display"](vm, "never leave device") == "never leave device" and len(calls) == old
    check("original and local modes never submit or lookup game text")
    state["config"]["mode"] = "online"; state["time"] = 2000
    phrase = "本名＠音声ID「原文です。」"
    translation = 'モデル別名「这是汉语译文。」'
    text_id = "dialogue-v3-original:"+"e" * 64 + ":0:" + hashlib.sha256(phrase.encode()).hexdigest()
    state["result"][text_id] = translation
    fixture = lua.eval("""function(text,adapter,encode,phrase)
      local state={console={visible=true,text='',name='',x=0,y=0,configuration={1,0,0,400,200},
          font_spec={'',20,10,28},color=16777215,shadow=0,page={},enabled_history=true,
          history={},history_page={}},settings={},progress={read={}},present_requested=0}
      local graphics,events={},{}
      local options={text_controls={},environment={NAMEPOPUP='1'},cp932=encode,
        display_bytes=adapter and function(v)return adapter.display_bytes(v,encode)end or nil,
        graphics=function(v)graphics[#graphics+1]=v;return'OK'end,now=function()return 0 end,
        online_translation=adapter}
      local frame={script={name='story.mjo',source_sha256=string.rep('e',64),functions={},
        code={{0,0x840,phrase},{24,0x841},{26,0x842,'p'}}},index=2}
      local vm={context_generation=0,current_offset=0,thread_id=1,frames={frame},
        frame=function()return frame end}
      local runtime=text.install(state,options,function(_,name,args)events[#events+1]={name,args}end,{})
      runtime.execute(vm,'text',phrase)
      vm.current_offset=24;frame.index=3;runtime.execute(vm,'proc')
      frame.index=3;runtime.execute(vm,'proc')
      frame.index=3;runtime.execute(vm,'proc')
      vm.current_offset=26;frame.index=4;runtime.execute(vm,'ctrl','p')
      return state,graphics,events,frame
    end""")
    text = module("majiro_text.lua")
    actual, graphics, events, fixture_frame = fixture(text, new(), cp932, phrase)
    check("source NAME_DISP identity is preserved independently of provider speaker output",
          actual["console"]["name"] == "本名＠音声ID" and events[1][1] == "NAME_DISP"
          and events[1][2][1] == "本名＠音声ID")
    check("Chinese body follows original glyphs and CONSOLE_WROTE with original frame offsets",
          "这是汉语译文" in graphics[1] and events[2][1] == "CONSOLE_WROTE"
          and fixture_frame["script"]["code"][1][3] == phrase)
    check("translated history uses original ring accounting without CP932 exceptions",
          actual["console"]["history"][1]["text"] == "「这是汉语译文。」")
    state["result"] = {}
    left, *left_rest = fixture(text, None, cp932, phrase)
    right, *right_rest = fixture(text, new(), cp932, phrase)
    right["text_translation_ready"] = None
    for a, b in zip((left, *left_rest), (right, *right_rest)):
        assert codec["encode"](a) == codec["encode"](b)
    check("uncached original glyphs, callbacks, history and frame are identical with/without online translation")
    delayed=lua.eval("""function(text,adapter,encode,now)
      local state={console={visible=true,text='',name='',x=0,y=0,configuration={1,0,0,400,200},
        font_spec={'',20,10,28},color=16777215,shadow=0,page={}},settings={},progress={read={}}}
      local graphics,events={},{}
      local frame={script={name='delayed.mjo',source_sha256=string.rep('f',64),functions={},
        code={{0,0x840,'原文です。'},{24,0x841},{26,0x842,'p'}}},index=2}
      local vm={context_generation=0,current_offset=0,thread_id=1,frames={frame},
        frame=function()return frame end}
      local runtime=text.install(state,{environment={},cp932=encode,now=now,
        graphics=function(v)graphics[#graphics+1]=v;return'OK'end,online_translation=adapter,
        display_bytes=function(v)return adapter.display_bytes(v,encode)end},
        function(_,event)events[#events+1]=event end,{})
      runtime.execute(vm,'text','原文です。')
      return state,graphics,events,vm,function()
        vm.waiting=nil;vm.current_offset=24;frame.index=3;runtime.execute(vm,'proc')
      end
    end""")
    state["time"]=4000;state["result"]={}
    delayed_adapter=new(wait_ms=15000)
    pending_state,pending_graphics,pending_events,pending_vm,resume=delayed(
        text,delayed_adapter,cp932,lambda:state["time"])
    resume()
    check("cold-cache production proc waits without rastering source text or emitting CONSOLE_WROTE",
          pending_vm["waiting"]["kind"]=="time"and len(pending_graphics)==0 and len(pending_events)==0)
    state["time"]=5000
    delayed_id="dialogue-v3-original:"+"f"*64+":0:"+hashlib.sha256("原文です。".encode()).hexdigest()
    state["result"][delayed_id]="译文已返回。";resume()
    check("asynchronous completion resumes the same proc with Chinese and exactly one source text event",
          len(pending_graphics)==1 and"译文已返回。"in pending_graphics[1]
          and len(pending_events)==1 and pending_events[1]=="CONSOLE_WROTE"
          and delayed_adapter["stats"]["translated"]==1)
    state["result"]={};state["time"]=6000
    timeout_adapter=new(wait_ms=15000)
    _,timeout_graphics,_,timeout_vm,timeout_resume=delayed(text,timeout_adapter,cp932,lambda:state["time"])
    timeout_resume();state["time"]=21001;timeout_resume()
    check("provider timeout releases only the pending source proc and preserves responsive fallback",
          len(timeout_graphics)==1 and"原文です。"in timeout_graphics[1]
          and timeout_adapter["stats"]["fallback"]==1)
    real = []
    sample_config = read_json(ROOT / "config/player_samples.json")["games"] + read_json(ROOT / "config/player_new_samples.json")["games"]
    for game in sample_config:
        catalogue = Catalogue(Path(game["directory"]), include_patch=False)
        scripts, references = [], {}
        for items in catalogue.by_name.values():
            member = items[0]
            if member.extension != ".mjo": continue
            parsed = Script(member.name, member.read())
            record = parsed.runtime_data()
            source = lua.table_from(record, recursive=True)
            scripts.append((source, parsed, record))
            for hash_, offset in parsed.functions:
                references.setdefault(hash_, {"script": source, "offset": offset})
        vm_real = lua.table(); functions = lua.table()
        for hash_, entry in references.items(): functions[hash_] = lua.table_from(entry)
        vm_real["functions"] = functions
        lengths, reasons, proof_failures, examples = [], Counter(), Counter(), []
        for source, parsed, record in scripts:
            indices = [i + 1 for i, row in enumerate(record["code"]) if row[1] == 0x840]
            if not indices: continue
            selected = indices[::max(1, len(indices) // 8)][:8]
            for index in selected:
                found = adapter["scan"](source, index, 20, vm_real)
                lengths.append(len(found)); reasons[adapter["last_scan"]["reason"]] += 1
                if adapter["last_scan"]["proof_failure"]:
                    proof_failures[adapter["last_scan"]["proof_failure"]] += 1
                if len(found) == 20 and len(examples) < 2:
                    examples.append({"script": parsed.name, "offset": int(source["code"][index][1]),
                                     "source_sha256": parsed.sha256, "lines": 20,
                                     "proof_nodes": int(adapter["last_scan"]["proof_nodes"]),
                                     "first_text_sha256": hashlib.sha256(found[1]["text"].encode()).hexdigest()})
        real.append({"game": game["id"], "scripts": len(scripts), "tested_positions": len(lengths),
            "filled_20": sum(n == 20 for n in lengths), "at_least_10": sum(n >= 10 for n in lengths),
            "maximum": max(lengths, default=0), "median": statistics.median(lengths) if lengths else 0,
            "stops": dict(reasons), "proof_failures":dict(proof_failures), "examples": examples,
            "function_context":"all original functions parsed as a read-only availability fixture; runtime only uses actually loaded functions"})
        print(game["id"], "scanned", len(lengths), "source positions", flush=True)
    check("real original Japanese story MJO reach twenty-line prefetch without VM execution",
          sum(row["filled_20"] for row in real) > 0 and
          any(row["filled_20"] for row in real if row["game"] in ("happy_princess", "kizoku", "magical_kanon2", "idol_revolution", "oh_pants")))
    boot = (ROOT / "player/runtime/player_boot.lua").read_text(encoding="utf8")
    native = (ROOT / "player/android/game_host.cpp").read_text(encoding="utf8")
    check("complete text layer is preflighted before configuration and VM loading",
          "module('runtime/majiro_text_resources.lua').new(mode,patch_config,HOST,module)" in boot and
          "local raw_patch,text_patch=resources.raw,resources.text" in boot and
          boot.index("resources.preflight()") <
          boot.index("module('runtime/player_configuration.lua').make()"))
    check("no independent translation overlay or texture remains", "translation_overlay" not in native
          and "translation_texture" not in native and "HOST(translation);HOST(text_mode);" in native)
    report = {"schema": 1, "timestamp": timestamp(), "status": "passed", "checks": checks,
        "real_lookahead": real, "network_accessed": False, "device_accessed": False,
        "scope": "original text/proc/ctrl path, speaker identity, display-only Unicode widths, deterministic CFG lookahead",
        "full_gameplay_verified": False, "evidence": str(folder.relative_to(ROOT)),
        "remaining": ["Unproved branches/calls bound lookahead; batches may contain fewer than configured sentences",
                      "Production cold-cache proc waits up to 15 seconds, then displays the original on timeout without replaying events",
                      "On-device visual and full-story acceptance remain unverified"],
        "source_sha256": {name: sha256(ROOT / name) for name in (
            "player/runtime/majiro_json.lua", "player/runtime/majiro_translation.lua",
            "player/runtime/majiro_text.lua", "player/runtime/majiro_console.lua", "player/runtime/player_boot.lua",
            "player/android/game_host.cpp", "scripts/analysis/test_online_translation.py")}}
    write_json(ROOT / "reports/online_translation_tests.json", report)
    write_json(folder / "20261110_online_translation_tests.json", report)
    print("Online source console:", len(checks), "checks;", sum(g["tested_positions"] for g in real),
          "real Japanese positions,", sum(g["filled_20"] for g in real), "full twenty-line batches")
    for game in real:
        print(game["game"], "median", game["median"], "max", game["maximum"],
              "twenty", game["filled_20"], "/", game["tested_positions"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
