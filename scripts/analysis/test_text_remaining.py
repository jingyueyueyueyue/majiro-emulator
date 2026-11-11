"""Cross-build font-output state stacks and font preference query comparisons."""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers, source_identity
from lua_bridge import table_value
from original_opcode import native_number
from original_text_remaining import FontoutOracle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


FIXTURE = """
function(mod,ops,system)
  local script={name='fontout-state-fixture',main=0,functions={},
    code={{0,0x191},{8,0x191}}}
  local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
  vm:begin(script.name)
  local function stack()
    local result={}
    local f=vm.threads[vm.thread_id].fontout
    while f do
      result[#result+1]={face=f.face,height=f.height,width=f.width,step=f.step,
        style=f.style,color=f.color,shadow=f.shadow,page=f.page,x=f.x,y=f.y}
      f=f.previous
    end
    return result
  end
  return {vm=vm,stack=stack,call=function(hash,args,discard)
    vm:frame().index=1;script.code[1]={0,discard and 0x835 or 0x834,hash,#args}
    for i=#args,1,-1 do vm:push(args[i])end
    vm:step()
    local result={}
    for i,value in ipairs(vm.stack)do result[i]=value end
    while #vm.stack>0 do vm:pop()end
    return result
  end}
end
"""


def programs(page):
    yield [("$fontout_pop", ()), ("$fontout_pop", ())]
    yield [("$fontout_push", ()), ("$fontout_pop", ()), ("$fontout_pop", ())]
    yield [("$fontout_color", (0x123456, 0x789abc)), ("$fontout_push", ()),
           ("$fontout_color", (0x776655,)), ("$fontout_push", ()),
           ("$fontout_pop", ()), ("$fontout_pop", ()), ("$fontout_pop", ())]
    for color in (-2147483648, -1, 0, 1, 0xffffff, 2147483647):
        for shadow in (None, -2147483648, -1, 0, 0x123456, 2147483647):
            args = (color,) if shadow is None else (color, shadow)
            yield [("$fontout_color", args), ("$fontout_push", ()), ("$fontout_pop", ())]
    for face in ("", "MS Gothic", "\uff2d\uff33 \u30b4\u30b7\u30c3\u30af", "MS Mincho"):
        for height in (-2147483648, -5, -1, 0, 1, 19, 32, 71, 2147483647):
            for count in range(1, 6):
                values = (face, height, 9, 37, 3)[:count]
                yield [("$fontout_font", ("MS Gothic", 31, 13, 43, 2)),
                       ("$fontout_font", values), ("$fontout_push", ()),
                       ("$fontout_pop", ())]
    for x, y, selected in itertools.product((-2147483648, -1, 0, 1, 2147483647),
                                           (-19, 0, 27), (page, page + 0x100)):
        yield [("$fontout_locate", (selected, x, y)), ("$fontout_push", ()),
               ("$fontout_pop", ())]


def run():
    samples = json.loads((ROOT / "reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch = json.loads((ROOT / "reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name = {row["sample"]: row for row in dispatch}
    profiles = json.loads((ROOT / "config/player_engine_profiles.json").read_text(encoding="utf-8"))["executables"]
    before = source_identity()
    report = {"schema": 1, "timestamp": timestamp(), "source_identity": before,
        "samples": [], "failures": [], "complete_engine_semantics": False,
        "scope": "thread-owned font-output default-node push/pop, implicit node creation, font/color/valid-page location state, four registry-backed font queries, production guard for missing-voice logging",
        "host_boundaries": ["bounded guest allocation and free", "current front page",
                            "valid page metadata", "in-memory Win32 registry with REG_SZ values"],
        "limits": ["fontout glyph raster, orientation, proportional advances, antialias, shadow, outline and font cache output remain pending",
                   "load_font is not implemented: host font registration and resource search require an explicit provider",
                   "vertical text, kinsoku rules, history replay and callback execution remain pending",
                   "overlong font families and invalid page handles are excluded from these state comparisons"],
        "pending_features": ["fontout_raster_and_style", "external_font_resource_registration",
                             "vertical_layout", "kinsoku_and_indentation"],
        "local_checks": []}
    names = {"$is_font_setup_bold": ("fontbold", 0),
             "$is_font_setup_anti": ("fontanti", 1),
             "$is_font_setup_shadow": ("fontshadow", 1),
             "$is_font_setup_fuchidori": ("fuchidori", 1)}
    for sample in samples:
        oracle = FontoutOracle(sample, by_name[sample["sample"]]["entries"])
        profile = profiles[sample["exe_sha256"]]["text_controls"]
        counts, failures, arities = Counter(), Counter(), {}

        def fixture(overrides=None):
            options = {"text_controls": profile,
                       "integer_parse": profiles[sample["exe_sha256"]]["integer_parse"]}
            options.update(overrides or {})
            lua, _, _, system = registered_providers(overrides=options)
            system["state"]["frontbuffer"] = oracle.default_page
            for page in (oracle.default_page, oracle.default_page + 0x100):
                system["state"]["pages"][page] = lua.table_from({"width": 1280, "height": 720})
            module = lua.execute((ROOT / "player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
            ops = lua.execute((ROOT / "player/tables/opcodes.lua").read_text(encoding="utf-8"))
            return lua, system, lua.eval(FIXTURE)(module, ops, system)

        def check(lua, system, vm, name, args, discard, inspect_stack=False):
            counts[name] += 1
            arities.setdefault(name, set()).add(len(args))
            reason = None
            try:
                expected = oracle.call_fontout(name, args, discard)
                actual = table_value(vm["call"](int(oracle.entries[name]["hash"], 16),
                    lua.table_from(args), discard)) or []
                if actual != [native_number(tag, raw) for tag, raw in expected]:
                    reason = f"result stack differs: {actual} != {expected}"
                elif inspect_stack:
                    expected_state, actual_state = oracle.stack(), table_value(vm["stack"]()) or []
                    if actual_state != expected_state:
                        reason = f"font-output state differs: {actual_state} != {expected_state}"
            except Exception as error:
                reason = repr(error)
            if reason:
                failures[name] += 1
                if len(report["failures"]) < 80:
                    report["failures"].append({"sample": sample["sample"], "name": name,
                        "args": list(args), "discard": discard, "reason": reason})

        lua, system, vm = fixture()
        for discard in (False, True):
            for program in programs(oracle.default_page):
                oracle.reset()
                vm["vm"]["threads"][1]["fontout"] = None
                for name, args in program:
                    check(lua, system, vm, name, args, discard, True)
        for name, (key, default) in names.items():
            if name not in oracle.entries:
                continue
            for value, discard in itertools.product(
                    (None, "", "nonnumeric", " -19 suffix", "-1", "0", "1", "7",
                     "2147483647", "-2147483648", "2147483648", "-2147483649"), (False, True)):
                oracle.registry_values = {} if value is None else {key: value}
                system["state"]["registry"][key] = value
                check(lua, system, vm, name, (), discard)
        if "$is_log_no_voice" in oracle.entries:
            for mode, value, discard in itertools.product((-1, 0, 1, 2),
                    (-2147483648, -1, 0, 1, 7, 2147483647), (False, True)):
                oracle.write_u32(oracle.log_voice_slots["production"], mode)
                oracle.write_u32(oracle.log_voice_slots["value"], value)
                query_lua, query_system, query_vm = fixture({"testplay": mode != 1, "log_no_voice": value})
                check(query_lua, query_system, query_vm, "$is_log_no_voice", (), discard)
        calls = [{"name": name, "hash": oracle.entries[name]["hash"], "cases": count,
                  "failures": failures[name], "status": "passed" if not failures[name] else "failed",
                  "argument_counts": sorted(arities[name]), "discard_modes": [False, True]}
                 for name, count in sorted(counts.items())]
        report["samples"].append({"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
            "calls": calls, "native_fontout_template": oracle.template,
            "native_fontout_context_offset": oracle.context_offset,
            "native_fontout_node_size": oracle.node_size, "profile": profile})
        print(f"{sample['sample']}: {sum(counts.values())} text state comparisons; "
              f"{sum(failures.values())} mismatches", flush=True)
    # Independent integration checks protect state ownership and persistence.
    lua, system, vm = fixture({"text_controls": {}})
    node = vm["vm"]["threads"][1]
    system["providers"]["$fontout_color"](vm["vm"], lua.table_from([0x123456]))
    system["providers"]["$fontout_push"](vm["vm"], lua.table())
    codec = lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text(encoding="utf-8"))
    saved = codec["encode"](vm["vm"]["export_state"](vm["vm"]))
    cold_lua, cold_system, cold_vm = fixture({"text_controls": {}})
    cold_codec = cold_lua.execute((ROOT / "player/runtime/majiro_codec.lua").read_text(encoding="utf-8"))
    cold_vm["vm"]["import_state"](cold_vm["vm"], cold_codec["decode"](saved))
    cold_system["providers"]["$fontout_pop"](cold_vm["vm"], cold_lua.table())
    assert cold_vm["vm"]["threads"][1]["fontout"]["color"] == 0x123456
    report["local_checks"].append("VM checkpoint cold import preserves nested font-output links and pop restoration")
    system["providers"]["$fontout_pop"](vm["vm"], lua.table())
    assert node["fontout"]["color"] == 0x123456
    vm["vm"]["threads"][2] = lua.table_from({"id": 2, "frames": lua.table(), "stack": lua.table()})
    vm["vm"]["thread_id"] = 2
    system["providers"]["$fontout_pop"](vm["vm"], lua.table())
    system["providers"]["$fontout_color"](vm["vm"], lua.table_from([0x654321]))
    assert node["fontout"]["color"] == 0x123456
    report["local_checks"].append("empty pop and implicit allocation are isolated to the active thread")
    vm["vm"]["thread_id"] = 1
    system["providers"]["$fontout_font"](vm["vm"], lua.table_from(["MS Gothic", 32, 17, 41, 2]))
    system["providers"]["$fontout_font"](vm["vm"], lua.table_from(["MS Mincho", 20]))
    assert node["fontout"]["width"] == 17 and node["fontout"]["step"] == 41
    report["local_checks"].append("missing engine profile uses modern width and line-step retention")
    report["comparisons"] = sum(call["cases"] for sample in report["samples"] for call in sample["calls"])
    report["mismatches"] = sum(call["failures"] for sample in report["samples"] for call in sample["calls"])
    report["source_changed_during_test"] = source_identity() != before
    report["test_sources"] = {name: hashlib.sha256((ROOT / "scripts/analysis" / name).read_bytes()).hexdigest()
        for name in ("original_opcode.py", "original_strings.py", "original_calls.py",
                     "native_function.py", "original_text.py", "original_text_remaining.py", "test_text_remaining.py")}
    report["status"] = "passed" if not report["mismatches"] and not report["source_changed_during_test"] else "failed"
    folder = archive_dir("text_remaining")
    write_json(folder / (folder.name[:8] + "_text_remaining_tests.json"), report)
    write_json(ROOT / "reports/text_remaining_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
